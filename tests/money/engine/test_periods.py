"""期別：5 號起算、期名、邊界（31 號、二月）、改起算日不動舊期、週。"""
from datetime import date

import pytest

from money_engine import db
from money_engine.errors import InvalidInput, NotFound
from money_engine.services import periods, settings


def test_default_period_is_5th_to_4th_named_by_start_month(conn):
    p = periods.period_for(conn, date(2037, 10, 10))
    assert (p.key, p.start, p.end, p.days) == ("2037-10", date(2037, 10, 5), date(2037, 11, 4), 31)
    assert periods.period_for(conn, date(2037, 10, 4)).key == "2037-09"
    assert periods.period_for(conn, date(2037, 11, 5)).key == "2037-11"
    assert p.elapsed(date(2037, 10, 10)) == 6 and p.is_open(date(2037, 10, 10))


def test_current_uses_pinned_today(conn):
    assert periods.current(conn).key == "2037-10"
    assert periods.current(conn).to_dict()["elapsed"] == 6


def test_generates_contiguous_periods_both_directions(conn):
    periods.period_for(conn, date(2037, 10, 10))
    periods.period_for(conn, date(2038, 3, 1))
    periods.period_for(conn, date(2036, 11, 20))
    rows = conn.execute("SELECT start_date, end_date FROM periods ORDER BY start_date").fetchall()
    for a, b in zip(rows, rows[1:]):
        assert date.fromisoformat(a[1]).toordinal() + 1 == date.fromisoformat(b[0]).toordinal()
    assert periods.get(conn, "2036-11").start == date(2036, 11, 5)
    assert periods.get(conn, "2038-02").end == date(2038, 3, 4)


def test_start_day_31_clamps_to_month_end(conn):
    with db.write(conn) as w:
        settings.put(w, "period_start_day", 31)
    p = periods.period_for(conn, date(2037, 2, 10))
    assert (p.start, p.end) == (date(2037, 1, 31), date(2037, 2, 27))
    nxt = periods.shift(conn, p, 1)
    assert (nxt.start, nxt.end) == (date(2037, 2, 28), date(2037, 3, 30))
    assert len({p.key, nxt.key}) == 2


def test_changing_start_day_keeps_existing_periods(conn):
    old = periods.period_for(conn, date(2037, 10, 10))
    with db.write(conn) as w:
        settings.put(w, "period_start_day", 1)
    assert periods.period_for(conn, date(2037, 10, 10)) == old
    nxt = periods.period_for(conn, date(2037, 11, 10))
    assert nxt.start == date(2037, 11, 5) and nxt.end == date(2037, 11, 30)   # 過渡期
    after = periods.shift(conn, nxt, 1)
    assert (after.key, after.start, after.end) == ("2037-12", date(2037, 12, 1), date(2037, 12, 31))


def test_get_validates_keys(conn):
    with pytest.raises(InvalidInput):
        periods.get(conn, "10月")
    with pytest.raises(InvalidInput):
        periods.get(conn, "2037-13")
    assert periods.get(conn, None).key == "2037-10"


def test_start_day_25_names_by_midpoint_and_unknown_key_suggests(conn):
    with db.write(conn) as w:
        settings.put(w, "period_start_day", 25)
    p = periods.get(conn, "2037-10")          # 起算日 25 號：09/25–10/24，期中在 10 月
    assert (p.start, p.end) == (date(2037, 9, 25), date(2037, 10, 24))
    with pytest.raises(NotFound) as e:
        periods.get(conn, "2037-10b")
    assert e.value.suggestions == ["2037-10"]


def test_weeks():
    assert periods.week_key(date(2037, 10, 10)) == "2037-W41"
    assert periods.week_range("2037-W41") == (date(2037, 10, 5), date(2037, 10, 11))
    with pytest.raises(InvalidInput):
        periods.week_range("W41")
