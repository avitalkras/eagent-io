"""
Unit tests for scripts/notify.py — build_digest_message() (pure) and
send_telegram_message() (mocked HTTP, same dependency-injection pattern as
every other HTTP call site in this codebase since Phase 2).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "notify.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("notify", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def notify():
    return _load_module()


class FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# --- build_digest_message(): pure, no network -------------------------------


def test_digest_message_includes_every_row(notify):
    rows = [
        {
            "outreach_id": 1, "title": "Data Engineer", "company_name": "Acme",
            "ats_score": 92.0, "job_url": "https://acme.example/jobs/1",
        },
        {
            "outreach_id": 2, "title": "Analytics Engineer", "company_name": "Bright Co",
            "ats_score": 87.5, "job_url": None,
        },
    ]

    message = notify.build_digest_message(rows, threshold=85.0)

    assert "2 new matches" in message
    assert "92% — Data Engineer @ Acme" in message
    assert "https://acme.example/jobs/1" in message
    assert "88% — Analytics Engineer @ Bright Co" in message


def test_digest_message_singular_for_one_match(notify):
    rows = [{"outreach_id": 1, "title": "Data Engineer", "company_name": "Acme", "ats_score": 90.0, "job_url": None}]
    message = notify.build_digest_message(rows, threshold=85.0)
    assert "1 new match " in message
    assert "matches" not in message.split("\n")[0]


def test_digest_message_omits_url_line_when_none(notify):
    rows = [{"outreach_id": 1, "title": "Data Engineer", "company_name": "Acme", "ats_score": 90.0, "job_url": None}]
    message = notify.build_digest_message(rows, threshold=85.0)
    assert "None" not in message


# --- send_telegram_message(): mocked HTTP -----------------------------------


def test_send_telegram_message_posts_expected_payload(notify):
    captured: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(200)

    notify.send_telegram_message("fake-token", "12345", "hello", http_post=fake_post)

    assert captured["url"] == "https://api.telegram.org/botfake-token/sendMessage"
    assert captured["json"]["chat_id"] == "12345"
    assert captured["json"]["text"] == "hello"


def test_send_telegram_message_raises_on_http_error(notify):
    def failing_post(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(403)

    with pytest.raises(RuntimeError):
        notify.send_telegram_message("fake-token", "12345", "hello", http_post=failing_post)


# --- main(): skips gracefully when unconfigured, never raises --------------


def test_main_skips_quietly_when_telegram_env_vars_missing(notify, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["notify.py"])

    notify.main()  # must not raise — alerting being unconfigured isn't a failure
