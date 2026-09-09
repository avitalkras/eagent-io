"""Fast, DB-free unit tests for eagent.ats.loader helpers."""
from __future__ import annotations

from datetime import date

from eagent.ats.loader import today_date_id


def test_today_date_id_matches_current_date_format():
    # date.today() here deliberately mirrors the same local-timezone call
    # inside today_date_id() itself — see that function's docstring for the
    # known local-vs-server-timezone limitation this test is not meant to cover.
    assert today_date_id() == int(date.today().strftime("%Y%m%d"))  # noqa: DTZ011
