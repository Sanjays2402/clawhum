"""Tests for in process audit log rotation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from clawhum_api.audit import rotate_if_needed, write_event
from clawhum_core.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    # Settings is lru_cached; reset around each test so env overrides apply.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _write_lines(path: Path, n: int, payload: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        for i in range(n):
            f.write(f'{{"i":{i},"p":"{payload}"}}\n'.encode())


def test_rotate_if_needed_below_threshold_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_lines(path, 5)
    rotated = rotate_if_needed(path, max_bytes=1_000_000, backup_count=3)
    assert rotated is False
    assert path.exists()
    assert not (tmp_path / "audit.jsonl.1").exists()


def test_rotate_if_needed_disabled_when_max_bytes_zero(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    _write_lines(path, 100, payload="y" * 100)
    rotated = rotate_if_needed(path, max_bytes=0, backup_count=3)
    assert rotated is False
    assert path.exists()


def test_rotate_shifts_backups_and_drops_oldest(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    # Seed prior rotations so we exercise the shift loop and oldest drop.
    (tmp_path / "audit.jsonl.1").write_text("old1\n")
    (tmp_path / "audit.jsonl.2").write_text("old2\n")
    (tmp_path / "audit.jsonl.3").write_text("old3\n")
    _write_lines(path, 100, payload="z" * 200)

    rotated = rotate_if_needed(path, max_bytes=1024, backup_count=3)
    assert rotated is True
    # Active file is gone (renamed to .1).
    assert not path.exists()
    # .1 is the freshly rotated big file.
    rotated_one = (tmp_path / "audit.jsonl.1").read_text()
    assert '"i":0' in rotated_one
    # Previous .1 was shifted to .2, .2 -> .3.
    assert (tmp_path / "audit.jsonl.2").read_text() == "old1\n"
    assert (tmp_path / "audit.jsonl.3").read_text() == "old2\n"
    # Original .3 (old3) was dropped because backup_count=3.
    assert not (tmp_path / "audit.jsonl.4").exists()


def test_write_event_triggers_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(path))
    monkeypatch.setenv("CLAWHUM_AUDIT_MAX_BYTES", "200")
    monkeypatch.setenv("CLAWHUM_AUDIT_BACKUP_COUNT", "2")
    get_settings.cache_clear()

    big = {"actor": "anonymous", "blob": "q" * 300}
    write_event(big, path=path)
    # First write creates the file with one big line, no rotation yet.
    assert path.exists()
    write_event(big, path=path)
    # Second write sees the prior file already over max_bytes and rotates
    # it to .1 before appending. Active file now holds just the new line.
    assert (tmp_path / "audit.jsonl.1").exists()
    assert path.exists()
    active_lines = path.read_text().strip().splitlines()
    assert len(active_lines) == 1
    assert json.loads(active_lines[0])["blob"] == "q" * 300


def test_privacy_export_and_redact_walk_rotated_files(tmp_path: Path) -> None:
    from clawhum_api.privacy import actor_id_for, collect_events, redact_actor

    path = tmp_path / "audit.jsonl"
    actor = actor_id_for("sk-test")
    other = actor_id_for("sk-other")

    rotated = tmp_path / "audit.jsonl.1"
    rotated.write_text(
        json.dumps({"actor": actor, "client_ip": "1.1.1.1", "method": "POST"}) + "\n"
        + json.dumps({"actor": other, "client_ip": "2.2.2.2", "method": "POST"}) + "\n"
    )
    path.write_text(
        json.dumps({"actor": actor, "client_ip": "3.3.3.3", "method": "DELETE"}) + "\n"
    )

    events = collect_events(actor, path)
    assert len(events) == 2
    ips = sorted(ev["client_ip"] for ev in events)
    assert ips == ["1.1.1.1", "3.3.3.3"]

    redacted = redact_actor(actor, path)
    assert redacted == 2
    # After redaction, the other tenant's row is untouched.
    rotated_after = [json.loads(ln) for ln in rotated.read_text().splitlines() if ln]
    active_after = [json.loads(ln) for ln in path.read_text().splitlines() if ln]
    assert rotated_after[0]["client_ip"] == "redacted"
    assert rotated_after[0]["actor"] == "redacted"
    assert rotated_after[1]["client_ip"] == "2.2.2.2"
    assert active_after[0]["client_ip"] == "redacted"
