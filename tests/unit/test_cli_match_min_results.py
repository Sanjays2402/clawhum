"""`clawhum match --min-results N` (or `-N`) must exit non-zero (code 2)
when fewer than N matches survive the threshold and filters, so CI
gates can require a minimum candidate count (e.g. need at least 3
above-threshold hits before promoting an auto-tag) instead of treating
a single weak hit as a confident win.

A value of N=0 or negative is rejected so a misconfigured pipeline
cannot silently disable the gate. The flag composes with --fail-on-empty
(which is effectively --min-results 1): the stricter floor wins.
"""

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Match, Track


class _FakeEmbedder:
    sr = 16000
    dim = 4


class _FakeIndex:
    def size(self):
        return 5


def _make_matcher_returning(n_hits):
    hits = [
        Match(
            track=Track(id=f"t{i}", title=f"T{i}", artist=f"A{i}"),
            score=0.9 - 0.05 * i,
            segment_index=0,
        )
        for i in range(n_hits)
    ]

    class _M:
        def __init__(self, *a, **kw):  # noqa: ARG002
            pass

        def match(self, x, sr, top_k, threshold):  # noqa: ARG002
            return list(hits)

    return _M


class _FakeState:
    def __init__(self):
        self.tracks = {f"t{i}": Track(id=f"t{i}", title=f"T{i}", artist=f"A{i}") for i in range(5)}
        self.embedder = _FakeEmbedder()
        self.index = _FakeIndex()

    @classmethod
    def boot(cls, prefer_clap: bool = True):  # noqa: ARG003
        return cls()


def _patch_common(monkeypatch, matcher_cls):
    monkeypatch.setattr(
        "services.api.clawhum_api.state.AppState", _FakeState, raising=True
    )
    monkeypatch.setattr(
        "clawhum_audio.io.load_audio", lambda p, target_sr: (b"\x00", target_sr), raising=True
    )
    monkeypatch.setattr(
        "clawhum_match.matcher.Matcher", matcher_cls, raising=True
    )


def _hum(tmp_path):
    fake = tmp_path / "hum.wav"
    fake.write_bytes(b"\x00")
    return fake


def test_min_results_fires_when_under_floor(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(2))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--min-results", "3"])
    assert result.exit_code == 2, result.output
    # Surviving matches are still shown so the operator can see what came back.
    assert "T0" in result.output
    assert "T1" in result.output
    assert "only 2 match(es) survived" in result.output
    assert "need at least 3" in result.output


def test_min_results_short_flag(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(1))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "-N", "5"])
    assert result.exit_code == 2, result.output


def test_min_results_passes_when_floor_met(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(3))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--min-results", "3"])
    assert result.exit_code == 0, result.output
    assert "only" not in result.output


def test_min_results_passes_when_floor_exceeded(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(5))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "-N", "2"])
    assert result.exit_code == 0, result.output


def test_min_results_rejects_zero(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(0))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--min-results", "0"])
    assert result.exit_code != 0
    assert "must be a positive integer" in result.output


def test_min_results_rejects_negative(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(0))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--min-results", "-2"])
    assert result.exit_code != 0
    assert "must be a positive integer" in result.output


def test_min_results_json_mode_still_emits_payload_then_exits_two(tmp_path, monkeypatch):
    import json as _json

    _patch_common(monkeypatch, _make_matcher_returning(1))
    result = CliRunner().invoke(
        app, ["match", str(_hum(tmp_path)), "--json", "--min-results", "3"]
    )
    assert result.exit_code == 2, result.output
    # JSON payload must still be on stdout so downstream tools see the partial result.
    # Strip rich formatting by parsing the first balanced JSON array we find.
    start = result.output.find("[")
    end = result.output.rfind("]")
    assert start != -1 and end != -1, result.output
    parsed = _json.loads(result.output[start : end + 1])
    assert isinstance(parsed, list)
    assert len(parsed) == 1


def test_min_results_csv_mode_writes_rows_then_exits_two(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(2))
    out = tmp_path / "hits.csv"
    result = CliRunner().invoke(
        app,
        ["match", str(_hum(tmp_path)), "-o", str(out), "--min-results", "5"],
    )
    assert result.exit_code == 2, result.output
    # File was still written so the operator can inspect what did survive.
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "t0" in content
    assert "t1" in content


def test_min_results_empty_results_fires(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _make_matcher_returning(0))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--min-results", "1"])
    assert result.exit_code == 2, result.output
    assert "no matches" in result.output


def test_min_results_composes_with_fail_on_empty_stricter_wins(tmp_path, monkeypatch):
    # --fail-on-empty alone would pass (1 hit), but --min-results 3 raises the floor.
    _patch_common(monkeypatch, _make_matcher_returning(1))
    result = CliRunner().invoke(
        app, ["match", str(_hum(tmp_path)), "-E", "--min-results", "3"]
    )
    assert result.exit_code == 2, result.output


def test_min_results_unset_does_not_change_default_exit(tmp_path, monkeypatch):
    # Sanity: without --min-results a single hit still exits 0.
    _patch_common(monkeypatch, _make_matcher_returning(1))
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path))])
    assert result.exit_code == 0, result.output
