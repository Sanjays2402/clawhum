from pathlib import Path
from clawhum_library.local import LocalLibrary, scan_directory
from clawhum_library.feedback import record_feedback, read_feedback


def test_scan_empty_dir(tmp_path):
    assert list(scan_directory(tmp_path)) == []


def test_local_library_picks_up_audio(tmp_path):
    # create fake wav by extension only; library scans by ext
    (tmp_path / "artist1").mkdir()
    (tmp_path / "artist1" / "song.wav").write_bytes(b"")
    tracks = LocalLibrary(tmp_path).tracks()
    assert len(tracks) == 1
    assert tracks[0].artist == "artist1"
    assert tracks[0].title == "song"


def test_feedback_roundtrip(tmp_path):
    p = tmp_path / "fb.jsonl"
    record_feedback(p, "q1", "t1", 0.7, 1)
    record_feedback(p, "q1", "t2", 0.2, -1)
    rows = read_feedback(p)
    assert len(rows) == 2
    assert rows[0]["track_id"] == "t1"
