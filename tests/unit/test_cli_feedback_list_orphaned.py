"""--orphaned / --in-index filter feedback-list by indexed-library membership.

After pruning the library, a user wants to see the raw votes that now point at
track ids no longer in the index so they can delete them with feedback-delete,
and conversely list only votes against the live catalog.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Track
from clawhum_index.persistence import write_metadata
from clawhum_library.feedback import record_feedback


def _seed(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"
    meta = tmp_path / "metadata.jsonl"

    # t1 and t2 are in the live library; t3 has feedback but was pruned.
    write_metadata(meta, [
        Track(id="t1", title="A", artist="X", path="/x/a.mp3", source="local"),
        Track(id="t2", title="B", artist="Y", path="/x/b.mp3", source="local"),
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t3", 0.5, 1)
    record_feedback(fb, "q4", "t3", 0.6, -1)

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_feedback_list_orphaned_shows_only_pruned_tracks(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--orphaned", "--format", "json", "--limit", "0"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t3"}
    assert len(payload) == 2


def test_feedback_list_in_index_shows_only_live_tracks(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--in-index", "--format", "json", "--limit", "0"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t1", "t2"}
    assert len(payload) == 2


def test_feedback_list_orphaned_and_in_index_are_mutually_exclusive(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--orphaned", "--in-index", "--format", "json"]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_feedback_list_default_includes_all_tracks(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--format", "json", "--limit", "0"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert ids == {"t1", "t2", "t3"}


def test_feedback_list_in_index_auto_enriches(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["feedback-list", "--in-index", "--enrich", "--format", "json", "--limit", "0"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    by_id = {r["track_id"]: r for r in payload}
    assert set(by_id) == {"t1", "t2"}
    assert by_id["t1"]["title"] == "A"
    assert by_id["t2"]["artist"] == "Y"
