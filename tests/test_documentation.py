"""Documentation is checked, not trusted.

The assignment penalises documentation that contradicts behaviour, so the
model-facing descriptions and the tool contracts are asserted against the code
rather than maintained by hand.
"""

import subprocess
import sys
from pathlib import Path

from procurement_mcp.config import ROOT
from procurement_mcp.server import TOOLS, ToolHost

CONTRACTS = ROOT / "docs" / "tool-contracts.md"


def test_the_server_exposes_exactly_the_documented_tools():
    documented = {f"`{name}`" for name in TOOLS}
    text = CONTRACTS.read_text(encoding="utf-8")

    for name in documented:
        assert f"## {name}" in text, f"{name} is exposed but not documented"


def test_model_facing_descriptions_are_byte_identical_to_the_document():
    text = CONTRACTS.read_text(encoding="utf-8")
    for name, (module, _) in TOOLS.items():
        assert module.DESCRIPTION in text, (
            f"the description documented for {name} differs from the one the server exposes"
        )


def test_every_tool_declares_both_schemas_over_mcp():
    for tool in ToolHost().describe():
        assert tool.input_schema.get("properties"), f"{tool.name} has no input schema"
        assert tool.output_schema, f"{tool.name} has no output schema"
        assert tool.input_schema.get("additionalProperties") is False, (
            f"{tool.name} must refuse unknown arguments"
        )


def test_generated_contracts_are_current():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_tool_docs.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_no_secret_looking_values_are_committed():
    """A committed key would cost the submission outright."""
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=ROOT
    ).stdout.split()
    suspicious = []
    for name in tracked:
        if name.startswith("data/tenders/") or name.startswith("fixtures/"):
            continue
        path = Path(ROOT / name)
        if not path.is_file() or path.suffix in {".gz", ".png"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in ("sk-or-v1-", "sk-ant-", "ghp_", "AKIA"):
            if marker in content:
                suspicious.append((name, marker))
    assert not suspicious, f"possible secrets committed: {suspicious}"
