import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # session A
    record_feedback(fb, "qA", "t1", 0.9, 1)
    record_feedback(fb, "qA", "t2", 0.4, -1)
    # session B
    record_feedback(fb, "qB", "t1", 0.7, -1)
    record_feedback(fb, "qB", "t3", 0.5, 1)
    # session C (noise)
    record_feedback(fb, "qC", "t1", 0.1, -1)
    record_feedback(fb, "qC", "t2", 0.1, -1)
    return fb


def test_query_id_single_value_filters_one_session(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--query-id", "qA"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert {row["query_id"] for row in rows} == {"qA"}
    assert len(rows) == 2


def test_query_id_repeatable_unions_sessions(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-list", "--format", "json", "--query-id", "qA", "--query-id", "qB"],
    )
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert {row["query_id"] for row in rows} == {"qA", "qB"}
    # qC noise excluded
    assert len(rows) == 4


def test_query_id_short_flag(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "-q", "qB"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert {row["query_id"] for row in rows} == {"qB"}


def test_query_id_and_exclude_query_id_overlap_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-list",
            "--format",
            "json",
            "--query-id",
            "qA",
            "--exclude-query-id",
            "qA",
        ],
    )
    assert r.exit_code != 0
    assert "must not overlap" in r.output.lower() or "must not overlap" in str(
        r.exception or ""
    )


def test_query_id_blank_entries_ignored(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # blank --query-id should be a no-op, returning all rows
    r = runner.invoke(
        app, ["feedback-list", "--format", "json", "--query-id", "   "]
    )
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert {row["query_id"] for row in rows} == {"qA", "qB", "qC"}
