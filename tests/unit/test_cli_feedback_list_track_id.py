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
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q1", "t3", 0.6, 1)
    record_feedback(fb, "q1", "t4", 0.7, -1)
    return fb


def _rows(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_track_id_repeatable_scopes_to_shortlist(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--format",
            "json",
            "--track-id",
            "t1",
            "--track-id",
            "t3",
        ],
    )
    rows = _rows(result)
    assert {r["track_id"] for r in rows} == {"t1", "t3"}


def test_track_id_single_value_still_works(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["feedback-list", "--format", "json", "--track-id", "t2"],
    )
    rows = _rows(result)
    assert [r["track_id"] for r in rows] == ["t2"]


def test_track_id_and_exclude_track_overlap_rejected(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--format",
            "json",
            "--track-id",
            "t1",
            "--exclude-track",
            "t1",
        ],
    )
    assert result.exit_code != 0
    msg = (result.output + " " + str(result.exception or "")).lower()
    assert "must not overlap" in msg


def test_track_id_and_exclude_track_compose_when_disjoint(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    runner = CliRunner()
    # Pre-filter to the shortlist {t1, t3} then drop t2 via exclude (disjoint).
    result = runner.invoke(
        app,
        [
            "feedback-list",
            "--format",
            "json",
            "--track-id",
            "t1",
            "--track-id",
            "t3",
            "--exclude-track",
            "t2",
        ],
    )
    rows = _rows(result)
    assert {r["track_id"] for r in rows} == {"t1", "t3"}
