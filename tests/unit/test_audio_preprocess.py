import numpy as np
from clawhum_audio.preprocess import to_mono, normalize, resample
from clawhum_audio.segment import segment_fixed, segment_query
from clawhum_audio.vad import energy_vad, trim_silence
from tests.fixtures.synth import tone


def test_to_mono_passthrough_for_1d():
    x = tone(440)
    assert to_mono(x).shape == x.shape


def test_to_mono_collapses_stereo():
    x = np.stack([tone(440), tone(880)], axis=-1)
    m = to_mono(x)
    assert m.ndim == 1 and m.shape[0] == x.shape[0]


def test_normalize_peak():
    x = tone(440, amp=0.1)
    y = normalize(x, peak=0.9)
    assert abs(np.max(np.abs(y)) - 0.9) < 1e-3


def test_resample_changes_length():
    x = tone(440, seconds=1.0, sr=48000)
    y = resample(x, 48000, 16000)
    assert abs(len(y) - 16000) <= 4


def test_segment_fixed_count():
    x = tone(440, seconds=10.0, sr=16000)
    segs = segment_fixed(x, 16000, window_s=2.0, hop_s=1.0)
    assert len(segs) >= 8
    assert segs[0].samples.shape[0] == 32000


def test_segment_query_truncates():
    x = tone(440, seconds=20.0, sr=16000)
    s = segment_query(x, 16000, max_seconds=5.0)
    assert s.samples.shape[0] == 80000


def test_vad_trims_silence():
    silence = np.zeros(8000, dtype=np.float32)
    voiced = tone(440, seconds=1.0, sr=16000)
    x = np.concatenate([silence, voiced, silence])
    trimmed = trim_silence(x, 16000)
    assert len(trimmed) < len(x)


def test_energy_vad_returns_bool_array():
    x = tone(440, seconds=1.0, sr=16000)
    v = energy_vad(x, 16000)
    assert v.dtype == bool
