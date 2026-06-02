"""`clawhum feedback-list --exclude-track <id>` drops entries for the given
track_id from the listing so the user can look past a known-noisy track (e.g.
a duplicate edition that dominates recent votes) without re-querying.
Repeatable; whitespace-trimmed; unknown ids are a no-op."""

import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _seed(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    record_feedback(fb, "q3", "t2", 0.4, -1)
    record_feedback(fb, "q4", "t2", 0.5, 1)
    record_feedback(fb, "q5", "t3", 0.2, -1)
    return fb


def _ids(out: str) -> list[str]:
    return [r["track_id"] for r in json.loads(out)]


def test_exclude_track_drops_named_track(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-list", "--exclude-track", "t1", "-f", "json"])
    assert r.exit_code == 0, r.output
    ids = _ids(r.output)
    assert "t1" not in ids
    assert set(ids) == {"t2", "t3"}


def test_exclude_track_is_repeatable(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-list", "--exclude-track", "t1", "-x", "t3", "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    assert set(_ids(r.output)) == {"t2"}


def test_exclude_track_trims_whitespace(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app, ["feedback-list", "--exclude-track", "  t1  ", "-f", "json"]
    )
    assert r.exit_code == 0, r.output
    assert "t1" not in _ids(r.output)


def test_exclude_unknown_track_is_noop(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app, ["feedback-list", "--exclude-track", "nope", "-f", "json"]
    )
    assert r.exit_code == 0, r.output
    assert set(_ids(r.output)) == {"t1", "t2", "t3"}


def test_exclude_blank_entries_are_ignored(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-list", "--exclude-track", "", "--exclude-track", "   ", "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    assert set(_ids(r.output)) == {"t1", "t2", "t3"}


def test_exclude_track_composes_with_vote_filter(tmp_path, monkeypatch):
    """Excluding t1 plus --vote -1 must leave only the down-votes on other tracks."""
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-list", "--exclude-track", "t1", "--vote", "-1", "-f", "json"],
    )
    assert r.exit_code == 0, r.output
    rows = json.loads(r.output)
    assert {row["track_id"] for row in rows} == {"t2", "t3"}
    assert all(row["vote"] == -1 for row in rows)
