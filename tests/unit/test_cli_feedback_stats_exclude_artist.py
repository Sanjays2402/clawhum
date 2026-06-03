"""--exclude-artist on feedback-stats lets the user hide a noisy artist
(covers, alternate editions, smoke-test seeds) from the aggregated stats
without rewriting the feedback file."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Track
from clawhum_index.persistence import write_metadata
from clawhum_library.feedback import record_feedback


def _seed(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    meta = tmp_path / "metadata.jsonl"

    write_metadata(meta, [
        Track(id="t1", title="Bohemian Rhapsody", artist="Queen", path="/x/a.mp3", source="local"),
        Track(id="t2", title="Imagine", artist="John Lennon", path="/x/b.mp3", source="local"),
        Track(id="t3", title="Yellow", artist="Coldplay", path="/x/c.mp3", source="local"),
        Track(id="t5", title="Don't Stop Me Now", artist="Queen", path="/x/e.mp3", source="local"),
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t3", 0.8, 1)
    record_feedback(fb, "q4", "t4", 0.7, 1)  # orphan: no metadata row
    record_feedback(fb, "q5", "t5", 0.6, 1)

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_exclude_artist_drops_matching_artist_case_insensitive(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-stats", "--exclude-artist", "queen", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    # Both Queen rows (t1, t5) gone. t2, t3 stay. Orphan t4 stays (no metadata,
    # can't prove it's Queen) so it remains visible for cleanup.
    assert ids == {"t2", "t3", "t4"}
    # auto-enrich: title/artist columns appear without --enrich.
    for r in payload:
        assert "title" in r and "artist" in r


def test_exclude_artist_is_repeatable(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-stats",
            "--exclude-artist", "Queen",
            "--exclude-artist", "Coldplay",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t2", "t4"}


def test_exclude_artist_whitespace_trimmed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-stats", "--exclude-artist", "  QUEEN  ", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert "t1" not in ids and "t5" not in ids


def test_exclude_artist_keeps_orphans_unless_in_index(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-stats", "--exclude-artist", "Queen", "--in-index", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert "t4" not in ids
    assert ids == {"t2", "t3"}


def test_exclude_artist_conflicts_with_matching_artist_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-stats",
            "--artist", "queen",
            "--exclude-artist", "Queen",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0
    assert "must not target the same artist" in result.output


def test_no_exclude_artist_keeps_existing_behaviour(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert {r["track_id"] for r in payload} == {"t1", "t2", "t3", "t4", "t5"}
