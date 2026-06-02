import json

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app, _filter_feedback


def _rows():
    return [
        {"query_id": "q1", "track_id": "t1", "vote": 1, "score": 0.95, "ts": 10.0},
        {"query_id": "q2", "track_id": "t2", "vote": -1, "score": 0.90, "ts": 20.0},
        {"query_id": "q3", "track_id": "t3", "vote": 1, "score": 0.50, "ts": 30.0},
        {"query_id": "q4", "track_id": "t4", "vote": -1, "score": 0.10, "ts": 40.0},
    ]


def test_filter_feedback_min_score_inclusive():
    out = _filter_feedback(_rows(), min_score=0.9)
    assert [r["track_id"] for r in out] == ["t1", "t2"]


def test_filter_feedback_max_score_inclusive():
    out = _filter_feedback(_rows(), max_score=0.5)
    assert [r["track_id"] for r in out] == ["t3", "t4"]


def test_filter_feedback_min_and_max_score_window():
    out = _filter_feedback(_rows(), min_score=0.4, max_score=0.9)
    assert [r["track_id"] for r in out] == ["t2", "t3"]


def test_filter_feedback_score_filter_drops_non_numeric_scores():
    rows = [
        {"track_id": "ok", "vote": 1, "score": 0.5, "ts": 1.0},
        {"track_id": "missing", "vote": 1, "ts": 2.0},
        {"track_id": "bad", "vote": 1, "score": "nope", "ts": 3.0},
    ]
    assert [r["track_id"] for r in _filter_feedback(rows, min_score=0.0)] == ["ok"]
    assert [r["track_id"] for r in _filter_feedback(rows, max_score=1.0)] == ["ok"]


def test_filter_feedback_combines_with_vote_for_false_positive_lookup():
    # The headline use case: find down-votes on high-confidence matches.
    out = _filter_feedback(_rows(), vote=-1, min_score=0.8)
    assert [r["track_id"] for r in out] == ["t2"]


def _write_feedback_file(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_cli_feedback_list_min_score_flag(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    _write_feedback_file(fb, _rows())

    class _S:
        feedback_path = fb
        metadata_path = tmp_path / "missing-meta.json"

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    runner = CliRunner()
    result = runner.invoke(app, ["feedback-list", "--format", "json", "--min-score", "0.9"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert sorted(r["track_id"] for r in data) == ["t1", "t2"]


def test_cli_feedback_list_rejects_min_greater_than_max(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    _write_feedback_file(fb, [])

    class _S:
        feedback_path = fb
        metadata_path = tmp_path / "missing-meta.json"

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--min-score", "0.9", "--max-score", "0.1"]
    )
    assert result.exit_code != 0
    assert "--min-score must be <= --max-score" in result.output
