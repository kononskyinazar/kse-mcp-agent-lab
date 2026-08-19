"""End-to-end through the real custom MCP server, over a real stdio session.

Only the Obsidian side and the model are stubbed - everything else is the
shipped server, spoken to over the protocol, exactly as the agent speaks to it
at the defence.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

from agent.graph import AgentDeps, build_graph
from agent.mcp_client import MCPConnection, ServerSpec

from test_agent_graph import StubHub, StubModel, obsidian_stub, procurement_stub  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

WATCHLIST = """---
buyers:
  - edrpou: "01999218"
    name: Лікарня
reviewed_tender_ids: []
---

No specific focus this cycle.
"""


@pytest.fixture
def live_server_spec():
    return ServerSpec(
        name="procurement",
        command=sys.executable,
        args=["-m", "procurement_mcp.server"],
        env={
            "PYTHONPATH": str(ROOT / "src"),
            "CUSTOM_MCP_OFFLINE": "true",
            "CUSTOM_MCP_DATA_DIR": str(ROOT / "data"),
            "CUSTOM_MCP_CONFIG_DIR": str(ROOT / "config"),
            "CUSTOM_MCP_FIXTURE_DIR": str(ROOT / "fixtures"),
        },
    )


@pytest.mark.skipif(not (ROOT / "data" / "manifest.json").exists(), reason="dataset not harvested")
async def test_the_agent_drives_the_real_server_over_mcp(live_server_spec):
    async with MCPConnection(live_server_spec) as procurement:
        assert set(procurement.tool_names()) == {
            "find_tenders",
            "compute_buyer_supplier_concentration",
            "check_procedure_threshold_compliance",
            "screen_tender_red_flags",
        }

        obsidian = obsidian_stub(screening_note=WATCHLIST)
        deps = AgentDeps(
            hub=StubHub({"obsidian": obsidian, "procurement": procurement}),
            model=StubModel(plan={"entries": [{"buyer_edrpou": "01999218",
                                               "rationale": "no specific focus stated",
                                               "limit": 5}]}),
            today=date(2026, 8, 19),
            require_approval=False,
        )
        from langgraph.checkpoint.memory import InMemorySaver

        compiled = build_graph(deps).compile(checkpointer=InMemorySaver())
        state = await compiled.ainvoke({}, config={"configurable": {"thread_id": "integration"}})

    assert state["candidates"], "the real server returned tenders"
    assert len(state["screened"]) == len(state["candidates"])
    assert state["buyer_context"]["01999218"]["result_count"] > 0
    assert obsidian.writes, "a findings note was produced"

    for record in state["screened"]:
        screening = record["screening"]
        assert "evidence_chain" in screening
        assert screening["provenance"]["mode"] == "offline-replay"
        assert screening["data_window"]["tenders_in_dataset"] == 533


@pytest.mark.skipif(not (ROOT / "data" / "manifest.json").exists(), reason="dataset not harvested")
async def test_a_tool_failure_crosses_the_protocol_with_its_code(live_server_spec):
    async with MCPConnection(live_server_spec) as procurement:
        from agent.mcp_client import MCPToolFailure

        with pytest.raises(MCPToolFailure) as excinfo:
            await procurement.call("compute_buyer_supplier_concentration", {"buyer_edrpou": "nope"})

        assert excinfo.value.code == "INVALID_INPUT"

        empty = await procurement.call(
            "compute_buyer_supplier_concentration", {"buyer_edrpou": "99999999"}
        )
        assert empty["result_count"] == 0, "an unknown but valid code is an empty success"


async def test_an_unstartable_server_is_reported_as_a_connection_failure():
    from agent.mcp_client import MCPConnectionError

    spec = ServerSpec(name="ghost", command="definitely-not-a-real-command-xyz", args=[])
    with pytest.raises(MCPConnectionError) as excinfo:
        async with MCPConnection(spec):
            pass

    assert "not found on PATH" in str(excinfo.value)
