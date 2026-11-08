"""Export the JSON Schema of the snapshot, event, error and stream models (03 §10.3).

    python backend/tools/export_schema.py [--out web/src/api/schema.json] [--check]

The renderer compiles this file into ``web/src/api/types.ts`` (``npm run types``). ``--check`` exits 1
when the file on disk differs from the models.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic.json_schema import models_json_schema  # noqa: E402

from relay.api import schemas, schemas_app, schemas_judge, schemas_procedures, schemas_programs  # noqa: E402

DEFAULT_OUT = ROOT / "web" / "src" / "api" / "schema.json"


def _tidy(node: Any, keep_title: bool = False) -> Any:
    """Drop open-world ``additionalProperties: true`` markers and field titles.

    Field titles would become one named TypeScript alias per field (``Id``, ``Id1``, ...); model titles stay.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "additionalProperties" and value is True:
                continue
            if key == "title" and not keep_title:
                continue
            if key == "$defs":
                out[key] = {name: _tidy(model, keep_title=True) for name, model in value.items()}
            elif key == "properties" and isinstance(value, dict):
                out[key] = {name: _tidy(field) for name, field in value.items()}
            else:
                out[key] = _tidy(value)
        return out
    if isinstance(node, list):
        return [_tidy(v) for v in node]
    return node


def build() -> dict[str, Any]:
    exported = {
        **schemas.EXPORTED,
        **schemas_app.EXPORTED,
        **schemas_procedures.EXPORTED,
        **schemas_programs.EXPORTED,  # WP-1
        **schemas_judge.EXPORTED,  # WP-2b
    }
    models = [(model, "serialization") for model in exported.values()]
    keys, defs = models_json_schema(models, by_alias=True, ref_template="#/$defs/{model}")
    properties = {name: keys[(model, "serialization")] for name, model in exported.items()}
    return _tidy(
        keep_title=True,
        node={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://agentnetwork.org.cn/schemas/anet-relay/v1.json",
            "title": "RelaySchema",
            "description": "ANet Relay API v1 models (backend/tools/export_schema.py; do not edit).",
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
            "$defs": dict(sorted(defs.get("$defs", {}).items())),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file differs")
    args = parser.parse_args(argv)
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        current = args.out.read_text("utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date; run backend/tools/export_schema.py", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, "utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
