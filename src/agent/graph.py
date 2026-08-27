"""The agent flow, as a LangGraph graph.

The branch points the assignment cares about are edges here, not hints in a
prompt:

* tenders already recorded in the vault are excluded before any screening;
* what the analyst wrote in the vault decides the filters passed to the custom
  server;
* a blocking violation, or a score above the configured threshold, routes the
  run through a human approval interrupt before anything is written back.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .mcp_client import MCPConnectionError, MCPHub, MCPToolFailure
from .prompts import NOTE_SYSTEM, PLAN_SYSTEM
from .vault import ObsidianVault, finding_id, findings_path, parse_frontmatter

DEFAULT_LIMIT = 10
MAX_LIMIT = 25


def _merge(left: list, right: list) -> list:
    return [*left, *right]


class RunState(TypedDict, total=False):
    run_id: str
    started_at: str
    watchlist_path: str
    watchlist_prose: str
    buyers: list[dict[str, Any]]
    reviewed_tender_ids: list[str]
    plan: list[dict[str, Any]]
    buyer_context: dict[str, Any]
    candidates: list[dict[str, Any]]
    screened: list[dict[str, Any]]
    flagged: list[dict[str, Any]]
    review_decision: dict[str, Any]
    notes: list[dict[str, Any]]
    written: list[str]
    skipped_existing: list[str]
    errors: Annotated[list[dict[str, Any]], _merge]
    discovery: dict[str, list[str]]


@dataclass
class AgentDeps:
    """Everything the graph talks to. Injected so tests never touch the network."""

    hub: MCPHub
    model: Any
    obsidian_server: str = "obsidian"
    procurement_server: str = "procurement"
    require_approval: bool = True
    today: date = field(default_factory=lambda: datetime.now(UTC).date())

    _vault: ObsidianVault | None = field(default=None, repr=False, compare=False)

    @property
    def vault(self) -> ObsidianVault:
        if self._vault is None:
            object.__setattr__(self, "_vault", ObsidianVault(self.hub[self.obsidian_server]))
        return self._vault

    @property
    def procurement(self):
        return self.hub[self.procurement_server]


def _is_approval(decision: Any) -> bool:
    """Only an explicit yes approves.

    Anything else - a bare string, a list, {"approved": "no"} - is a refusal.
    bool("no") is True, and this gate exists to stop an allegation reaching the
    vault without assent.
    """
    if isinstance(decision, dict):
        decision = decision.get("approved")
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, str):
        return decision.strip().casefold() in {"y", "yes", "true", "approve", "approved"}
    return False


def _error(stage: str, exc: Exception, buyer_edrpou: str | None = None) -> dict[str, Any]:
    """Errors carry the buyer they belong to, so notes cannot inherit each other's."""
    record: dict[str, Any] = {"stage": stage, "buyer_edrpou": buyer_edrpou}
    if isinstance(exc, MCPToolFailure):
        record.update({"kind": "tool_failure", "server": exc.server, "tool": exc.tool,
                       "code": exc.code, "message": exc.detail})
    elif isinstance(exc, MCPConnectionError):
        record.update({"kind": "connection", "server": exc.server, "message": exc.message})
    else:
        record.update({"kind": exc.__class__.__name__, "message": str(exc)})
    return record


def build_graph(deps: AgentDeps):
    async def discover(state: RunState) -> RunState:
        return {
            "run_id": state.get("run_id") or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S"),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "discovery": deps.hub.discovery_report(),
            "errors": [],
        }

    async def read_vault(state: RunState) -> RunState:
        """Read the watchlist and the prior findings from the existing server.

        A failure here aborts before any write, which is why it is its own node.
        """
        vault = deps.vault
        watchlist = await vault.read_watchlist(state.get("watchlist_path") or "procurement/watchlist.md")
        prior = await vault.read_prior_findings()
        logged = await vault.read_run_log()

        reviewed = list(watchlist.reviewed_tender_ids)
        reviewed.extend(str(f.get("tender_id")) for f in prior if f.get("tender_id"))
        for finding in prior:
            reviewed.extend(str(t) for t in (finding.get("tender_ids") or []))
        reviewed.extend(logged)
        _, prose = parse_frontmatter(watchlist.raw)

        return {
            "buyers": watchlist.buyers,
            "watchlist_prose": prose.strip(),
            # The vault's memory of previous runs is what stops the agent
            # re-judging the same tender: a real dependency, not a decoration.
            "reviewed_tender_ids": sorted({r for r in reviewed if r}),
        }

    async def plan(state: RunState) -> RunState:
        """Turn what the analyst wrote into the filters the custom server gets."""
        buyers = state.get("buyers") or []
        prose = state.get("watchlist_prose") or ""
        schema = {
            "title": "ScreeningPlan",
            "description": "Per-buyer screening filters derived from the watchlist note.",
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "buyer_edrpou": {"type": "string"},
                            "cpv_prefix": {"type": "string"},
                            "procedure_types": {"type": "array", "items": {"type": "string"}},
                            "min_value": {"type": "number"},
                            "limit": {"type": "integer"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["buyer_edrpou", "rationale"],
                    },
                }
            },
            "required": ["entries"],
        }
        payload = json.dumps({"buyers": buyers, "analyst_notes": prose}, ensure_ascii=False)
        response = await deps.model.with_structured_output(schema).ainvoke(
            [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": payload}]
        )
        entries = {e["buyer_edrpou"]: e for e in (response or {}).get("entries", [])}

        resolved = []
        for buyer in buyers:
            edrpou = str(buyer.get("edrpou"))
            entry = entries.get(edrpou, {"buyer_edrpou": edrpou, "rationale": "no specific focus stated"})
            entry["buyer_name"] = buyer.get("name")
            entry["limit"] = min(int(entry.get("limit") or DEFAULT_LIMIT), MAX_LIMIT)
            resolved.append(entry)

        # Printed because it is the observable link between what the analyst
        # wrote in the vault and the arguments the custom server receives. A
        # demonstration that this connection exists cannot rest on the operator
        # asserting it.
        for entry in resolved:
            filters = {
                key: entry[key]
                for key in ("cpv_prefix", "min_value", "procedure_types", "limit")
                if entry.get(key)
            }
            print(
                f"  plan {entry['buyer_edrpou']}: {filters} <- {entry.get('rationale')}",
                file=sys.stderr,
            )
        return {"plan": resolved}

    async def buyer_context(state: RunState) -> RunState:
        """Concentration once per buyer: a buyer-level metric, computed once."""
        context: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for entry in state.get("plan") or []:
            edrpou = entry["buyer_edrpou"]
            try:
                context[edrpou] = await deps.procurement.call(
                    "compute_buyer_supplier_concentration", {"buyer_edrpou": edrpou}
                )
            except (MCPToolFailure, MCPConnectionError) as exc:
                errors.append(_error(f"buyer_context[{edrpou}]", exc, edrpou))
        return {"buyer_context": context, "errors": errors}

    async def select(state: RunState) -> RunState:
        reviewed = state.get("reviewed_tender_ids") or []
        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for entry in state.get("plan") or []:
            arguments: dict[str, Any] = {
                "buyer_edrpou": entry["buyer_edrpou"],
                "limit": entry.get("limit", DEFAULT_LIMIT),
                "exclude_tender_ids": reviewed[:500],
            }
            for key in ("cpv_prefix", "min_value", "procedure_types"):
                if entry.get(key):
                    arguments[key] = entry[key]
            try:
                result = await deps.procurement.call("find_tenders", arguments)
            except (MCPToolFailure, MCPConnectionError) as exc:
                errors.append(_error(f"select[{entry['buyer_edrpou']}]", exc, entry["buyer_edrpou"]))
                continue
            for tender in result.get("tenders", []):
                candidates.append({**tender, "plan_rationale": entry.get("rationale"), "filters": arguments})
        return {"candidates": candidates, "errors": errors}

    async def screen(state: RunState) -> RunState:
        screened: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for candidate in state.get("candidates") or []:
            identifier = candidate.get("tender_id") or candidate.get("uuid")
            buyer_edrpou = (candidate.get("buyer") or {}).get("edrpou")
            record: dict[str, Any] = {"tender": candidate, "identifier": identifier}
            # Threshold compliance runs for every tender, not only for ones that
            # tripped something else: a threshold breach is itself a top signal
            # and must not be reachable only through another rule firing first.
            for tool, key in (
                ("check_procedure_threshold_compliance", "compliance"),
                ("screen_tender_red_flags", "screening"),
            ):
                try:
                    record[key] = await deps.procurement.call(tool, {"tender_identifier": identifier})
                except (MCPToolFailure, MCPConnectionError) as exc:
                    record[f"{key}_error"] = _error(f"{tool}[{identifier}]", exc, buyer_edrpou)
                    errors.append(record[f"{key}_error"])
            screened.append(record)

        # The threshold belongs to the server, which publishes its verdict as
        # requires_human_review. Keeping a second copy here meant the two could
        # disagree about when a person must see an allegation.
        #
        # `compliant is not True` deliberately catches null as well as false: a
        # compliance check that could not be performed is a reason to look, not
        # a reason to pass.
        flagged = [
            r
            for r in screened
            if (r.get("screening") or {}).get("has_blocking")
            or (r.get("screening") or {}).get("requires_human_review")
            or (r.get("compliance") or {}).get("compliant") is not True
        ]
        return {"screened": screened, "flagged": flagged, "errors": errors}

    async def human_review(state: RunState) -> RunState:
        """Stop and ask. The output can become an allegation about named people."""
        summary = [
            {
                "tender_id": r["identifier"],
                "risk_score": (r.get("screening") or {}).get("risk_score"),
                "blocking": [b["rule_id"] for b in (r.get("screening") or {}).get("blocking_violations", [])],
            }
            for r in state.get("flagged") or []
        ]
        decision = interrupt(
            {
                "question": "Approve writing these findings to the vault?",
                "flagged": summary,
                "run_id": state.get("run_id"),
            }
        )
        return {"review_decision": {"approved": _is_approval(decision), "raw": decision}}

    async def record_refusal(state: RunState) -> RunState:
        """A refusal is a decision, and it belongs in the log.

        Without this the run ends silently: the next run re-screens the same
        tenders and asks the same question, with nothing in the vault to say a
        person already answered it.
        """
        errors: list[dict[str, Any]] = []
        try:
            await _append_run_log(deps, state, decision="declined")
        except (MCPToolFailure, MCPConnectionError) as exc:
            errors.append(_error("append_run_log", exc))
        return {"written": [], "errors": errors}

    async def compose(state: RunState) -> RunState:
        notes: list[dict[str, Any]] = []
        by_buyer: dict[str, list[dict[str, Any]]] = {}
        for record in state.get("flagged") or []:
            edrpou = ((record.get("tender") or {}).get("buyer") or {}).get("edrpou") or "unknown"
            by_buyer.setdefault(edrpou, []).append(record)

        for entry in state.get("plan") or []:
            edrpou = entry["buyer_edrpou"]
            records = by_buyer.get(edrpou, [])
            evidence = {
                "buyer": {"edrpou": edrpou, "name": entry.get("buyer_name")},
                "plan_rationale": entry.get("rationale"),
                "concentration": (state.get("buyer_context") or {}).get(edrpou),
                "flagged": records,
                "screened_count": sum(
                    1
                    for r in state.get("screened") or []
                    if ((r.get("tender") or {}).get("buyer") or {}).get("edrpou") == edrpou
                ),
                "errors": [e for e in state.get("errors") or [] if e.get("buyer_edrpou") == edrpou],
            }
            response = await deps.model.ainvoke(
                [
                    {"role": "system", "content": NOTE_SYSTEM},
                    {"role": "user", "content": json.dumps(evidence, ensure_ascii=False, default=str)},
                ]
            )
            body = getattr(response, "content", str(response))
            path = findings_path(edrpou, deps.today)
            # Derived from what was found, not from the run id: two runs on the
            # same day that reach the same conclusion must not produce two
            # entries, while a run that finds something new must.
            tender_ids = sorted(r["identifier"] for r in records)
            identity = finding_id(
                "|".join(
                    [
                        edrpou,
                        deps.today.isoformat(),
                        ",".join(tender_ids),
                        str(max([(r.get("screening") or {}).get("risk_score", 0) for r in records] or [0])),
                    ]
                )
            )
            frontmatter = {
                "finding_id": identity,
                "buyer_edrpou": edrpou,
                "run_id": state.get("run_id"),
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
                "severity_score": max(
                    [(r.get("screening") or {}).get("risk_score", 0) for r in records] or [0]
                ),
                "review_status": "approved" if (state.get("review_decision") or {}).get("approved") else "auto",
                "tender_ids": tender_ids,
                "evidence_chain_ref": f"{path}#evidence",
            }
            notes.append({"path": path, "frontmatter": frontmatter, "body": body})
        return {"notes": notes}

    async def write_back(state: RunState) -> RunState:
        """Notes first, watchlist state last, each note idempotent by finding_id."""
        vault = deps.vault
        written: list[str] = []
        errors: list[dict[str, Any]] = []

        skipped: list[str] = []
        for note in state.get("notes") or []:
            try:
                outcome = await vault.write_finding(
                    note["path"],
                    frontmatter=_render_frontmatter(note["frontmatter"]),
                    body=note["body"],
                    marker=note["frontmatter"]["finding_id"],
                    heading=f"Revision {state.get('run_id')}",
                )
                (written if outcome in {"created", "revised"} else skipped).append(note["path"])
            except (MCPToolFailure, MCPConnectionError) as exc:
                errors.append(_error(f"write[{note['path']}]", exc))

        # State update goes last: if it fails, the notes still exist and their
        # tenders are simply re-screened next run rather than lost.
        try:
            await _append_run_log(deps, state)
        except (MCPToolFailure, MCPConnectionError) as exc:
            errors.append(_error("append_run_log", exc))

        return {"written": written, "skipped_existing": skipped, "errors": errors}

    def route_after_screening(state: RunState) -> str:
        if not state.get("flagged"):
            return "compose"
        return "human_review" if deps.require_approval else "compose"

    def route_after_review(state: RunState) -> str:
        return "compose" if (state.get("review_decision") or {}).get("approved") else "record_refusal"

    graph = StateGraph(RunState)
    graph.add_node("discover", discover)
    graph.add_node("read_vault", read_vault)
    graph.add_node("plan", plan)
    graph.add_node("buyer_context", buyer_context)
    graph.add_node("select", select)
    graph.add_node("screen", screen)
    graph.add_node("human_review", human_review)
    graph.add_node("record_refusal", record_refusal)
    graph.add_node("compose", compose)
    graph.add_node("write_back", write_back)

    graph.add_edge(START, "discover")
    graph.add_edge("discover", "read_vault")
    graph.add_edge("read_vault", "plan")
    graph.add_edge("plan", "buyer_context")
    graph.add_edge("buyer_context", "select")
    graph.add_edge("select", "screen")
    graph.add_conditional_edges("screen", route_after_screening, ["human_review", "compose"])
    graph.add_conditional_edges("human_review", route_after_review, ["compose", "record_refusal"])
    graph.add_edge("record_refusal", END)
    graph.add_edge("compose", "write_back")
    graph.add_edge("write_back", END)
    return graph


def _render_frontmatter(data: dict[str, Any]) -> str:
    import yaml

    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + "---\n"


async def _append_run_log(deps: AgentDeps, state: RunState, *, decision: str = "written") -> None:
    """Record what this run judged so the next run skips it."""
    screened = [r["identifier"] for r in state.get("screened") or [] if r.get("identifier")]
    flagged = [r["identifier"] for r in state.get("flagged") or [] if r.get("identifier")]
    buyers = [entry["buyer_edrpou"] for entry in state.get("plan") or []]

    await deps.vault.append_run_log(
        str(state.get("run_id")),
        deps.today.isoformat(),
        buyers=buyers,
        screened=screened,
        flagged=flagged,
        decision=decision,
    )
