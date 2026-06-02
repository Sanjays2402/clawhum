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


def test_feedback_stats_min_up_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # t1 has 2 up, t2 has 1 up, t3 has 0 up
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--min-up", "2"])
    assert r.exit_code == 0
    ids = {row["track_id"] for row in json.loads(r.stdout)}
    assert ids == {"t1"}


def test_feedback_stats_min_down_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # t1 has 0 down, t2 has 2 down, t3 has 1 down
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--min-down", "2"])
    assert r.exit_code == 0
    ids = {row["track_id"] for row in json.loads(r.stdout)}
    assert ids == {"t2"}


def test_feedback_stats_rejects_negative_min_up_min_down(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--min-up", "-1"])
    assert r.exit_code != 0
    assert "--min-up must be >= 0" in (r.stdout + (r.stderr or ""))
    r = runner.invoke(app, ["feedback-stats", "--min-down", "-1"])
    assert r.exit_code != 0
    assert "--min-down must be >= 0" in (r.stdout + (r.stderr or ""))


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
    assert "track_id,up,down,total,net,avg_score,approval" in text.splitlines()[0]
    assert "t1" in text


def test_aggregate_feedback_approval_ratio():
    rows = [
        {"track_id": "t1", "vote": 1, "score": 0.9},
        {"track_id": "t1", "vote": 1, "score": 0.8},
        {"track_id": "t1", "vote": -1, "score": 0.1},
        {"track_id": "t2", "vote": -1, "score": 0.2},
    ]
    out = {r["track_id"]: r for r in _aggregate_feedback(rows)}
    assert abs(out["t1"]["approval"] - (2 / 3)) < 1e-9
    assert out["t2"]["approval"] == 0.0


def test_feedback_stats_sort_by_approval(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # t_best: 3/3 = 1.0, t_mid: 2/4 = 0.5, t_worst: 0/2 = 0.0
    for q in ("q1", "q2", "q3"):
        record_feedback(fb, q, "t_best", 0.9, 1)
    record_feedback(fb, "q4", "t_mid", 0.5, 1)
    record_feedback(fb, "q5", "t_mid", 0.5, 1)
    record_feedback(fb, "q6", "t_mid", 0.4, -1)
    record_feedback(fb, "q7", "t_mid", 0.4, -1)
    record_feedback(fb, "q8", "t_worst", 0.1, -1)
    record_feedback(fb, "q9", "t_worst", 0.1, -1)

    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--sort", "approval"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert [row["track_id"] for row in rows] == ["t_best", "t_mid", "t_worst"]
    assert rows[0]["approval"] == 1.0
    assert abs(rows[1]["approval"] - 0.5) < 1e-9
    assert rows[2]["approval"] == 0.0


def test_feedback_stats_min_approval_filter(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    for q in ("q1", "q2"):
        record_feedback(fb, q, "t_good", 0.9, 1)
    record_feedback(fb, "q3", "t_bad", 0.4, -1)
    record_feedback(fb, "q4", "t_bad", 0.4, 1)  # 0.5

    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--min-approval", "0.75"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    ids = [row["track_id"] for row in rows]
    assert ids == ["t_good"]


def test_feedback_stats_min_approval_rejects_out_of_range(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--min-approval", "1.5"])
    assert r.exit_code != 0


def test_feedback_stats_min_avg_score_filters(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # t1 avg=0.85, t2 avg=0.4, t3 avg=0.2 (from _seed)
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--min-avg-score", "0.5"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    ids = {row["track_id"] for row in rows}
    assert ids == {"t1"}


def test_feedback_stats_min_avg_score_rejects_out_of_range(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--min-avg-score", "1.5"])
    assert r.exit_code != 0


def test_feedback_stats_empty(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout) == []


def test_feedback_stats_max_approval_filter(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    # t_good: 2 up (approval 1.0)
    record_feedback(fb, "q1", "t_good", 0.9, 1)
    record_feedback(fb, "q2", "t_good", 0.8, 1)
    # t_mid: 1 up 1 down (approval 0.5)
    record_feedback(fb, "q3", "t_mid", 0.5, 1)
    record_feedback(fb, "q4", "t_mid", 0.4, -1)
    # t_bad: 2 down (approval 0.0)
    record_feedback(fb, "q5", "t_bad", 0.2, -1)
    record_feedback(fb, "q6", "t_bad", 0.1, -1)

    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--max-approval", "0.5"])
    assert r.exit_code == 0, r.output
    ids = {row["track_id"] for row in json.loads(r.stdout)}
    assert ids == {"t_mid", "t_bad"}


def test_feedback_stats_max_approval_rejects_out_of_range(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--max-approval", "1.5"])
    assert r.exit_code != 0


def test_feedback_stats_min_max_approval_inverted_rejected(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(
        app, ["feedback-stats", "--min-approval", "0.8", "--max-approval", "0.2"]
    )
    assert r.exit_code != 0


def test_feedback_stats_max_avg_score_filters(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # t1 avg=0.85, t2 avg=0.4, t3 avg=0.2 (from _seed)
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--max-avg-score", "0.45"])
    assert r.exit_code == 0, r.output
    ids = {row["track_id"] for row in json.loads(r.stdout)}
    assert ids == {"t2", "t3"}


def test_feedback_stats_max_avg_score_rejects_out_of_range(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--max-avg-score", "-0.1"])
    assert r.exit_code != 0


def test_feedback_stats_min_max_avg_score_inverted_rejected(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(
        app, ["feedback-stats", "--min-avg-score", "0.8", "--max-avg-score", "0.2"]
    )
    assert r.exit_code != 0


def test_feedback_stats_max_net_surfaces_rejected_tracks(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # nets from _seed: t1 +2, t2 -1, t3 -1
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--max-net", "-1"])
    assert r.exit_code == 0, r.output
    ids = {row["track_id"] for row in json.loads(r.stdout)}
    assert ids == {"t2", "t3"}


def test_feedback_stats_min_net_filters_positive_consensus(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--format", "json", "--min-net", "1"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert [row["track_id"] for row in rows] == ["t1"]


def test_feedback_stats_min_max_net_inverted_rejected(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-stats", "--min-net", "2", "--max-net", "0"])
    assert r.exit_code != 0
