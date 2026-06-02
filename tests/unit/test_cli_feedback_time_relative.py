"""Tests for relative time-bound shorthand (e.g. --since 24h) in feedback CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app, _parse_time_bound


runner = CliRunner()


def test_parse_relative_hours():
    # 24h before t=1_000_000 is 1_000_000 - 86400
    assert _parse_time_bound("24h", flag="--since", now=1_000_000.0) == 1_000_000.0 - 24 * 3600


def test_parse_relative_minutes_seconds_days_weeks():
    base = 10_000_000.0
    assert _parse_time_bound("30m", flag="--since", now=base) == base - 30 * 60
    assert _parse_time_bound("45s", flag="--since", now=base) == base - 45
    assert _parse_time_bound("7d", flag="--since", now=base) == base - 7 * 86400
    assert _parse_time_bound("2w", flag="--since", now=base) == base - 2 * 7 * 86400


def test_parse_relative_zero_is_now():
    assert _parse_time_bound("0h", flag="--until", now=12345.0) == 12345.0
    assert _parse_time_bound("0d", flag="--until", now=12345.0) == 12345.0


def test_parse_relative_fractional():
    # 1.5h before base
    base = 1_000_000.0
    assert _parse_time_bound("1.5h", flag="--since", now=base) == base - 1.5 * 3600


def test_parse_relative_negative_rejected():
    # negative offset is not meaningful for a "since/until in the past" filter,
    # and should fall through and be rejected as garbage rather than silently
    # produce a future timestamp.
    with pytest.raises(Exception):
        _parse_time_bound("-1h", flag="--since")


def test_parse_relative_does_not_swallow_iso_or_epoch():
    # ISO and epoch parsing still work alongside the new shorthand.
    assert _parse_time_bound("1700000000", flag="--since") == 1_700_000_000.0
    assert _parse_time_bound("1970-01-02", flag="--since") == 86400.0


def _seed(tmp_path: Path, monkeypatch) -> Path:
    feedback_path = tmp_path / "feedback.jsonl"
    rows = [
        {"query_id": "q1", "track_id": "t1", "score": 0.9, "vote": 1, "ts": 100.0},
        {"query_id": "q2", "track_id": "t2", "score": 0.8, "vote": 1, "ts": 200.0},
        {"query_id": "q3", "track_id": "t3", "score": 0.7, "vote": -1, "ts": 300.0},
    ]
    with feedback_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    class _S:
        feedback_path_attr = str(feedback_path)

        def __init__(self):
            self.feedback_path = str(feedback_path)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return feedback_path


def test_feedback_list_accepts_relative_since(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    # Freeze "now" so 100s relative ago == 250 (rows with ts >= 250).
    import time as _t
    monkeypatch.setattr(_t, "time", lambda: 350.0)

    result = runner.invoke(app, ["feedback-list", "--format", "json", "--since", "100s"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    ts = sorted(r["ts"] for r in rows)
    assert ts == [300.0]


def test_feedback_stats_accepts_relative_window(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    import time as _t
    # "now" = 400; --since 200s => 200, --until 50s => 350. Window [200, 350).
    monkeypatch.setattr(_t, "time", lambda: 400.0)

    result = runner.invoke(
        app,
        ["feedback-stats", "--format", "json", "--since", "200s", "--until", "50s"],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    track_ids = sorted(r["track_id"] for r in rows)
    assert track_ids == ["t2", "t3"]
