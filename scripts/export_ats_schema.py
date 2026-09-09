#!/usr/bin/env python3
"""
Regenerates src/eagent/ats/ats_analysis_result.schema.json from the Pydantic
model — the model is the single source of truth; this file is a generated
artifact for documentation / for feeding to tools that want raw JSON Schema.

Run after changing eagent.ats.models.ATSAnalysisResult:
    python scripts/export_ats_schema.py

tests/test_ats_models.py::test_exported_schema_matches_model fails the build
if you change the model and forget to re-run this.
"""
from __future__ import annotations

import json
from pathlib import Path

from eagent.ats.models import ATSAnalysisResult

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "src" / "eagent" / "ats" / "ats_analysis_result.schema.json"


def main() -> None:
    schema = ATSAnalysisResult.model_json_schema()
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
