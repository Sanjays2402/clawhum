import csv
import io
import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app, _feedback_as_csv, _filter_feedback


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


def test_filter_feedback_by_query_track_and_vote():
    rows = [
        {"query_id": "q1", "track_id": "t1", "vote": 1, "score": 0.9, "ts": 1.0},
        {"query_id": "q1", "track_id": "t2", "vote": -1, "score": 0.4, "ts": 2.0},
        {"query_id": "q2", "track_id": "t1", "vote": 1, "score": 0.8, "ts": 3.0},
    ]
    assert len(_filter_feedback(rows, query_id="q1")) == 2
    assert len(_filter_feedback(rows, track_id="t1")) == 2
    assert len(_filter_feedback(rows, vote=-1)) == 1
    assert _filter_feedback(rows, query_id="q1", track_id="t1", vote=1) == [rows[0]]


def test_feedback_as_csv_header_and_rows():
    rows = [
        {"ts": 1.5, "query_id": "q1", "track_id": "t1", "score": 0.9, "vote": 1},
        {"ts": 2.0, "query_id": "q1", "track_id": "t2", "score": 0.4, "vote": -1, "tenant_id": "x"},
    ]
    payload = _feedback_as_csv(rows)
    reader = list(csv.DictReader(io.StringIO(payload)))
    assert reader[0]["query_id"] == "q1"
    assert reader[1]["vote"] == "-1"
    # tenant_id is not in the column set
    assert "tenant_id" not in reader[0]


def test_feedback_list_json_orders_newest_first(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-list", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 3
    # newest first by ts
    timestamps = [r["ts"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_feedback_list_filters_and_limit(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json", "--query-id", "q1"])
    assert r.exit_code == 0
    assert {row["query_id"] for row in json.loads(r.stdout)} == {"q1"}

    r = runner.invoke(app, ["feedback-list", "--format", "json", "--vote", "-1"])
    assert r.exit_code == 0
    rows = json.loads(r.stdout)
    assert len(rows) == 1 and rows[0]["vote"] == -1

    r = runner.invoke(app, ["feedback-list", "--format", "json", "--limit", "1"])
    assert r.exit_code == 0
    assert len(json.loads(r.stdout)) == 1


def test_feedback_list_rejects_bad_vote(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--vote", "2"])
    assert r.exit_code != 0


def test_feedback_list_empty(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--format", "json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout) == []
