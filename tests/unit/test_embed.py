import numpy as np
from clawhum_embed.fallback import HashEmbedder
from clawhum_embed.clap import select_device
from tests.fixtures.synth import tone, melody


def test_hash_embedder_shape():
    e = HashEmbedder(dim=512, sr=48000)
    v = e.embed(tone(440), 48000)
    assert v.shape == (512,)
    assert abs(np.linalg.norm(v) - 1.0) < 1e-3


def test_hash_embedder_batch():
    e = HashEmbedder(dim=256, sr=48000)
    batch = [tone(220), tone(440), tone(880)]
    V = e.embed_batch(batch, 48000)
    assert V.shape == (3, 256)


def test_hash_embedder_similarity_self_vs_other():
    e = HashEmbedder()
    a = e.embed(melody([440, 494, 523, 587]), 48000)
    a2 = e.embed(melody([440, 494, 523, 587]), 48000)
    b = e.embed(melody([220, 247, 262, 294]), 48000)
    self_sim = float(a @ a2)
    cross = float(a @ b)
    assert self_sim > cross


def test_select_device_returns_string():
    assert isinstance(select_device("cpu"), str)
