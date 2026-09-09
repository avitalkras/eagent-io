"""Shared pytest fixtures for the ATS test suite."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eagent.ats.models import MasterResume

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "master_resume.example.json"


@pytest.fixture()
def master_resume() -> MasterResume:
    return MasterResume.model_validate(json.loads(FIXTURE_PATH.read_text()))
