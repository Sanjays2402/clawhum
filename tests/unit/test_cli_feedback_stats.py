import csv
import io
import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app, _aggregate_feedback, _feedback_stats_as_csv


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # t1: 2 up, 0 down (net +2)
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    # t2: 1 up, 2 down (net -1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t2", 0.5, 1)
    record_feedback(fb, "q4", "t2", 0.3, -1)
    # t3: 0 up, 1 down (net -1)
    record_feedback(fb, "q5", "t3", 0.2, -1)
    return fb


def test_aggregate_feedback_counts_and_avg():
    rows = [
        {"track_id": "t1", "vote": 1, "score": 1.0},
        {"track_id": "t1", "vote": 1, "score": 0.5},
        {"track_id": "t1", "vote": -1, "score": 0.0},
        {"track_id": "t2", "vote": -1, "score": 0.2},
    ]
    out = {r["track_id"]: r for r in _aggregate_feedback(rows)}
    assert out["t1"]["up"] == 2
    assert out["t1"]["down"] == 1
    assert out["t1"]["total"] == 3
    assert out["t1"]["net"] == 1
    assert abs(out["t1"]["avg_score"] - 0.5) < 1e-9
    assert out["t2"]["net"] == -1


def test_aggregate_feedback_ignores_rows_without_track():
    rows = [{"vote": 1, "score": 0.5}, {"track_id": "t1", "vote": 1, "score": 0.5}]
    out = _aggregate_feedback(rows)
    assert len(out) == 1 and out[0]["track_id"] == "t1"


def test_feedback_stats_as_csv_formats_avg_and_blanks():
    rows = [
        {"track_id": "t1", "up": 1, "down": 0, "total": 1, "net": 1, "avg_score": 0.5},
        {"track_id": "t2", "up": 0, "down": 0, "total": 0, "net": 0, "avg_score": None},
    ]
    payload = _feedback_stats_as_csv(rows)
    reader = list(csv.DictReader(io.StringIO(payload)))
    assert reader[0]["avg_score"] == "0.500000"
    assert reader[1]["avg_score"] == ""


def test_feedback_stats_json_sorted_by_net(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    nets = [row["net"] for row in rows]
    assert nets == sorted(nets, reverse=True)
    assert rows[0]["track_id"] == "t1"


def test_feedback_stats_min_total_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--min-total", "2"])
    assert r.exit_code == 0
    rows = json.loads(r.stdout)
    ids = {row["track_id"] for row in rows}
    assert ids == {"t1", "t2"}  # t3 has only 1 vote


def test_feedback_stats_track_id_filter_and_limit(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--track-id", "t2"])
    assert r.exit_code == 0
    rows = json.loads(r.stdout)
    assert len(rows) == 1 and rows[0]["track_id"] == "t2" and rows[0]["total"] == 3

    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--limit", "1"])
    assert r.exit_code == 0
    assert len(json.loads(r.stdout)) == 1


def test_feedback_stats_rejects_bad_sort(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--sort", "nope"])
    assert r.exit_code != 0


def test_feedback_stats_sort_by_avg_score(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # t_high: avg 0.9, t_mid: avg 0.5, t_low: avg 0.1
    record_feedback(fb, "q1", "t_mid", 0.5, 1)
    record_feedback(fb, "q2", "t_high", 0.9, 1)
    record_feedback(fb, "q3", "t_low", 0.1, -1)

    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--sort", "avg_score"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    ids = [row["track_id"] for row in rows]
    assert ids == ["t_high", "t_mid", "t_low"]


def test_feedback_stats_csv_to_file(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    out = tmp_path / "stats.csv"
    r = runner.invoke(app, ["feedback-stats", "--format", "csv", "--output", str(out)])
    assert r.exit_code == 0
    text = out.read_text()
    assert "track_id,up,down,total,net,avg_score" in text.splitlines()[0]
    assert "t1" in text


def test_feedback_stats_empty(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout) == []
