#!/usr/bin/env python3
"""Regenerate the custom-tool contracts in docs/tool-contracts.md.

The model-facing description, the schemas and the worked example are read from
the code and produced by running the tool against the committed dataset, so the
documentation cannot drift from behaviour. A test asserts the file is current.

    python scripts/generate_tool_docs.py          # rewrite the file
    python scripts/generate_tool_docs.py --check  # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procurement_mcp.config import Configuration  # noqa: E402
from procurement_mcp.store import DatasetStore  # noqa: E402
from procurement_mcp.tools import compliance, concentration, find, screen  # noqa: E402

BEGIN = "<!-- BEGIN GENERATED TOOL CONTRACTS -->"
END = "<!-- END GENERATED TOOL CONTRACTS -->"
DOC = ROOT / "docs" / "tool-contracts.md"

ORDER = [
    ("find_tenders", find),
    ("compute_buyer_supplier_concentration", concentration),
    ("check_procedure_threshold_compliance", compliance),
    ("screen_tender_red_flags", screen),
]

MAX_EXAMPLE_CHARS = 2600


def schema_rows(schema: dict) -> str:
    required = set(schema.get("required") or [])
    lines = ["| Field | Type | Required | Constraints | Default | Meaning |", "|---|---|---|---|---|---|"]
    for name, spec in (schema.get("properties") or {}).items():
        constraints = []
        for key in ("minLength", "maxLength", "minimum", "maximum", "maxItems"):
            if key in spec:
                constraints.append(f"{key} {spec[key]}")
        if "enum" in spec:
            constraints.append("enum: " + ", ".join(f"`{v}`" for v in spec["enum"][:6]) + ("…" if len(spec["enum"]) > 6 else ""))
        if spec.get("items", {}).get("enum"):
            values = spec["items"]["enum"]
            constraints.append("items enum: " + ", ".join(f"`{v}`" for v in values[:5]) + ("…" if len(values) > 5 else ""))
        lines.append(
            "| `{name}` | {type} | {req} | {con} | {default} | {desc} |".format(
                name=name,
                type=spec.get("type", "any"),
                req="yes" if name in required else "no",
                con="; ".join(constraints) or "—",
                default=f"`{spec['default']}`" if "default" in spec else "—",
                desc=spec.get("description", "").replace("\n", " "),
            )
        )
    extra = schema.get("additionalProperties")
    if extra is False:
        lines.append("| _any other field_ | — | — | rejected with `INVALID_INPUT` | — | Unknown arguments are refused, not ignored. |")
    return "\n".join(lines)


def output_rows(schema: dict) -> str:
    lines = ["| Field | Type | Meaning |", "|---|---|---|"]
    for name, spec in (schema.get("properties") or {}).items():
        kind = spec.get("type", "any")
        if isinstance(kind, list):
            kind = " or ".join(kind)
        lines.append(f"| `{name}` | {kind} | {spec.get('description', '')} |")
    return "\n".join(lines)


def truncate(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= MAX_EXAMPLE_CHARS:
        return text
    return text[:MAX_EXAMPLE_CHARS].rsplit("\n", 1)[0] + "\n  … truncated for the document; the tool returns the full object"


def render() -> str:
    config = Configuration.load()
    store = DatasetStore(config.settings.data_dir).load()

    blocks = [BEGIN, ""]
    for name, module in ORDER:
        try:
            example_text = truncate(module.run(config, store, dict(module.EXAMPLE_ARGUMENTS)))
        except Exception as exc:  # a document must not claim an example that fails
            example_text = f"# example unavailable: {exc.__class__.__name__}: {exc}"

        errors = "\n".join(f"| `{code}` | {reason} |" for code, reason in module.ERROR_CONDITIONS)
        required = ", ".join(f"`{key}`" for key in module.OUTPUT_SCHEMA.get("required", []))

        blocks.append(
            "\n".join(
                [
                    f"## `{name}`",
                    "",
                    "| Contract element | Content |",
                    "|---|---|",
                    f"| **Name** | `{name}` |",
                    f"| **Purpose** | {module.PURPOSE} |",
                    f"| **Side effects** | {module.SIDE_EFFECTS} |",
                    "",
                    "**Model-facing description** (the exact string exposed over MCP):",
                    "",
                    f"> {module.DESCRIPTION}",
                    "",
                    "**Input schema**",
                    "",
                    schema_rows(module.INPUT_SCHEMA),
                    "",
                    "**Output schema**",
                    "",
                    output_rows(module.OUTPUT_SCHEMA),
                    "",
                    f"Always present on success: {required}.",
                    "",
                    "**Error conditions**",
                    "",
                    "| Code | Raised when |",
                    "|---|---|",
                    errors,
                    "",
                    "Failures come back as an MCP error result carrying",
                    '`{"status": "error", "error": {"code", "message", "retryable", "details"}}`.',
                    "A successful empty result is an ordinary payload with `result_count: 0`,",
                    "so the two can never be read for one another.",
                    "",
                    "**Example** — arguments:",
                    "",
                    "```json",
                    json.dumps(module.EXAMPLE_ARGUMENTS, ensure_ascii=False, indent=2),
                    "```",
                    "",
                    "Response, produced by running the tool against the committed dataset:",
                    "",
                    "```json",
                    example_text,
                    "```",
                    "",
                ]
            )
        )
    blocks.append(END)
    return "\n".join(blocks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    current = DOC.read_text(encoding="utf-8")
    if BEGIN not in current or END not in current:
        print(f"markers not found in {DOC}", file=sys.stderr)
        return 2

    head, rest = current.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    updated = head + render() + tail

    if args.check:
        if updated != current:
            print("docs/tool-contracts.md is stale; run scripts/generate_tool_docs.py", file=sys.stderr)
            return 1
        print("docs/tool-contracts.md is current")
        return 0

    DOC.write_text(updated, encoding="utf-8")
    print(f"wrote {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
