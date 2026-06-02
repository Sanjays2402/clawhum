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
    record_feedback(fb, "qC", "t3", 0.1, -1)
    return fb


def _rows(result):
    assert result.exit_code == 0, result.output
    return {r["track_id"]: r for r in json.loads(result.output)}


def test_query_id_includes_only_listed_sessions(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-stats", "--format", "json", "--query-id", "qA", "--query-id", "qB"]
    )
    out = _rows(result)
    # t1 has +1 (qA) and -1 (qB) only, qC excluded
    assert out["t1"]["up"] == 1
    assert out["t1"]["down"] == 1
    assert out["t1"]["total"] == 2
    # t3 only in qB
    assert out["t3"]["up"] == 1
    assert out["t3"]["down"] == 0


def test_exclude_query_id_drops_session(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-stats", "--format", "json", "--exclude-query-id", "qC"]
    )
    out = _rows(result)
    # qC contributed 3 down-votes; excluding it changes totals
    assert out["t1"]["total"] == 2
    assert out["t1"]["down"] == 1
    assert out["t2"]["total"] == 1
    assert out["t3"]["total"] == 1


def test_query_id_and_exclude_query_id_overlap_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-stats",
            "--format",
            "json",
            "--query-id",
            "qA",
            "--exclude-query-id",
            "qA",
        ],
    )
    assert result.exit_code != 0
    assert "must not overlap" in result.output.lower() or "must not overlap" in str(
        result.exception or ""
    )
