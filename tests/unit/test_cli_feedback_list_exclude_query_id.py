import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    record_feedback(fb, "qA", "t1", 0.9, 1)
    record_feedback(fb, "qB", "t2", 0.7, -1)
    record_feedback(fb, "qC", "t3", 0.1, -1)
    record_feedback(fb, "qC", "t4", 0.1, -1)
    return fb


def _rows(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_exclude_query_id_drops_listed_sessions(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--format", "json", "--exclude-query-id", "qC"]
    )
    rows = _rows(result)
    qids = sorted(r["query_id"] for r in rows)
    assert qids == ["qA", "qB"]


def test_exclude_query_id_repeatable(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--format",
            "json",
            "--exclude-query-id",
            "qB",
            "--exclude-query-id",
            "qC",
        ],
    )
    rows = _rows(result)
    qids = sorted(r["query_id"] for r in rows)
    assert qids == ["qA"]


def test_exclude_query_id_overlap_with_query_id_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
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
    assert result.exit_code != 0
    combined = result.output.lower() + str(result.exception or "").lower()
    assert "must not overlap" in combined


def test_exclude_query_id_blank_values_ignored(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--format",
            "json",
            "--exclude-query-id",
            "   ",
        ],
    )
    rows = _rows(result)
    # all four rows survive: blank/whitespace excludes are ignored
    assert len(rows) == 4
