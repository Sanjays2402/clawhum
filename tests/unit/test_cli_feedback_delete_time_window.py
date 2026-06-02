import json
import time

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import delete_feedback, read_feedback


def _patch_settings(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def _write_rows(fb, rows):
    fb.parent.mkdir(parents=True, exist_ok=True)
    with open(fb, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _seed_dated(fb):
    # ts: 2023-01-01, 2024-01-01, 2025-01-01, plus one undated row
    _write_rows(fb, [
        {"ts": 1672531200.0, "query_id": "q1", "track_id": "t1", "score": 0.9, "vote": 1},
        {"ts": 1704067200.0, "query_id": "q2", "track_id": "t1", "score": 0.8, "vote": -1},
        {"ts": 1735689600.0, "query_id": "q3", "track_id": "t2", "score": 0.7, "vote": 1},
        {"query_id": "q4", "track_id": "t2", "score": 0.6, "vote": 1},
    ])


def test_delete_feedback_until_purges_old_rows(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed_dated(fb)
    # delete everything strictly before 2024-06-01
    removed = delete_feedback(fb, until=1717200000.0)
    assert removed == 2  # 2023-01-01 and 2024-01-01
    remaining = read_feedback(fb)
    qs = sorted(r["query_id"] for r in remaining)
    assert qs == ["q3", "q4"]  # 2025 row plus the undated row are preserved


def test_delete_feedback_since_purges_recent_rows(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed_dated(fb)
    removed = delete_feedback(fb, since=1704067200.0)  # 2024-01-01
    assert removed == 2  # 2024-01-01 and 2025-01-01
    remaining = read_feedback(fb)
    qs = sorted(r["query_id"] for r in remaining)
    assert qs == ["q1", "q4"]


def test_delete_feedback_time_window_with_vote(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed_dated(fb)
    removed = delete_feedback(fb, vote=-1, until=1717200000.0)
    assert removed == 1
    remaining = read_feedback(fb)
    assert not any(r.get("vote") == -1 and isinstance(r.get("ts"), (int, float)) and r["ts"] < 1717200000.0 for r in remaining)


def test_delete_feedback_undated_rows_safe_from_time_filter(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _write_rows(fb, [
        {"query_id": "q1", "track_id": "t1", "score": 0.9, "vote": 1},
    ])
    removed = delete_feedback(fb, until=time.time())
    assert removed == 0
    assert len(read_feedback(fb)) == 1


def test_cli_feedback_delete_until_dry_run(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed_dated(fb)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--until", "2024-06-01", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would delete 2" in result.output
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_since_until_with_yes(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed_dated(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--since", "2023-06-01", "--until", "2024-06-01", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    remaining = read_feedback(fb)
    assert sorted(r["query_id"] for r in remaining) == ["q1", "q3", "q4"]


def test_cli_feedback_delete_rejects_since_after_until(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed_dated(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--since", "2025-01-01", "--until", "2024-01-01", "--yes"],
    )
    assert result.exit_code != 0
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_rejects_implausible_epoch_in_time_bound(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed_dated(fb)
    runner = CliRunner()
    # "2024" looks like a year, not 2024 epoch seconds; must be rejected
    result = runner.invoke(app, ["feedback-delete", "--until", "2024", "--yes"])
    assert result.exit_code != 0
    assert len(read_feedback(fb)) == 4
