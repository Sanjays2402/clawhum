import json

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
    record_feedback(fb, "q2", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t3", 0.8, 1)
    record_feedback(fb, "q4", "t4", 0.7, -1)


def test_library_delete_feedback_query_ids_allowlist(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, query_ids=["q1", "q3"])
    assert removed == 2
    rows = read_feedback(fb)
    qs = sorted(r["query_id"] for r in rows)
    assert qs == ["q2", "q4"]


def test_library_delete_feedback_query_ids_empty_allowlist_is_noop(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, query_ids=[])
    assert removed == 0
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_repeatable_query_id(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q1", "--query-id", "q3", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "deleted 2" in result.output
    qs = sorted(r["query_id"] for r in read_feedback(fb))
    assert qs == ["q2", "q4"]


def test_cli_feedback_delete_repeatable_track_id(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--track-id", "t1", "--track-id", "t4", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "deleted 2" in result.output
    ts = sorted(r["track_id"] for r in read_feedback(fb))
    assert ts == ["t2", "t3"]


def test_cli_feedback_delete_repeatable_query_id_overlap_with_exclude_rejected(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-delete",
            "--query-id", "q1",
            "--query-id", "q2",
            "--exclude-query-id", "q2",
            "--yes",
        ],
    )
    assert result.exit_code != 0
    assert "must not overlap" in result.output
    # nothing deleted
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_repeatable_query_id_unknown_is_noop(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "nope-a", "--query-id", "nope-b", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "no matching feedback entries" in result.output
    assert len(read_feedback(fb)) == 4


def test_cli_feedback_delete_query_id_blank_entries_ignored(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-delete",
            "--query-id", "  q1  ",
            "--query-id", "",
            "--query-id", "   ",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    qs = sorted(r["query_id"] for r in read_feedback(fb))
    assert qs == ["q2", "q3", "q4"]
