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

    # recorded oldest first; record_feedback stamps ts at write time
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q2", "t3", 0.7, 1)
    return fb


def _scores(rows):
    return [r["score"] for r in rows]


def _track_ids(rows):
    return [r["track_id"] for r in rows]


def test_feedback_list_sort_score_desc(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--sort", "score"])
    assert r.exit_code == 0, r.output
    assert _scores(json.loads(r.stdout)) == [0.9, 0.7, 0.4]


def test_feedback_list_sort_score_asc(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--sort", "score-asc"])
    assert r.exit_code == 0, r.output
    assert _scores(json.loads(r.stdout)) == [0.4, 0.7, 0.9]


def test_feedback_list_sort_ts_asc(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--sort", "ts-asc"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    ts = [row["ts"] for row in rows]
    assert ts == sorted(ts)


def test_feedback_list_sort_track_id(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--sort", "track_id"])
    assert r.exit_code == 0, r.output
    assert _track_ids(json.loads(r.stdout)) == ["t1", "t2", "t3"]


def test_feedback_list_sort_invalid(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--sort", "bogus"])
    assert r.exit_code != 0
    assert "--sort" in (r.output + (r.stderr or ""))


def test_feedback_list_sort_score_puts_unscored_last(tmp_path, monkeypatch):
    fb = _seed(tmp_path, monkeypatch)
    # append a row with no numeric score
    with open(fb, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"query_id": "q3", "track_id": "t9", "vote": 1, "score": None, "ts": 9999.0}) + "\n")
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--sort", "score"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert rows[-1]["track_id"] == "t9"
