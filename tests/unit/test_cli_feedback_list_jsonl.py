import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    return fb


def test_feedback_list_jsonl_emits_one_object_per_line(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-list", "--format", "jsonl"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 3
    rows = [json.loads(ln) for ln in lines]
    # newest first by ts (same ordering contract as --format json)
    timestamps = [r["ts"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)
    # each line is a stand-alone object, not wrapped in a list
    for ln in lines:
        assert ln.startswith("{") and ln.endswith("}")


def test_feedback_list_jsonl_extension_inferred(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    out = tmp_path / "out.jsonl"
    result = runner.invoke(app, ["feedback-list", "--output", str(out)])
    assert result.exit_code == 0, result.output
    body = out.read_text(encoding="utf-8")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(lines) == 3
    for ln in lines:
        json.loads(ln)  # each line parses standalone
    # must not be a single JSON array
    assert not body.lstrip().startswith("[")


def test_feedback_list_ndjson_extension_inferred(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    out = tmp_path / "out.ndjson"
    result = runner.invoke(app, ["feedback-list", "--output", str(out)])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    for ln in lines:
        json.loads(ln)
