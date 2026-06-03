"""`clawhum feedback-delete --exclude-track` / `--exclude-query-id` act as a
denylist on top of a positive filter so a bulk purge can skip tracks or
sessions the operator still wants to keep. Repeatable; whitespace-trimmed;
blank entries ignored; unknown ids are a no-op; overlap with the
corresponding positive filter is rejected."""

import pytest
from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import (
    delete_feedback,
    read_feedback,
    record_feedback,
)


def _patch_settings(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def _seed(fb):
    # 3 down-votes across 3 tracks, 1 up-vote on t1
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q2", "t1", 0.2, -1)
    record_feedback(fb, "q3", "t3", 0.1, -1)


def test_exclude_track_preserves_named_track(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, vote=-1, exclude_track_ids=["t1"])
    # t1's down-vote survives; t2 and t3 down-votes go
    assert removed == 2
    remaining = read_feedback(fb)
    assert {(r["query_id"], r["track_id"]) for r in remaining} == {
        ("q1", "t1"),
        ("q2", "t1"),
    }


def test_exclude_query_id_preserves_named_session(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, vote=-1, exclude_query_ids=["q1"])
    # q1's down-vote (on t2) survives; q2 and q3 down-votes go
    assert removed == 2
    remaining = read_feedback(fb)
    assert {(r["query_id"], r["track_id"]) for r in remaining} == {
        ("q1", "t1"),
        ("q1", "t2"),
    }


def test_exclude_track_empty_set_is_noop(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    removed = delete_feedback(fb, vote=-1, exclude_track_ids=[])
    assert removed == 3
    assert len(read_feedback(fb)) == 1


def test_cli_exclude_track_is_repeatable(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote",
            "-1",
            "--exclude-track",
            "t1",
            "--exclude-track",
            "t2",
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "deleted 1" in r.output
    remaining = read_feedback(fb)
    assert {r["track_id"] for r in remaining} == {"t1", "t2"}


def test_cli_exclude_query_id_is_repeatable(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote",
            "-1",
            "--exclude-query-id",
            "q1",
            "--exclude-query-id",
            "q3",
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    # only q2's down-vote on t1 should be deleted
    assert "deleted 1" in r.output
    remaining = read_feedback(fb)
    assert {(r["query_id"], r["track_id"]) for r in remaining} == {
        ("q1", "t1"),
        ("q1", "t2"),
        ("q3", "t3"),
    }


def test_cli_exclude_track_trims_and_ignores_blanks(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote",
            "-1",
            "--exclude-track",
            "  t1  ",
            "--exclude-track",
            "",
            "--exclude-track",
            "   ",
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    # t1 down-vote preserved; t2 + t3 down-votes deleted
    assert "deleted 2" in r.output
    remaining = read_feedback(fb)
    assert {(r["query_id"], r["track_id"]) for r in remaining} == {
        ("q1", "t1"),
        ("q2", "t1"),
    }


def test_cli_exclude_unknown_track_is_noop(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    r = runner.invoke(
        app,
        [
            "feedback-delete",
            "--vote",
            "-1",
            "--exclude-track",
            "nope",
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "deleted 3" in r.output
    assert len(read_feedback(fb)) == 1


def test_cli_exclude_track_cannot_stand_alone(tmp_path, monkeypatch):
    """--exclude-track without a positive filter is rejected so a bare
    `feedback-delete --exclude-track foo` can never wipe everything else."""
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    r = runner.invoke(app, ["feedback-delete", "--exclude-track", "t1"])
    assert r.exit_code != 0
    assert len(read_feedback(fb)) == 4


def test_cli_exclude_overlap_with_positive_filter_rejected(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["feedback-delete", "--track-id", "t1", "--exclude-track", "t1", "--yes"],
    )
    assert r.exit_code != 0
    assert len(read_feedback(fb)) == 4

    r2 = runner.invoke(
        app,
        ["feedback-delete", "--query-id", "q1", "--exclude-query-id", "q1", "--yes"],
    )
    assert r2.exit_code != 0
    assert len(read_feedback(fb)) == 4


def test_delete_feedback_requires_a_filter_even_with_excludes(tmp_path):
    """Excludes alone are not a filter; the no-positive-filter guard still
    fires so a misuse can never wipe everything not in the denylist."""
    fb = tmp_path / "feedback.jsonl"
    _seed(fb)
    with pytest.raises(ValueError):
        delete_feedback(fb, exclude_track_ids=["t1"])
    with pytest.raises(ValueError):
        delete_feedback(fb, exclude_query_ids=["q1"])
    assert len(read_feedback(fb)) == 4
