"""Fast, DB-free unit tests for eagent.ats.loader helpers."""
from __future__ import annotations

from datetime import date

from eagent.ats.loader import today_date_id


def test_today_date_id_matches_current_date_format():
    assert today_date_id() == int(date.today().strftime("%Y%m%d"))
