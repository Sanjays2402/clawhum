import numpy as np
from clawhum_index.numpy_index import NumpyIndex
from clawhum_index.base import IndexedItem
from clawhum_index.persistence import write_metadata, read_metadata, upsert_metadata
from clawhum_core.types import Track


def _items(n=5, dim=16):
    rng = np.random.default_rng(0)
    return [
        IndexedItem(track_id=f"t{i}", segment_index=0,
                    vector=rng.standard_normal(dim).astype("float32"), meta={})
        for i in range(n)
    ]


def test_numpy_index_add_and_search():
    idx = NumpyIndex(dim=16)
    items = _items()
    idx.add(items)
    assert idx.size() == 5
    q = items[2].vector
    res = idx.search(q, k=3)
    assert res[0][0] == 2
    assert res[0][1] > 0.9


def test_numpy_index_persist_roundtrip(tmp_path):
    idx = NumpyIndex(dim=16)
    idx.add(_items())
    p = tmp_path / "ix.npz"
    idx.save(str(p))
    idx2 = NumpyIndex(dim=16)
    idx2.load(str(p))
    assert idx2.size() == 5


def test_metadata_upsert_roundtrip(tmp_path):
    p = tmp_path / "meta.jsonl"
    write_metadata(p, [Track(id="a", title="A"), Track(id="b", title="B")])
    out = read_metadata(p)
    assert {t.id for t in out} == {"a", "b"}
    upsert_metadata(p, [Track(id="a", title="A2"), Track(id="c", title="C")])
    out2 = {t.id: t.title for t in read_metadata(p)}
    assert out2["a"] == "A2"
    assert out2["c"] == "C"
