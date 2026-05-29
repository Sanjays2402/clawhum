import numpy as np
from clawhum_core.types import Track
from clawhum_index.numpy_index import NumpyIndex
from clawhum_index.base import IndexedItem
from clawhum_embed.fallback import HashEmbedder
from clawhum_match.matcher import Matcher
from clawhum_match.rerank import threshold_filter, tempo_rerank
from clawhum_core.types import Match
from tests.fixtures.synth import melody


def test_matcher_finds_self():
    e = HashEmbedder(dim=256)
    tracks = [
        Track(id="x", title="X"), Track(id="y", title="Y"), Track(id="z", title="Z"),
    ]
    audios = [melody([440, 494, 523]), melody([220, 247, 262]), melody([660, 740, 784])]
    idx = NumpyIndex(dim=256)
    for t, a in zip(tracks, audios):
        v = e.embed(a, 48000)
        idx.add([IndexedItem(track_id=t.id, segment_index=0, vector=v, meta={})])

    m = Matcher(e, idx, {t.id: t for t in tracks})
    res = m.match(audios[0], 48000, top_k=3, threshold=0.0, rerank=False)
    assert res[0].track.id == "x"


def test_threshold_filter():
    ms = [Match(track=Track(id=str(i), title=str(i)), score=s) for i, s in enumerate([0.1, 0.5, 0.9])]
    assert len(threshold_filter(ms, 0.4)) == 2


def test_tempo_rerank_no_query_tempo():
    ms = [Match(track=Track(id="a", title="A", tempo_bpm=120), score=0.5)]
    out = tempo_rerank(ms, 0.0)
    assert out[0].score == 0.5
