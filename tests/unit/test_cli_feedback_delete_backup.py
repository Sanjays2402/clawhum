import json

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_library.feedback import read_feedback, record_feedback


def _patch_settings(tmp_path, monkeypatch):
    fb = tmp_path / "feedback.jsonl"

    class _S:
        feedback_path = str(fb)

    monkeypatch.setattr("clawhum_core.settings.get_settings", lambda: _S())
    monkeypatch.setattr("cli.clawhum_cli.main.get_settings", lambda: _S())
    return fb


def _seed(fb):
    record_feedback(fb, "q1", "t1", 0.9, 1)
    record_feedback(fb, "q1", "t2", 0.4, -1)
    record_feedback(fb, "q2", "t1", 0.8, 1)
    record_feedback(fb, "q3", "t1", 0.7, -1)


def test_backup_writes_matched_rows_before_delete(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    backup = tmp_path / "deleted.jsonl"
    r = CliRunner().invoke(
        app,
        ["feedback-delete", "--query-id", "q1", "--backup", str(backup), "--yes"],
    )
    assert r.exit_code == 0, r.output
    assert backup.exists()
    saved = [json.loads(line) for line in backup.read_text().splitlines() if line.strip()]
    assert len(saved) == 2
    assert {row["track_id"] for row in saved} == {"t1", "t2"}
    assert all(row["query_id"] == "q1" for row in saved)
    # Original log lost q1 rows
    remaining = read_feedback(fb)
    assert {row["query_id"] for row in remaining} == {"q2", "q3"}


def test_backup_with_dry_run_writes_preview_but_keeps_log(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    backup = tmp_path / "preview.jsonl"
    r = CliRunner().invoke(
        app,
        ["feedback-delete", "--vote", "-1", "--backup", str(backup), "--dry-run"],
    )
    assert r.exit_code == 0, r.output
    saved = [json.loads(line) for line in backup.read_text().splitlines() if line.strip()]
    assert len(saved) == 2
    assert all(row["vote"] == -1 for row in saved)
    # Log untouched
    assert len(read_feedback(fb)) == 4


def test_backup_refuses_to_clobber_existing_file(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    backup = tmp_path / "exists.jsonl"
    backup.write_text("preexisting\n")
    r = CliRunner().invoke(
        app,
        ["feedback-delete", "--query-id", "q1", "--backup", str(backup), "--yes"],
    )
    assert r.exit_code != 0
    # Log untouched, file untouched
    assert backup.read_text() == "preexisting\n"
    assert len(read_feedback(fb)) == 4


def test_backup_overwrite_allows_replacing_existing_file(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    backup = tmp_path / "exists.jsonl"
    backup.write_text("preexisting\n")
    r = CliRunner().invoke(
        app,
        [
            "feedback-delete",
            "--query-id",
            "q1",
            "--backup",
            str(backup),
            "--backup-overwrite",
            "--yes",
        ],
    )
    assert r.exit_code == 0, r.output
    saved = [json.loads(line) for line in backup.read_text().splitlines() if line.strip()]
    assert len(saved) == 2
    assert {row["track_id"] for row in saved} == {"t1", "t2"}


def test_backup_creates_parent_directories(tmp_path, monkeypatch):
    fb = _patch_settings(tmp_path, monkeypatch)
    _seed(fb)
    backup = tmp_path / "nested" / "deep" / "backup.jsonl"
    r = CliRunner().invoke(
        app,
        ["feedback-delete", "--query-id", "q2", "--backup", str(backup), "--yes"],
    )
    assert r.exit_code == 0, r.output
    assert backup.exists()
    saved = [json.loads(line) for line in backup.read_text().splitlines() if line.strip()]
    assert len(saved) == 1
    assert saved[0]["query_id"] == "q2"
