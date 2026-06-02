from __future__ import annotations

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import read_feedback


def _setup(tmp_path, monkeypatch, meta):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main._load_track_metadata", lambda: meta)
    return fb


def test_require_known_track_rejects_orphan_vote(tmp_path, monkeypatch):
    fb = _setup(tmp_path, monkeypatch, meta={"t-known": ("Song", "Artist")})
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback", "q1", "t-missing", "0.9", "1", "--require-known-track"],
    )
    assert result.exit_code == 2, result.output
    assert "unknown track_id" in result.output
    assert read_feedback(fb) == []


def test_require_known_track_records_when_present(tmp_path, monkeypatch):
    fb = _setup(tmp_path, monkeypatch, meta={"t-known": ("Song", "Artist")})
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback", "q1", "t-known", "0.9", "1", "-K"],
    )
    assert result.exit_code == 0, result.output
    assert "Song - Artist" in result.output
    rows = read_feedback(fb)
    assert len(rows) == 1
    assert rows[0]["track_id"] == "t-known"


def test_without_flag_unknown_track_still_records(tmp_path, monkeypatch):
    # Default behavior is preserved: unknown track_ids are not blocked.
    fb = _setup(tmp_path, monkeypatch, meta={"t-known": ("Song", "Artist")})
    runner = CliRunner()
    result = runner.invoke(app, ["feedback", "q1", "t-missing", "0.9", "1"])
    assert result.exit_code == 0, result.output
    rows = read_feedback(fb)
    assert len(rows) == 1
    assert rows[0]["track_id"] == "t-missing"
