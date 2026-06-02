import json

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import delete_feedback, read_feedback, record_feedback


def _patch_settings(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def _seed(fb):
    record_feedback(fb, "q1", "t1", 0.95, 1)
    record_feedback(fb, "q1", "t2", 0.40, -1)
    record_feedback(fb, "q2", "t1", 0.15, -1)
    record_feedback(fb, "q3", "t1", 0.70, 1)


def test_delete_feedback_min_score(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, min_score=0.7)
    # only entries with score >= 0.7 are removed (0.95 and 0.70)
    assert removed == 2
    remaining = read_feedback(fb)
    assert sorted(r["score"] for r in remaining) == [0.15, 0.40]


def test_delete_feedback_max_score(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, max_score=0.4)
    # entries with score <= 0.4 are removed (0.40 and 0.15)
    assert removed == 2
    remaining = read_feedback(fb)
    assert sorted(r["score"] for r in remaining) == [0.70, 0.95]


def test_delete_feedback_score_range_and_vote(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    # purge down-votes on weak matches (score < 0.5)
    removed = delete_feedback(fb, vote=-1, max_score=0.5)
    assert removed == 2
    remaining = read_feedback(fb)
    assert all(r["vote"] == 1 for r in remaining)


def test_delete_feedback_keeps_rows_without_numeric_score(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    # write a row with a non-numeric score manually
    fb.write_text(
        json.dumps({"ts": 1.0, "query_id": "q1", "track_id": "t1", "score": "weird", "vote": 1}) + "\n"
        + json.dumps({"ts": 2.0, "query_id": "q1", "track_id": "t2", "score": 0.1, "vote": -1}) + "\n",
        encoding="utf-8",
    )
    removed = delete_feedback(fb, max_score=0.5)
    assert removed == 1
    remaining = read_feedback(fb)
    assert len(remaining) == 1
    assert remaining[0]["score"] == "weird"


def test_cli_feedback_delete_score_flags(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--vote", "-1", "--max-score", "0.5", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "deleted 2" in result.output
    remaining = read_feedback(fb)
    assert all(r["vote"] == 1 for r in remaining)


def test_cli_feedback_delete_score_dry_run(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--min-score", "0.7", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "would delete 2" in result.output
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_rejects_inverted_score_range(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--min-score", "0.9", "--max-score", "0.1", "--yes"],
    )
    assert result.exit_code != 0
    assert len(read_feedback(fb)) == 4
