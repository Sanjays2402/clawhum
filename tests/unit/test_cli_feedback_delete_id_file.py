"""`clawhum feedback-delete --track-id-file`, `--exclude-track-file`,
`--query-id-file`, and `--exclude-query-id-file` load id shortlists/denylists
from disk so scripted bulk purges can drive the delete from an external file
when the id set is too long for a command line or is reused across runs.

Blank lines and lines starting with '#' are ignored; file ids union with any
CLI --track-id/--exclude-track/--query-id/--exclude-query-id values; the
existing overlap checks still apply to the merged set; a positive filter
supplied only via --track-id-file or --query-id-file is enough to satisfy the
no-positive-filter guard so the bare safety check still fires when only
denylists are present."""

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import read_feedback, record_feedback


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
    record_feedback(fb, "q2", "t1", 0.2, -1)
    record_feedback(fb, "q3", "t3", 0.1, -1)
    record_feedback(fb, "q4", "t4", 0.3, -1)


def test_track_id_file_drives_delete(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    shortlist = tmp_path / "kill.txt"
    shortlist.write_text("# tracks to purge\nt2\n\nt4\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-delete", "--track-id-file", str(shortlist), "--yes"],
    )
    assert r.exit_code == 0, r.output
    assert "deleted 2" in r.output
    remaining = read_feedback(fb)
    assert {(row["query_id"], row["track_id"]) for row in remaining} == {
        ("q1", "t1"),
        ("q2", "t1"),
        ("q3", "t3"),
    }


def test_track_id_file_unions_with_cli_track_id(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    shortlist = tmp_path / "kill.txt"
    shortlist.write_text("t2\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--track-id-file", str(shortlist),
            "--track-id", "t4",
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "deleted 2" in r.output
    remaining = read_feedback(fb)
    assert {row["track_id"] for row in remaining} == {"t1", "t3"}


def test_query_id_file_drives_delete(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    sessions = tmp_path / "sessions.txt"
    sessions.write_text("# retired eval runs\nq1\nq4\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-delete", "--query-id-file", str(sessions), "--yes"],
    )
    assert r.exit_code == 0, r.output
    assert "deleted 3" in r.output
    remaining = read_feedback(fb)
    assert {row["query_id"] for row in remaining} == {"q2", "q3"}


def test_exclude_track_file_protects_listed_ids(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    keep = tmp_path / "keep.txt"
    keep.write_text("# do not touch\nt1\nt3\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote", "-1",
            "--exclude-track-file", str(keep),
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    # down-votes on t2 and t4 deleted; t1, t3 protected; up-vote on t1 untouched
    assert "deleted 2" in r.output
    remaining = read_feedback(fb)
    assert {(row["query_id"], row["track_id"]) for row in remaining} == {
        ("q1", "t1"),
        ("q2", "t1"),
        ("q3", "t3"),
    }


def test_exclude_query_id_file_protects_listed_sessions(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    keep = tmp_path / "keep_q.txt"
    keep.write_text("q1\nq3\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote", "-1",
            "--exclude-query-id-file", str(keep),
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    # only q2 and q4 down-votes deleted
    assert "deleted 2" in r.output
    remaining = read_feedback(fb)
    assert {(row["query_id"], row["track_id"]) for row in remaining} == {
        ("q1", "t1"),
        ("q1", "t2"),
        ("q3", "t3"),
    }


def test_track_id_file_overlap_with_exclude_track_rejected(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    shortlist = tmp_path / "kill.txt"
    shortlist.write_text("t1\nt2\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--track-id-file", str(shortlist),
            "--exclude-track", "t1",
            "--yes",
        ],
    )
    assert r.exit_code != 0
    assert len(read_feedback(fb)) == 5


def test_exclude_track_file_alone_still_requires_positive_filter(tmp_path, monkeypatch):
    """A file-loaded denylist on its own must not satisfy the safety guard,
    so a bare `feedback-delete --exclude-track-file foo` can never wipe
    everything not in the denylist."""
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    keep = tmp_path / "keep.txt"
    keep.write_text("t1\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-delete", "--exclude-track-file", str(keep), "--yes"],
    )
    assert r.exit_code != 0
    assert len(read_feedback(fb)) == 5


def test_track_id_file_missing_path_errors(tmp_path, monkeypatch):
    _patch_settings(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-delete", "--track-id-file", str(tmp_path / "nope.txt"), "--yes"],
    )
    assert r.exit_code != 0
