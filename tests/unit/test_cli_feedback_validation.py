from __future__ import annotations

import math

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import read_feedback


def _setup(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def test_feedback_rejects_invalid_vote(tmp_path, monkeypatch):
    fb = _setup(tmp_path, monkeypatch)
    runner = CliRunner()
    for bad in ("0", "2", "-2", "5"):
        result = runner.invoke(app, ["feedback", "q1", "t1", "0.9", "--", bad])
        assert result.exit_code != 0, f"vote={bad} should be rejected"
    assert read_feedback(fb) == []


def test_feedback_rejects_empty_ids(tmp_path, monkeypatch):
    fb = _setup(tmp_path, monkeypatch)
    runner = CliRunner()
    r1 = runner.invoke(app, ["feedback", "", "t1", "0.9", "1"])
    assert r1.exit_code != 0
    r2 = runner.invoke(app, ["feedback", "q1", "   ", "0.9", "1"])
    assert r2.exit_code != 0
    assert read_feedback(fb) == []


def test_feedback_rejects_non_finite_score(tmp_path, monkeypatch):
    fb = _setup(tmp_path, monkeypatch)
    runner = CliRunner()
    r1 = runner.invoke(app, ["feedback", "q1", "t1", "nan", "1"])
    assert r1.exit_code != 0
    r2 = runner.invoke(app, ["feedback", "q1", "t1", "inf", "1"])
    assert r2.exit_code != 0


def test_feedback_accepts_valid_votes(tmp_path, monkeypatch):
    fb = _setup(tmp_path, monkeypatch)
    runner = CliRunner()
    assert runner.invoke(app, ["feedback", "q1", "t1", "0.9", "1"]).exit_code == 0
    assert runner.invoke(app, ["feedback", "q1", "t2", "0.4", "--", "-1"]).exit_code == 0
    rows = read_feedback(fb)
    assert len(rows) == 2
    assert {r["vote"] for r in rows} == {1, -1}
    for r in rows:
        assert math.isfinite(r["score"])
