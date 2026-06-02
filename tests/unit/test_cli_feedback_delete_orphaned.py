"""--orphaned / --in-index restrict feedback-delete to stale or live votes.

After pruning the library, a workspace owner wants to purge the now-orphaned
votes without touching feedback against the live catalog. --in-index is the
inverse and is useful to combine with score/vote filters so a name-blind
purge cannot accidentally wipe stale votes you intended to audit first.
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

    # t1 and t2 are in the live library; t3 has feedback but was pruned.
    write_metadata(meta, [
        Track(id="t1", title="A", artist="X", path="/x/a.mp3", source="local"),
        Track(id="t2", title="B", artist="Y", path="/x/b.mp3", source="local"),
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t3", 0.5, 1)
    record_feedback(fb, "q4", "t3", 0.6, -1)

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_feedback_delete_orphaned_only_purges_pruned_tracks(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--orphaned", "--yes"])
    assert result.exit_code == 0, result.output
    assert "deleted 2" in result.output
    remaining = {r["track_id"] for r in read_feedback(str(fb))}
    assert remaining == {"t1", "t2"}


def test_feedback_delete_in_index_leaves_orphans_alone(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # Purge down-votes only against the live catalog; the orphaned -1 on t3
    # must survive because --in-index restricts the allowlist to t1/t2.
    result = runner.invoke(
        app, ["feedback-delete", "--in-index", "--vote", "-1", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    rows = read_feedback(str(fb))
    surviving = {(r["track_id"], r["vote"]) for r in rows}
    assert (("t2", -1)) not in surviving
    assert ("t3", -1) in surviving
    assert ("t3", 1) in surviving
    assert ("t1", 1) in surviving


def test_feedback_delete_orphaned_and_in_index_are_mutually_exclusive(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-delete", "--orphaned", "--in-index", "--yes"]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_feedback_delete_orphaned_dry_run_does_not_write(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    before = read_feedback(str(fb))
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--orphaned", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would delete 2" in result.output
    after = read_feedback(str(fb))
    assert before == after


def test_feedback_delete_orphaned_no_orphans_is_noop(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    meta = tmp_path / "metadata.jsonl"
    write_metadata(meta, [
        Track(id="t1", title="A", artist="X", path="/x/a.mp3", source="local"),
    ])
    record_feedback(fb, "q1", "t1", 0.9, 1)

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-delete", "--orphaned", "--yes"])
    assert result.exit_code == 0, result.output
    assert "no matching feedback entries" in result.output
    assert len(read_feedback(str(fb))) == 1


def test_feedback_delete_in_index_with_title_intersects(tmp_path, monkeypatch):
    fb, _ = _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # title=A matches only t1; --in-index keeps t1 (also live). Expect 1 delete.
    result = runner.invoke(
        app, ["feedback-delete", "--in-index", "--title", "A", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "deleted 1" in result.output
    remaining = {r["track_id"] for r in read_feedback(str(fb))}
    assert "t1" not in remaining
    assert {"t2", "t3"} <= remaining
