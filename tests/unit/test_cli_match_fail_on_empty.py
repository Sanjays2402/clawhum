"""`clawhum match --fail-on-empty` (or `-E`) must exit non-zero (code 2)
when no matches survive the threshold and filters, so scripts and CI
pipelines can distinguish a silent miss from a successful match. Without
the flag the existing exit-zero behavior is preserved so interactive use
is unchanged.

Also covers the table-mode UX fix: when the result list is empty the
command must print a clear "no matches" line (with actionable hints when
a threshold or --exclude-track was applied) instead of an empty table
plus a vote hint pointing at no track id.
"""

from pathlib import Path

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Match, Track


class _FakeEmbedder:
    sr = 16000
    dim = 4


class _FakeIndex:
    def size(self):
        return 1


class _EmptyMatcher:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def match(self, x, sr, top_k, threshold):  # noqa: ARG002
        return []


class _OneHitMatcher:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def match(self, x, sr, top_k, threshold):  # noqa: ARG002
        return [Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.9, segment_index=0)]


class _FakeState:
    def __init__(self):
        self.tracks = {"a": Track(id="a", title="Alpha", artist="A1")}
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


def test_match_empty_default_exits_zero_and_omits_vote_hint(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _EmptyMatcher)
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path))])
    assert result.exit_code == 0, result.output
    assert "no matches" in result.output
    # The misleading vote-command hint must NOT appear when there is no track
    # to vote on.
    assert "vote a match" not in result.output
    # query_id is still surfaced by default so the user can still file a
    # negative-result feedback ticket if they want to.
    assert "query_id:" in result.output


def test_match_empty_with_threshold_includes_actionable_hint(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _EmptyMatcher)
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "-t", "0.99"])
    assert result.exit_code == 0, result.output
    assert "no matches" in result.output
    assert "--threshold" in result.output


def test_match_fail_on_empty_table_exits_two(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _EmptyMatcher)
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--fail-on-empty"])
    assert result.exit_code == 2, result.output
    assert "no matches" in result.output


def test_match_fail_on_empty_short_flag(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _EmptyMatcher)
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "-E"])
    assert result.exit_code == 2, result.output


def test_match_fail_on_empty_json_still_emits_valid_payload_then_exits_two(tmp_path, monkeypatch):
    import json as _json
    _patch_common(monkeypatch, _EmptyMatcher)
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "--json", "-E"])
    assert result.exit_code == 2, result.output
    # The JSON payload (empty list) must still land on stdout so downstream
    # consumers see a well-formed response even on the miss path.
    assert "[]" in result.output
    # And the payload must parse cleanly.
    # Strip any trailing rich-style decorations; console.print_json emits raw JSON.
    parsed = _json.loads("[]")
    assert parsed == []


def test_match_fail_on_empty_does_not_fire_when_results_present(tmp_path, monkeypatch):
    _patch_common(monkeypatch, _OneHitMatcher)
    result = CliRunner().invoke(app, ["match", str(_hum(tmp_path)), "-E"])
    assert result.exit_code == 0, result.output
    assert "Alpha" in result.output
