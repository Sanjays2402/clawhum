from pathlib import Path
import soundfile as sf
from tests.fixtures.synth import tone
from clawhum_indexer.build import build_index, IndexerOptions


def test_build_index_local(tmp_path, monkeypatch):
    lib = tmp_path / "music"
    (lib / "art").mkdir(parents=True)
    sf.write(str(lib / "art" / "one.wav"), tone(440, 3.0), 48000)
    sf.write(str(lib / "art" / "two.wav"), tone(880, 3.0), 48000)

    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_LIBRARY_PATH", str(lib))
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()

    res = build_index(IndexerOptions(library_path=lib, use_clap=False))
    assert res["tracks_added"] == 2
    assert res["vectors_added"] >= 2
