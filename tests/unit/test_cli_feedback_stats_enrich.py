"""--enrich joins feedback-stats with the indexed library to show
title / artist next to each track_id."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Track
from clawhum_index.persistence import write_metadata
from clawhum_library.feedback import record_feedback


def _seed(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    meta = tmp_path / "metadata.jsonl"

    write_metadata(meta, [
        Track(id="t1", title="Bohemian Rhapsody", artist="Queen", path="/x/a.mp3", source="local"),
        Track(id="t2", title="Imagine", artist="John Lennon", path="/x/b.mp3", source="local"),
        # t3 intentionally omitted to prove missing tracks degrade to blanks
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    record_feedback(fb, "q3", "t2", 0.4, -1)
    record_feedback(fb, "q4", "t3", 0.5, 1)  # unknown track

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_feedback_stats_enrich_json_adds_title_and_artist(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--enrich", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    by_id = {r["track_id"]: r for r in payload}
    assert by_id["t1"]["title"] == "Bohemian Rhapsody"
    assert by_id["t1"]["artist"] == "Queen"
    assert by_id["t2"]["title"] == "Imagine"
    # Unknown track stays in the output with blank metadata, not a crash.
    assert by_id["t3"]["title"] == ""
    assert by_id["t3"]["artist"] == ""


def test_feedback_stats_enrich_csv_includes_columns(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--enrich", "--format", "csv"])
    assert result.exit_code == 0, result.output
    reader = list(csv.DictReader(io.StringIO(result.stdout)))
    assert reader, "expected at least one row"
    assert "title" in reader[0] and "artist" in reader[0]
    by_id = {r["track_id"]: r for r in reader}
    assert by_id["t1"]["title"] == "Bohemian Rhapsody"
    assert by_id["t1"]["artist"] == "Queen"
    assert by_id["t3"]["title"] == ""


def test_feedback_stats_without_enrich_omits_columns(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for r in payload:
        assert "title" not in r
        assert "artist" not in r


def test_feedback_stats_enrich_table_renders_human_columns(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    # Widen the rich console so titles aren't truncated by the default 80-col terminal.
    from rich.console import Console
    import cli.clawhum_cli.main as cli_main
    monkeypatch.setattr(cli_main, "console", Console(width=200, force_terminal=False))
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--enrich", "--format", "table"])
    assert result.exit_code == 0, result.output
    assert "Bohemian Rhapsody" in result.stdout
    assert "Queen" in result.stdout
