"""`clawhum feedback-stats --track-id-file <path>` and `--exclude-track-file
<path>` load track id shortlists/denylists from disk for scoping the
aggregation when the id set is too long to pass on the command line or is
reused across many feedback-stats runs. Blank lines and lines starting with
'#' are ignored; file ids union with any --track-id/--exclude-track values;
the existing overlap check still applies to the merged set."""

import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.5, 1)
    record_feedback(fb, "q3", "t3", 0.4, -1)
    record_feedback(fb, "q4", "t4", 0.2, -1)
    return fb


def _ids(out: str) -> set[str]:
    return {r["track_id"] for r in json.loads(out)}


def test_track_id_file_restricts_to_listed_ids(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    shortlist = tmp_path / "only.txt"
    shortlist.write_text("# my candidates\nt1\n\nt3\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-stats", "--track-id-file", str(shortlist), "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    assert _ids(r.output) == {"t1", "t3"}


def test_track_id_file_unions_with_cli_track_id(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    shortlist = tmp_path / "only.txt"
    shortlist.write_text("t1\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-stats",
            "--track-id-file", str(shortlist),
            "--track-id", "t2",
            "-f", "json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert _ids(r.output) == {"t1", "t2"}


def test_exclude_track_file_drops_listed_ids(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    denylist = tmp_path / "skip.txt"
    denylist.write_text("# noise\nt1\nt4\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-stats", "--exclude-track-file", str(denylist), "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    ids = _ids(r.output)
    assert "t1" not in ids and "t4" not in ids
    assert ids == {"t2", "t3"}


def test_exclude_track_file_unions_with_cli_exclude_track(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    denylist = tmp_path / "skip.txt"
    denylist.write_text("t1\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-stats",
            "--exclude-track-file", str(denylist),
            "-x", "t4",
            "-f", "json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert _ids(r.output) == {"t2", "t3"}


def test_track_id_file_overlap_with_exclude_track_still_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    shortlist = tmp_path / "only.txt"
    shortlist.write_text("t1\nt2\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-stats",
            "--track-id-file", str(shortlist),
            "-x", "t1",
            "-f", "json",
        ],
    )
    assert r.exit_code != 0
    assert "must not overlap" in (r.output + str(r.exception or ""))


def test_track_id_file_missing_path_errors(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-stats", "--track-id-file", str(tmp_path / "nope.txt"), "-f", "json"],
    )
    assert r.exit_code != 0
