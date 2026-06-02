"""`clawhum feedback-stats --fail-on-empty` (or `-E`) must exit non-zero
(code 2) when no rows survive the filters, so monitoring scripts and CI
pipelines can distinguish a silent empty result from a successful no-op.

Mirrors the contract already shipped on `clawhum match --fail-on-empty`.
Without the flag the existing exit-zero behavior is preserved so
interactive use stays unchanged across every format (table/json/csv).
"""

import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


def _empty_settings(tmp_path, monkeypatch):
    class _S:
        feedback_path = str(tmp_path / "missing.jsonl")

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())


def _seed_one_track(tmp_path, monkeypatch):
    from clawhum_library.feedback import record_feedback

    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    record_feedback(fb, "q1", "t1", 0.9, 1)
    return fb


def test_feedback_stats_empty_default_exits_zero(tmp_path, monkeypatch):
    _empty_settings(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["feedback-stats"])
    assert r.exit_code == 0, r.output


def test_feedback_stats_fail_on_empty_table_exits_two(tmp_path, monkeypatch):
    _empty_settings(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["feedback-stats", "--fail-on-empty"])
    assert r.exit_code == 2, r.output
    assert "no feedback" in r.output


def test_feedback_stats_fail_on_empty_short_flag(tmp_path, monkeypatch):
    _empty_settings(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["feedback-stats", "-E"])
    assert r.exit_code == 2, r.output


def test_feedback_stats_fail_on_empty_json_still_emits_valid_payload(tmp_path, monkeypatch):
    _empty_settings(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["feedback-stats", "--format", "json", "-E"])
    assert r.exit_code == 2, r.output
    # Empty JSON array must still land on stdout so downstream consumers
    # see a well-formed response even on the miss path.
    assert "[]" in r.stdout
    assert json.loads(r.stdout.strip().splitlines()[-1]) == []


def test_feedback_stats_fail_on_empty_csv_still_emits_header(tmp_path, monkeypatch):
    _empty_settings(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["feedback-stats", "--format", "csv", "-E"])
    assert r.exit_code == 2, r.output
    # CSV header row must still print so a consumer parsing the stream
    # always sees a schema, even when the body is empty.
    assert "track_id" in r.stdout


def test_feedback_stats_fail_on_empty_does_not_fire_when_rows_present(tmp_path, monkeypatch):
    _seed_one_track(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["feedback-stats", "-E"])
    assert r.exit_code == 0, r.output
    assert "t1" in r.output


def test_feedback_stats_fail_on_empty_fires_when_filter_drops_everything(tmp_path, monkeypatch):
    _seed_one_track(tmp_path, monkeypatch)
    # The seeded track has 1 vote; --min-total 5 filters it out.
    r = CliRunner().invoke(app, ["feedback-stats", "--min-total", "5", "-E"])
    assert r.exit_code == 2, r.output
