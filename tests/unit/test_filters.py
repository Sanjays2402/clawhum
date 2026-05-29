import numpy as np
from clawhum_audio.filters.biquad import high_pass, low_pass, band_pass
from clawhum_audio.filters.pre_emphasis import pre_emphasis
from tests.fixtures.synth import tone


def test_high_pass_attenuates_low_freq():
    x = tone(50, seconds=0.5, sr=16000)
    y = high_pass(x, 16000, cutoff=200.0)
    assert np.max(np.abs(y)) < np.max(np.abs(x)) * 0.5


def test_low_pass_attenuates_high_freq():
    x = tone(4000, seconds=0.5, sr=16000)
    y = low_pass(x, 16000, cutoff=500.0)
    assert np.max(np.abs(y)) < np.max(np.abs(x)) * 0.5


def test_band_pass_passes_in_band():
    x = tone(440, seconds=0.5, sr=16000)
    y = band_pass(x, 16000, low=200, high=2000)
    assert np.max(np.abs(y)) > 0.05


def test_pre_emphasis_changes_signal():
    x = tone(440, seconds=0.5, sr=16000)
    y = pre_emphasis(x)
    assert not np.allclose(x, y)
