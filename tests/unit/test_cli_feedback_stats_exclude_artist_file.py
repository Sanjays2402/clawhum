"""--exclude-artist-file on feedback-stats loads a persistent deny-list of
noisy artists from disk so users do not have to retype the same
``--exclude-artist X --exclude-artist Y`` list on every aggregation."""
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
    record_feedback(fb, "q5", "t5", 0.6, 1)

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_exclude_artist_file_loads_names_and_drops_matches(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deny = tmp_path / "deny.txt"
    deny.write_text("# noisy covers\nQueen\nColdplay\n\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-stats", "--exclude-artist-file", str(deny), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    ids = {r["track_id"] for r in json.loads(result.stdout)}
    assert ids == {"t2"}


def test_exclude_artist_file_unions_with_inline_flag(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deny = tmp_path / "deny.txt"
    deny.write_text("Queen\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-stats",
            "--exclude-artist-file", str(deny),
            "--exclude-artist", "Coldplay",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    ids = {r["track_id"] for r in json.loads(result.stdout)}
    assert ids == {"t2"}


def test_exclude_artist_file_case_insensitive_and_whitespace_trimmed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deny = tmp_path / "deny.txt"
    deny.write_text("  queen  \n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-stats", "--exclude-artist-file", str(deny), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    ids = {r["track_id"] for r in json.loads(result.stdout)}
    assert "t1" not in ids and "t5" not in ids
    assert ids == {"t2", "t3"}


def test_exclude_artist_file_missing_path_is_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-stats", "--exclude-artist-file", str(tmp_path / "nope.txt")],
    )
    assert result.exit_code != 0
