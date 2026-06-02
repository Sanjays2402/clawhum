import json
import time

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app, _filter_feedback, _parse_time_bound


def test_parse_time_bound_unix_epoch():
    assert _parse_time_bound("1700000000", flag="--since") == 1700000000.0
    assert _parse_time_bound("1700000000.5", flag="--since") == 1700000000.5


def test_parse_time_bound_iso_date_treated_as_utc():
    # 1970-01-02 UTC = 86400
    assert _parse_time_bound("1970-01-02", flag="--since") == 86400.0


def test_parse_time_bound_iso_datetime_z_suffix():
    # 1970-01-01T00:00:00Z = 0
    assert _parse_time_bound("1970-01-01T00:00:00Z", flag="--since") == 0.0


def test_parse_time_bound_rejects_garbage():
    import typer
    with pytest.raises(typer.BadParameter):
        _parse_time_bound("not-a-date", flag="--since")
    with pytest.raises(typer.BadParameter):
        _parse_time_bound("", flag="--since")


def test_parse_time_bound_rejects_bare_year_or_short_int():
    # Regression: "2024" used to silently parse as epoch=2024s (1970-01-01)
    # and let nearly every row through the since/until filter.
    import typer
    for bad in ("2024", "20240101", "99999999", "0", "-1"):
        with pytest.raises(typer.BadParameter):
            _parse_time_bound(bad, flag="--since")
    # Plausible epoch seconds (>= 100000000 ~ 1973) still work as integers.
    assert _parse_time_bound("100000000", flag="--since") == 100000000.0
    # Explicit float keeps working for any magnitude.
    assert _parse_time_bound("0.0", flag="--since") == 0.0


def test_filter_feedback_since_until_inclusive_lower_exclusive_upper():
    rows = [
        {"query_id": "q", "track_id": "t", "vote": 1, "score": 0.5, "ts": 10.0},
        {"query_id": "q", "track_id": "t", "vote": 1, "score": 0.5, "ts": 20.0},
        {"query_id": "q", "track_id": "t", "vote": 1, "score": 0.5, "ts": 30.0},
    ]
    assert [r["ts"] for r in _filter_feedback(rows, since=20.0)] == [20.0, 30.0]
    assert [r["ts"] for r in _filter_feedback(rows, until=30.0)] == [10.0, 20.0]
    assert [r["ts"] for r in _filter_feedback(rows, since=20.0, until=30.0)] == [20.0]


def test_filter_feedback_drops_rows_without_numeric_ts_when_time_filtering():
    rows = [
        {"track_id": "t", "vote": 1, "score": 0.5, "ts": 10.0},
        {"track_id": "t", "vote": 1, "score": 0.5},  # no ts
        {"track_id": "t", "vote": 1, "score": 0.5, "ts": "bad"},
    ]
    assert _filter_feedback(rows, since=0.0) == [rows[0]]


def _seed_two_epochs(tmp_path, monkeypatch):
    from clawhum_library import feedback as fb_mod
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # two rows with known, deterministic timestamps
    times = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr(fb_mod.time, "time", lambda: next(times))
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    return fb


def test_feedback_list_filters_by_since_and_until(tmp_path, monkeypatch):
    _seed_two_epochs(tmp_path, monkeypatch)
    runner = CliRunner()

    r = runner.invoke(app, ["feedback-list", "--format", "json", "--since", "200.0"])
    assert r.exit_code == 0, r.output
    assert sorted(row["ts"] for row in json.loads(r.stdout)) == [200.0, 300.0]

    r = runner.invoke(app, ["feedback-list", "--format", "json", "--until", "300.0"])
    assert r.exit_code == 0
    assert sorted(row["ts"] for row in json.loads(r.stdout)) == [100.0, 200.0]

    r = runner.invoke(
        app, ["feedback-list", "--format", "json", "--since", "150.0", "--until", "250.0"]
    )
    assert r.exit_code == 0
    rows = json.loads(r.stdout)
    assert len(rows) == 1 and rows[0]["ts"] == 200.0


def test_feedback_list_rejects_inverted_window(tmp_path, monkeypatch):
    _seed_two_epochs(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app, ["feedback-list", "--since", "300.0", "--until", "200.0"]
    )
    assert r.exit_code != 0


def test_feedback_stats_window_excludes_older_rows(tmp_path, monkeypatch):
    _seed_two_epochs(tmp_path, monkeypatch)
    runner = CliRunner()
    # window 250..inf keeps only ts=300 (q2/t1, +1)
    r = runner.invoke(
        app, ["feedback-stats", "--format", "json", "--since", "250.0"]
    )
    assert r.exit_code == 0, r.output
    stats = json.loads(r.stdout)
    assert len(stats) == 1
    s = stats[0]
    assert s["track_id"] == "t1"
    assert s["up"] == 1 and s["down"] == 0 and s["total"] == 1
