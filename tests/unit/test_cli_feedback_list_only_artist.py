"""--only-artist / --only-artist-file on feedback-list restrict the listing
to one or more artists. Counterpart to --exclude-artist on the same command
and modelled on the same flag on feedback-stats."""
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


def test_only_artist_restricts_to_one_artist(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--only-artist", "queen", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    # Only Queen tracks survive; orphan t4 is dropped because we cannot prove
    # it belongs to the allowlisted artist.
    assert ids == {"t1", "t5"}
    for r in payload:
        assert r["artist"] == "Queen"


def test_only_artist_is_repeatable(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--only-artist", "Queen",
            "--only-artist", "Coldplay",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t1", "t3", "t5"}


def test_only_artist_whitespace_and_case_insensitive(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-list", "--only-artist", "  QUEEN  ", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t1", "t5"}


def test_only_artist_file_unions_with_cli(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    allow = tmp_path / "allow.txt"
    allow.write_text("# my favourites\nColdplay\n\n   John Lennon  \n")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--only-artist", "Queen",
            "--only-artist-file", str(allow),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t1", "t2", "t3", "t5"}


def test_only_artist_conflicts_with_exclude_artist(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--only-artist", "Queen",
            "--exclude-artist", "Coldplay",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_only_artist_drops_orphans(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-list", "--only-artist", "Queen", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    # orphan t4 has no metadata row, so it cannot prove "Queen" membership
    assert "t4" not in ids
