"""--title / --artist substring filters on feedback-list let users find
their votes by song name without first knowing the internal track_id."""
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
        # t4 omitted so we can check that orphaned tracks drop out of title/artist filters.
    ])

    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q2", "t2", 0.4, -1)
    record_feedback(fb, "q3", "t3", 0.8, 1)
    record_feedback(fb, "q4", "t4", 0.7, 1)  # orphan: no metadata row

    class _S:
        feedback_path = str(fb)
        metadata_path = str(meta)

    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb, meta


def test_title_filter_matches_case_insensitive_substring(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-list", "--title", "rhapsody", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [r["track_id"] for r in payload] == ["t1"]
    # auto-enrich: title/artist appear without --enrich on the command line.
    assert payload[0]["title"] == "Bohemian Rhapsody"
    assert payload[0]["artist"] == "Queen"


def test_artist_filter_matches_case_insensitive_substring(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-list", "--artist", "COLDPLAY", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [r["track_id"] for r in payload] == ["t3"]


def test_title_and_artist_filters_combine_with_and(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # title matches t1 and t3, artist narrows to t3 only.
    result = runner.invoke(
        app,
        ["feedback-list", "--title", "o", "--artist", "coldplay", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [r["track_id"] for r in payload] == ["t3"]


def test_title_filter_excludes_tracks_missing_from_index(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # No metadata at all matches an empty title needle, but orphans (t4)
    # must still be dropped because we can't prove they match.
    result = runner.invoke(app, ["feedback-list", "--title", "", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    ids = {r["track_id"] for r in payload}
    assert "t4" not in ids
    assert ids == {"t1", "t2", "t3"}


def test_no_title_or_artist_keeps_existing_behaviour(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["feedback-list", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # Orphan t4 still present, no title/artist columns added.
    assert {r["track_id"] for r in payload} == {"t1", "t2", "t3", "t4"}
    for r in payload:
        assert "title" not in r
        assert "artist" not in r
