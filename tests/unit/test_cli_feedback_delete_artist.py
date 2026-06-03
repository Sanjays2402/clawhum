"""--only-artist / --exclude-artist filters on feedback-delete let users
purge (or protect) all votes for a given artist without first looking up
each internal track_id.

Critical safety properties:
* --only-artist resolving to zero tracks must be a no-op (never a no-filter
  wipe). Orphan tracks (no metadata) are skipped because we cannot prove
  the artist matches the allowlist.
* --exclude-artist must protect every track tagged with that artist, even
  when the positive filter (e.g. --vote -1) would otherwise match it.
  Orphan tracks are kept too because we cannot prove they are not the
  protected artist.
* --only-artist and --exclude-artist are mutually exclusive.
"""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Track
from clawhum_index.persistence import write_metadata
from clawhum_library.feedback import read_feedback, record_feedback


def _seed(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    meta = tmp_path / "metadata.jsonl"

    write_metadata(meta, [
        Track(id="t1", title="Bohemian Rhapsody", artist="Queen", path="/x/a.mp3", source="local"),
        Track(id="t2", title="Imagine", artist="John Lennon", path="/x/b.mp3", source="local"),
        Track(id="t3", title="Yellow", artist="Coldplay", path="/x/c.mp3", source="local"),
        Track(id="t4", title="Fix You", artist="Coldplay", path="/x/d.mp3", source="local"),
        # t5 intentionally omitted: orphan votes (no metadata)
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)   # Queen up
    record_feedback(fb, "q2", "t2", 0.4, -1)  # Lennon down
    record_feedback(fb, "q3", "t3", 0.8, 1)   # Coldplay up
    record_feedback(fb, "q4", "t4", 0.3, -1)  # Coldplay down
    record_feedback(fb, "q5", "t5", 0.7, -1)  # orphan down

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_only_artist_purges_just_that_artist_case_insensitive(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--only-artist", "coldplay", "--yes"])
    assert result.exit_code == 0, result.output
    remaining = sorted(r["track_id"] for r in read_feedback(fb))
    # Both Coldplay votes gone; Queen, Lennon, and the orphan survive.
    assert remaining == ["t1", "t2", "t5"]


def test_only_artist_unknown_is_noop_not_wipe(tmp_path, monkeypatch):
    """Critical: an unknown artist must NOT degrade to a no-filter wipe."""
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--only-artist", "Nobody Real", "--yes"])
    assert result.exit_code == 0, result.output
    assert "no matching feedback entries" in result.output
    assert len(read_feedback(fb)) == 5


def test_exclude_artist_protects_tracks_during_bulk_purge(tmp_path, monkeypatch):
    """--vote -1 alone would delete t2, t4, t5. Protecting Coldplay must save t4."""
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--vote", "-1", "--exclude-artist", "Coldplay", "--yes"],
    )
    assert result.exit_code == 0, result.output
    remaining = sorted(r["track_id"] for r in read_feedback(fb))
    # t2 (Lennon down) and t5 (orphan down) purged; t4 (Coldplay down) protected.
    assert remaining == ["t1", "t3", "t4"]


def test_only_artist_and_exclude_artist_mutually_exclusive(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--only-artist", "Queen", "--exclude-artist", "Coldplay", "--yes"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_exclude_artist_cannot_stand_alone(tmp_path, monkeypatch):
    """--exclude-artist is a denylist, not a positive filter; it must not pass
    the no-positive-filter guard."""
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--exclude-artist", "Coldplay", "--yes"])
    assert result.exit_code != 0
    assert "supply at least one of" in result.output


def test_only_artist_file_unions_with_cli(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    list_file = tmp_path / "artists.txt"
    list_file.write_text("# favourites\nColdplay\n\n")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-delete",
            "--only-artist", "Queen",
            "--only-artist-file", str(list_file),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    remaining = sorted(r["track_id"] for r in read_feedback(fb))
    # Queen (t1) + both Coldplay (t3, t4) gone. Lennon (t2) and orphan (t5) survive.
    assert remaining == ["t2", "t5"]


def test_exclude_artist_file_unions_with_cli(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    list_file = tmp_path / "protected.txt"
    list_file.write_text("Queen\n")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote", "1",
            "--exclude-artist", "Coldplay",
            "--exclude-artist-file", str(list_file),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    remaining = sorted(r["track_id"] for r in read_feedback(fb))
    # --vote 1 would delete t1 (Queen up) and t3 (Coldplay up); both protected.
    # All five rows survive.
    assert remaining == ["t1", "t2", "t3", "t4", "t5"]


def test_only_artist_dry_run_does_not_modify(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--only-artist", "Coldplay", "--dry-run", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "would delete 2" in result.output
    assert len(read_feedback(fb)) == 5
