"""--title / --artist substring filters on feedback-stats let users aggregate
votes by song name without first knowing the internal track_id, matching the
parity already shipped on feedback-list."""
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

    write_metadata(meta, [
        Track(id="t1", title="Bohemian Rhapsody", artist="Queen", path="/x/a.mp3", source="local"),
        Track(id="t2", title="Imagine", artist="John Lennon", path="/x/b.mp3", source="local"),
        Track(id="t3", title="Yellow", artist="Coldplay", path="/x/c.mp3", source="local"),
        # t4 omitted so we can check orphans drop out of name filters.
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    record_feedback(fb, "q3", "t2", 0.4, -1)
    record_feedback(fb, "q4", "t3", 0.8, 1)
    record_feedback(fb, "q5", "t4", 0.7, 1)  # orphan: no metadata row

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_title_filter_matches_case_insensitive_substring(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--title", "rhapsody", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [r["track_id"] for r in payload] == ["t1"]
    # auto-enrich: title/artist appear without --enrich on the command line.
    assert payload[0]["title"] == "Bohemian Rhapsody"
    assert payload[0]["artist"] == "Queen"
    assert payload[0]["up"] == 2


def test_artist_filter_matches_case_insensitive_substring(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-stats", "--artist", "COLDPLAY", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [r["track_id"] for r in payload] == ["t3"]


def test_title_and_artist_filters_combine_with_and(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # title 'o' matches Bohemian, Yellow, John Lennon's Imagine (no 'o' in
    # 'Imagine'); narrow by artist coldplay -> t3 only.
    result = runner.invoke(
        app,
        ["feedback-stats", "--title", "o", "--artist", "coldplay", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [r["track_id"] for r in payload] == ["t3"]


def test_title_filter_excludes_orphan_tracks(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # Empty needle matches every metadata title but orphans (t4) must drop:
    # we can't prove a missing track matches the needle.
    result = runner.invoke(app, ["feedback-stats", "--title", "", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert "t4" not in ids
    assert {"t1", "t2", "t3"}.issubset(ids)
