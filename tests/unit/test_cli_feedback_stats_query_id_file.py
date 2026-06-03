"""`clawhum feedback-stats --query-id-file <path>` and `--exclude-query-id-file
<path>` load session id shortlists/denylists from disk for scoping the aggregated
stats when the qid set is too long to pass on the command line or is reused
across many feedback-stats runs. Blank lines and lines starting with '#' are
ignored; file ids union with any --query-id/--exclude-query-id values; the
existing overlap check still applies to the merged set."""

import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # Distinct (query_id, track_id) so each row maps to a unique track and
    # filtering by qid changes which tracks survive the aggregation.
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.5, 1)
    record_feedback(fb, "q3", "t3", 0.4, -1)
    record_feedback(fb, "q4", "t4", 0.2, -1)
    return fb


def _track_ids(out: str) -> set[str]:
    return {r["track_id"] for r in json.loads(out)}


def test_query_id_file_restricts_to_listed_qids(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    shortlist = tmp_path / "only.txt"
    shortlist.write_text("# eval session\nq1\n\nq3\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-stats", "--query-id-file", str(shortlist), "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    assert _track_ids(r.output) == {"t1", "t3"}


def test_query_id_file_unions_with_cli_query_id(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    shortlist = tmp_path / "only.txt"
    shortlist.write_text("q1\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-stats",
            "--query-id-file", str(shortlist),
            "--query-id", "q2",
            "-f", "json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert _track_ids(r.output) == {"t1", "t2"}


def test_exclude_query_id_file_drops_listed_qids(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    denylist = tmp_path / "skip.txt"
    denylist.write_text("# noisy smoke run\nq1\nq4\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-stats", "--exclude-query-id-file", str(denylist), "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    tids = _track_ids(r.output)
    assert "t1" not in tids and "t4" not in tids
    assert tids == {"t2", "t3"}


def test_exclude_query_id_file_unions_with_cli_exclude_query_id(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    denylist = tmp_path / "skip.txt"
    denylist.write_text("q1\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-stats",
            "--exclude-query-id-file", str(denylist),
            "-X", "q4",
            "-f", "json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert _track_ids(r.output) == {"t2", "t3"}


def test_query_id_file_overlap_with_exclude_query_id_still_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    shortlist = tmp_path / "only.txt"
    shortlist.write_text("q1\nq2\n")
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-stats",
            "--query-id-file", str(shortlist),
            "-X", "q1",
            "-f", "json",
        ],
    )
    assert r.exit_code != 0
    assert "must not overlap" in (r.output + str(r.exception or ""))


def test_query_id_file_missing_path_errors(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-stats", "--query-id-file", str(tmp_path / "nope.txt"), "-f", "json"],
    )
    assert r.exit_code != 0
