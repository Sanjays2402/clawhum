import json

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import (
    delete_feedback,
    read_feedback,
    record_feedback,
)


def _patch_settings(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def _seed(fb):
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    record_feedback(fb, "q3", "t1", 0.7, -1)


def test_delete_feedback_requires_a_filter(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    with pytest.raises(ValueError):
        delete_feedback(fb)
    # nothing was removed
    assert len(read_feedback(fb)) == 4


def test_delete_feedback_by_query_and_track(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, query_id="q1", track_id="t1")
    assert removed == 1
    remaining = read_feedback(fb)
    assert len(remaining) == 3
    assert not any(r["query_id"] == "q1" and r["track_id"] == "t1" for r in remaining)


def test_delete_feedback_by_vote_only(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, vote=-1)
    assert removed == 2
    remaining = read_feedback(fb)
    assert all(r["vote"] == 1 for r in remaining)


def test_delete_feedback_no_match_returns_zero(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, query_id="nope")
    assert removed == 0
    assert len(read_feedback(fb)) == 4


def test_delete_feedback_missing_file(tmp_path):
    fb = tmp_path / "missing.jsonl"
    assert delete_feedback(fb, query_id="q1") == 0


def test_cli_feedback_delete_rejects_no_filter(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete"])
    assert result.exit_code != 0
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_rejects_bad_vote(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--vote", "2"])
    assert result.exit_code != 0
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_dry_run_does_not_write(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--query-id", "q1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would delete 2" in result.output
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_with_yes_removes_matching(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q1", "--track-id", "t2", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    remaining = read_feedback(fb)
    assert len(remaining) == 3
    assert not any(r["query_id"] == "q1" and r["track_id"] == "t2" for r in remaining)


def test_cli_feedback_delete_prompt_no_aborts(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--query-id", "q1"], input="n\n")
    assert result.exit_code != 0
    assert "aborted" in result.output
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_no_matches_message(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--query-id", "nope", "--yes"])
    assert result.exit_code == 0, result.output
    assert "no matching feedback" in result.output
    assert len(read_feedback(fb)) == 4


def test_delete_feedback_preserves_other_rows_byte_exact(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    delete_feedback(fb, query_id="q1", track_id="t1")
    # the kept rows are valid JSON lines and in original order
    with open(fb) as f:
        kept = [json.loads(line) for line in f if line.strip()]
    assert [r["query_id"] for r in kept] == ["q1", "q2", "q3"]
    assert [r["track_id"] for r in kept] == ["t2", "t1", "t1"]
