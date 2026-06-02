"""--title / --artist substring filters on feedback-delete let users purge
their bad votes by song name without first looking up the internal track_id.

Critical safety property: if --title / --artist resolves to zero tracks the
delete must be a no-op, never a no-filter wipe of the whole log.
"""
from __future__ import annotations

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
        # t4 intentionally omitted: orphaned votes must survive a name-based purge
        # because we can't prove they match the requested title/artist.
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t3", 0.8, 1)
    record_feedback(fb, "q4", "t4", 0.7, 1)  # orphan: no metadata row

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_delete_by_title_substring_case_insensitive(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--title", "rhapsody", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted 1 entry" in result.output
    remaining = read_feedback(fb)
    assert sorted(r["track_id"] for r in remaining) == ["t2", "t3", "t4"]


def test_delete_by_artist_substring_case_insensitive(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--artist", "COLDPLAY", "--yes"])
    assert result.exit_code == 0, result.output
    remaining = read_feedback(fb)
    assert "t3" not in {r["track_id"] for r in remaining}
    assert len(remaining) == 3


def test_delete_by_title_and_artist_ands(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # title "o" matches t1 (Bohemian) and t3 (Yellow); artist narrows to t3.
    result = runner.invoke(
        app,
        ["feedback-delete", "--title", "o", "--artist", "coldplay", "--yes"],
    )
    assert result.exit_code == 0, result.output
    remaining = read_feedback(fb)
    ids = sorted(r["track_id"] for r in remaining)
    assert ids == ["t1", "t2", "t4"]


def test_delete_by_title_combines_with_vote(tmp_path, monkeypatch):
    """--title narrows the candidate tracks, --vote narrows again. Both apply."""
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # Yellow is an up-vote; --vote -1 must keep it.
    result = runner.invoke(
        app,
        ["feedback-delete", "--title", "yellow", "--vote", "-1", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "no matching feedback entries" in result.output
    assert len(read_feedback(fb)) == 4


def test_delete_by_title_no_metadata_match_is_noop(tmp_path, monkeypatch):
    """Critical: a title that resolves to zero tracks must NOT wipe the log."""
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--title", "no-such-song", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "no matching feedback entries" in result.output
    assert len(read_feedback(fb)) == 4


def test_delete_by_title_skips_orphaned_tracks(tmp_path, monkeypatch):
    """Orphaned votes (t4) have no metadata, so a name-based purge can't touch them."""
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # An empty title needle would match every indexed track, but t4 has no
    # metadata row so it must survive.
    result = runner.invoke(app, ["feedback-delete", "--title", "", "--yes"])
    assert result.exit_code == 0, result.output
    remaining = read_feedback(fb)
    assert {r["track_id"] for r in remaining} == {"t4"}


def test_delete_by_title_dry_run_does_not_write(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-delete", "--title", "rhapsody", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "would delete 1 entry" in result.output
    assert len(read_feedback(fb)) == 4
