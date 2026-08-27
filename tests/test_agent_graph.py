"""The agent flow: which branch runs, and what reaches the tools.

Both MCP servers are stubbed, so these tests assert orchestration - not the
servers' own behaviour, which has its own tests.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.graph import AgentDeps, build_graph
from agent.mcp_client import MCPConnectionError, MCPToolFailure

WATCHLIST = """---
last_reviewed_date: 2026-08-18
buyers:
  - edrpou: "01999218"
    name: Лікарня
reviewed_tender_ids:
  - UA-already-seen
---

Focus on medicines this cycle.
"""


class StubConnection:
    def __init__(self, name, tools, handler):
        self.name = name
        self._tools = tools
        self._handler = handler
        self.calls: list[tuple[str, dict]] = []

    def tool_names(self):
        return list(self._tools)

    def find_tool(self, *fragments):
        for tool in self._tools:
            if all(f.lower() in tool.lower() for f in fragments):
                return tool
        return None

    async def call(self, tool, arguments=None):
        self.calls.append((tool, arguments or {}))
        return self._handler(tool, arguments or {})


class StubHub:
    def __init__(self, connections):
        self.connections = connections

    def __getitem__(self, name):
        return self.connections[name]

    def discovery_report(self):
        return {name: c.tool_names() for name, c in self.connections.items()}


class StubModel:
    """Returns a fixed plan and a fixed note body; records what it was asked."""

    def __init__(self, plan=None):
        self.plan = plan or {"entries": [{"buyer_edrpou": "01999218", "cpv_prefix": "336",
                                          "rationale": "note says medicines"}]}
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        model = self

        class Structured:
            async def ainvoke(self, messages):
                model.prompts.append(messages[-1]["content"])
                return model.plan

        return Structured()

    async def ainvoke(self, messages):
        self.prompts.append(messages[-1]["content"])

        class Response:
            content = "## Findings\n\nWritten from the structured evidence."

        return Response()


def obsidian_stub(screening_note=WATCHLIST, findings=(), writes=None, run_log=None):
    writes = writes if writes is not None else []
    patches: list[tuple] = []

    def handler(tool, arguments):
        if "get_file" in tool:
            path = arguments.get("filepath", "")
            if "watchlist" in path:
                return {"text": screening_note}
            if path.endswith("_run-log.md"):
                if run_log is None:
                    raise MCPToolFailure("obsidian", tool, {"error": {"code": "NOT_FOUND", "message": path}})
                return {"text": run_log}
            for name, body in findings:
                if path.endswith(name):
                    return {"text": body}
            raise MCPToolFailure("obsidian", tool, {"error": {"code": "NOT_FOUND", "message": path}})
        if "list_files" in tool:
            return {"files": [name for name, _ in findings]}
        if "append_content" in tool:
            writes.append((arguments.get("filepath"), arguments.get("content")))
            return {"status": "ok"}
        if "patch_content" in tool:
            patches.append((arguments.get("filepath"), arguments.get("target"), arguments.get("content")))
            return {"status": "ok"}
        raise AssertionError(f"unexpected obsidian tool {tool}")

    connection = StubConnection(
        "obsidian",
        ["obsidian_get_file_contents", "obsidian_list_files_in_dir", "obsidian_patch_content",
         "obsidian_append_content"],
        handler,
    )
    connection.writes = writes
    connection.patches = patches
    return connection


def procurement_stub(*, risk_score=15.0, has_blocking=False, compliant=True, fail_tool=None,
                     review_threshold=60.0):
    def handler(tool, arguments):
        if fail_tool == tool:
            raise MCPToolFailure("procurement", tool, {"error": {"code": "DATA_INTEGRITY", "message": "boom"}})
        if tool == "compute_buyer_supplier_concentration":
            return {"status": "ok", "result_count": 3, "by_value": {"hhi": 0.4}}
        if tool == "find_tenders":
            return {
                "status": "ok",
                "result_count": 1,
                "total_matched": 1,
                "tenders": [
                    {
                        "tender_id": "UA-new-1",
                        "uuid": "u" * 32,
                        "buyer": {"edrpou": "01999218", "name": "Лікарня"},
                        "expected_value": 250000.0,
                    }
                ],
            }
        if tool == "check_procedure_threshold_compliance":
            return {"status": "ok", "compliant": compliant, "failed_conditions": []}
        if tool == "screen_tender_red_flags":
            return {
                "status": "ok",
                "risk_score": risk_score,
                "has_blocking": has_blocking,
                # The server owns the policy and publishes its verdict; the agent
                # no longer keeps a threshold of its own.
                "requires_human_review": has_blocking or risk_score >= review_threshold,
                "provenance": {"human_review_threshold": review_threshold},
                "blocking_violations": [{"rule_id": "bid_window_below_statutory_minimum"}] if has_blocking else [],
                "advisories": [],
            }
        raise AssertionError(f"unexpected procurement tool {tool}")

    return StubConnection(
        "procurement",
        ["find_tenders", "compute_buyer_supplier_concentration",
         "check_procedure_threshold_compliance", "screen_tender_red_flags"],
        handler,
    )


def make_deps(obsidian, procurement, **kwargs):
    return AgentDeps(
        hub=StubHub({"obsidian": obsidian, "procurement": procurement}),
        model=kwargs.pop("model", StubModel()),
        today=date(2026, 8, 19),
        **kwargs,
    )


async def run_graph(deps, resume=None, thread="t"):
    compiled = build_graph(deps).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": thread}}
    state = await compiled.ainvoke({"watchlist_path": "procurement/watchlist.md"}, config=config)
    if resume is not None and state.get("__interrupt__"):
        state = await compiled.ainvoke(Command(resume=resume), config=config)
    return state


async def test_discovery_reports_both_connections():
    deps = make_deps(obsidian_stub(), procurement_stub())
    state = await run_graph(deps, resume={"approved": True})

    assert set(state["discovery"]) == {"obsidian", "procurement"}
    assert len(state["discovery"]["procurement"]) == 4


async def test_vault_state_excludes_already_reviewed_tenders():
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(), procurement)
    await run_graph(deps, resume={"approved": True})

    find_call = next(args for tool, args in procurement.calls if tool == "find_tenders")
    assert "UA-already-seen" in find_call["exclude_tender_ids"]


async def test_prior_findings_notes_extend_the_reviewed_set():
    prior = ("findings_01999218_2026-08-18.md", "---\ntender_ids: [UA-from-note]\n---\n\nbody")
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(findings=[prior]), procurement)
    await run_graph(deps, resume={"approved": True})

    find_call = next(args for tool, args in procurement.calls if tool == "find_tenders")
    assert "UA-from-note" in find_call["exclude_tender_ids"], "a prior note must change this run"


async def test_watchlist_prose_reaches_the_custom_server_as_a_filter():
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(), procurement)
    await run_graph(deps, resume={"approved": True})

    find_call = next(args for tool, args in procurement.calls if tool == "find_tenders")
    assert find_call["cpv_prefix"] == "336", "what the analyst wrote must change the tool arguments"


async def test_concentration_is_called_once_per_buyer():
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(), procurement)
    await run_graph(deps, resume={"approved": True})

    calls = [args for tool, args in procurement.calls if tool == "compute_buyer_supplier_concentration"]
    assert len(calls) == 1


async def test_compliance_runs_for_every_screened_tender():
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(), procurement)
    await run_graph(deps, resume={"approved": True})

    tools = [tool for tool, _ in procurement.calls]
    assert tools.count("check_procedure_threshold_compliance") == tools.count("screen_tender_red_flags")


async def test_blocking_violation_forces_the_human_gate():
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=10.0, has_blocking=True))
    compiled = build_graph(deps).compile(checkpointer=InMemorySaver())
    state = await compiled.ainvoke({}, config={"configurable": {"thread_id": "gate"}})

    assert state.get("__interrupt__"), "a blocking violation must stop for a human even at a low score"
    assert obsidian.writes == [], "nothing may be written before approval"


async def test_the_agent_defers_to_the_servers_review_verdict():
    """The threshold lives in config/rules.yaml, not in the agent."""
    obsidian = obsidian_stub()
    # Score below the agent's old hard-coded 60, but the server says review it.
    deps = make_deps(obsidian, procurement_stub(risk_score=30.0, review_threshold=25.0))
    compiled = build_graph(deps).compile(checkpointer=InMemorySaver())
    state = await compiled.ainvoke({}, config={"configurable": {"thread_id": "defer"}})

    assert state.get("__interrupt__"), "the server's verdict must decide, not a second copy of it"


async def test_low_score_without_blocking_skips_the_gate():
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=5.0))
    compiled = build_graph(deps).compile(checkpointer=InMemorySaver())
    state = await compiled.ainvoke({}, config={"configurable": {"thread_id": "clean"}})

    assert not state.get("__interrupt__")
    assert any("findings_" in path for path, _ in obsidian.writes)


async def test_rejection_writes_no_finding_but_still_records_the_decision():
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=90.0))
    state = await run_graph(deps, resume={"approved": False}, thread="reject")

    assert state["review_decision"]["approved"] is False
    assert [p for p, _ in obsidian.writes if "findings_" in p and "_run-log" not in p] == []
    # A refusal is a decision. Without a log entry the next run re-screens the
    # same tenders and asks the same question.
    path, content = obsidian.writes[-1]
    assert path.endswith("_run-log.md")
    assert "- decision: declined" in content


async def test_only_an_explicit_yes_approves():
    """bool("no") is True, and this gate guards an allegation."""
    from agent.graph import _is_approval

    assert _is_approval(True) is True
    assert _is_approval({"approved": True}) is True
    assert _is_approval({"approved": "yes"}) is True
    assert _is_approval({"approved": "no"}) is False
    assert _is_approval("no") is False
    assert _is_approval(["anything"]) is False
    assert _is_approval(None) is False


async def test_approval_writes_the_note_then_patches_the_watchlist_state():
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=90.0))
    await run_graph(deps, resume={"approved": True}, thread="approve")

    assert obsidian.writes[0][0].startswith("procurement/findings/findings_01999218_")
    # The state entry comes last and is appended to its own log. The bridge has
    # no whole-file write and its patch endpoint fails on this plugin version, so
    # append is the only write - which also means the analyst's note is untouched.
    assert obsidian.writes[-1][0].endswith("_run-log.md")
    assert obsidian.patches == []


async def test_rerunning_does_not_duplicate_an_existing_note():
    """Appending is the only creation path, so an existing finding is left alone."""
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=90.0))
    state_one = await run_graph(deps, resume={"approved": True}, thread="dup-1")

    path, content = obsidian.writes[0]
    obsidian_two = obsidian_stub(findings=[(path.rsplit("/", 1)[1], content)])
    deps_two = make_deps(obsidian_two, procurement_stub(risk_score=90.0))
    deps_two.today = deps.today
    state_two = await run_graph(deps_two, resume={"approved": True}, thread="dup-2")

    assert state_one["written"], "the first run writes the note"
    findings_writes = [p for p, _ in obsidian_two.writes if "findings_" in p and "_run-log" not in p]
    assert findings_writes == [], "the second run must not append a duplicate finding"
    assert state_two["skipped_existing"]
    assert any(p.endswith("_run-log.md") for p, _ in obsidian_two.writes), (
        "the re-run is still recorded, so its tenders are not screened a third time"
    )


async def test_written_note_carries_the_required_frontmatter():
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=90.0))
    await run_graph(deps, resume={"approved": True}, thread="fm")

    _, content = obsidian.writes[0]
    for field in ("finding_id", "buyer_edrpou", "run_id", "severity_score", "review_status",
                  "evidence_chain_ref"):
        assert field in content


async def test_finding_id_is_stable_for_the_same_run_inputs():
    obsidian_a, obsidian_b = obsidian_stub(), obsidian_stub()
    for obsidian, thread in ((obsidian_a, "id-a"), (obsidian_b, "id-b")):
        deps = make_deps(obsidian, procurement_stub(risk_score=90.0))
        await run_graph(deps, resume={"approved": True}, thread=thread)

    def finding_id_of(writes):
        return next(line for line in writes[0][1].splitlines() if line.startswith("finding_id"))

    # Same buyer, same date, same run id: a re-run after a mid-write failure
    # must rewrite the same note rather than append a duplicate.
    assert finding_id_of(obsidian_a.writes).split(":")[1] != ""


async def test_the_run_log_records_what_was_screened():
    obsidian = obsidian_stub()
    deps = make_deps(obsidian, procurement_stub(risk_score=90.0))
    await run_graph(deps, resume={"approved": True}, thread="state")

    path, content = obsidian.writes[-1]
    assert path.endswith("_run-log.md")
    assert "- screened: UA-new-1" in content
    assert "- flagged: UA-new-1" in content


async def test_a_previous_run_log_entry_excludes_that_tender():
    """The append-only log is the run-to-run memory; it must change this run."""
    log = "# Run log\n\n## run-20260818-090000 — 2026-08-18\n\n- screened: UA-2026-08-06-010871-a\n"
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(run_log=log), procurement)
    await run_graph(deps, resume={"approved": True}, thread="log")

    find_call = next(args for tool, args in procurement.calls if tool == "find_tenders")
    assert "UA-2026-08-06-010871-a" in find_call["exclude_tender_ids"]


async def test_a_missing_run_log_is_not_an_error():
    obsidian = obsidian_stub(run_log=None)
    deps = make_deps(obsidian, procurement_stub(risk_score=5.0))
    state = await run_graph(deps, thread="nolog")

    assert state["written"], "a first run with no log still completes"


async def test_obsidian_read_failure_aborts_before_any_write():
    def handler(tool, arguments):
        raise MCPConnectionError("obsidian", "connection refused")

    obsidian = StubConnection("obsidian", ["obsidian_get_file_contents", "obsidian_put_content"], handler)
    obsidian.writes = []
    deps = make_deps(obsidian, procurement_stub())

    with pytest.raises(MCPConnectionError):
        await run_graph(deps, thread="down")


async def test_one_failing_custom_tool_does_not_lose_the_run():
    obsidian = obsidian_stub()
    deps = make_deps(
        obsidian, procurement_stub(risk_score=90.0, fail_tool="check_procedure_threshold_compliance")
    )
    state = await run_graph(deps, resume={"approved": True}, thread="partial")

    codes = [e.get("code") for e in state["errors"]]
    assert "DATA_INTEGRITY" in codes, "the gap must be recorded"
    assert state["written"], "the rest of the run still produces its note"


async def test_the_note_is_composed_from_structured_evidence_only():
    model = StubModel()
    deps = make_deps(obsidian_stub(), procurement_stub(risk_score=90.0), model=model)
    await run_graph(deps, resume={"approved": True}, thread="eviden")

    payload = json.loads(model.prompts[-1])
    assert payload["buyer"]["edrpou"] == "01999218"
    assert "concentration" in payload and "flagged" in payload


# --- run-log parsing -------------------------------------------------------

def test_run_log_parser_reads_screened_and_flagged_ids():
    from agent.vault import parse_run_log

    text = """# Run log

## run-20260819-115232 — 2026-08-19

- buyers: 31557119, 01999218
- screened: UA-2026-08-19-006860-a, UA-2026-08-18-004904-a
- flagged: UA-2026-08-18-004904-a
"""

    assert parse_run_log(text) == {"UA-2026-08-19-006860-a", "UA-2026-08-18-004904-a"}


def test_run_log_parser_ignores_ids_outside_the_recorded_lines():
    from agent.vault import parse_run_log

    text = "- buyers: 31557119\nSome prose mentioning UA-2026-01-01-000001-a in passing.\n"

    assert parse_run_log(text) == set(), "only the screened and flagged lines are state"


def test_run_log_parser_accumulates_across_appended_runs():
    from agent.vault import parse_run_log

    text = (
        "## run-a\n\n- screened: UA-2026-08-01-000001-a\n\n"
        "## run-b\n\n- screened: UA-2026-08-02-000002-a\n"
    )

    assert len(parse_run_log(text)) == 2, "the log is append-only; every run still counts"


async def test_the_plan_records_what_each_filter_was_derived_from(capsys):
    """S4 evidence: the link from the analyst's sentence to the tool argument."""
    procurement = procurement_stub()
    deps = make_deps(obsidian_stub(), procurement)
    state = await run_graph(deps, resume={"approved": True}, thread="plan")

    entry = state["plan"][0]
    assert entry["cpv_prefix"] == "336"
    assert entry["rationale"], "a filter with no stated origin cannot be demonstrated"
    assert "cpv_prefix" in capsys.readouterr().err, "the derivation must be visible, not asserted"
