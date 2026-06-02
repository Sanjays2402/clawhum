"""`clawhum match --no-hint` must suppress the trailing query_id and
vote-command hint in table mode so the output is clean enough to pipe
into a pager or capture in a script. The hint must still appear by
default so the existing terminal UX is unchanged.
"""

from pathlib import Path

from typer.testing import CliRunner

from cli.clawhum_cli.main import app
from clawhum_core.types import Match, Track


class _FakeTrack:
    sr = 16000
    dim = 4


class _FakeIndex:
    def size(self):
        return 1


class _FakeMatcher:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def match(self, x, sr, top_k, threshold):  # noqa: ARG002
        return [
            Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.9, segment_index=0),
        ]


class _FakeState:
    def __init__(self):
        self.tracks = {"a": Track(id="a", title="Alpha", artist="A1")}
        self.embedder = _FakeTrack()
        self.index = _FakeIndex()

    @classmethod
    def boot(cls, prefer_clap: bool = True):  # noqa: ARG003
        return cls()


def _common_monkeypatches(monkeypatch):
    monkeypatch.setattr(
        "services.api.clawhum_api.state.AppState", _FakeState, raising=True
    )
    monkeypatch.setattr(
        "clawhum_audio.io.load_audio", lambda p, target_sr: (b"\x00", target_sr), raising=True
    )
    monkeypatch.setattr(
        "clawhum_match.matcher.Matcher", _FakeMatcher, raising=True
    )


def test_match_table_default_prints_vote_hint(tmp_path, monkeypatch):
    _common_monkeypatches(monkeypatch)
    fake = tmp_path / "hum.wav"
    fake.write_bytes(b"\x00")

    runner = CliRunner()
    result = runner.invoke(app, ["match", str(fake)])

    assert result.exit_code == 0, result.output
    assert "query_id:" in result.output
    assert "vote a match" in result.output


def test_match_no_hint_suppresses_vote_hint(tmp_path, monkeypatch):
    _common_monkeypatches(monkeypatch)
    fake = tmp_path / "hum.wav"
    fake.write_bytes(b"\x00")

    runner = CliRunner()
    result = runner.invoke(app, ["match", str(fake), "--no-hint"])

    assert result.exit_code == 0, result.output
    # the table itself must still render
    assert "Alpha" in result.output
    # but the trailing hint block is gone
    assert "query_id:" not in result.output
    assert "vote a match" not in result.output


def test_match_no_hint_short_flag(tmp_path, monkeypatch):
    _common_monkeypatches(monkeypatch)
    fake = tmp_path / "hum.wav"
    fake.write_bytes(b"\x00")

    runner = CliRunner()
    result = runner.invoke(app, ["match", str(fake), "-Q"])

    assert result.exit_code == 0, result.output
    assert "Alpha" in result.output
    assert "query_id:" not in result.output
