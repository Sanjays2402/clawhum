"""`clawhum match --query-id` lets a caller pin the query id instead of
accepting the auto-generated UUID, so follow-up `clawhum feedback` votes,
deterministic test output, and higher-level request tracing can all line
up against the same id across runs.

The flag must be opt-in (default behaviour is unchanged: a fresh UUID per
run) and reject malformed values up front so a bad id can never poison
the feedback log or table/JSON/CSV output.
"""

import json
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


class _FakeMatcher:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def match(self, x, sr, top_k, threshold):  # noqa: ARG002
        return [
            Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.9, segment_index=0),
        ]


class _EmptyMatcher:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def match(self, x, sr, top_k, threshold):  # noqa: ARG002
        return []


class _FakeState:
    def __init__(self):
        self.tracks = {"a": Track(id="a", title="Alpha", artist="A1")}
        self.embedder = _FakeEmbedder()
        self.index = _FakeIndex()

    @classmethod
    def boot(cls, prefer_clap: bool = True):  # noqa: ARG003
        return cls()


def _patch_common(monkeypatch, matcher=_FakeMatcher):
    monkeypatch.setattr(
        "services.api.clawhum_api.state.AppState", _FakeState, raising=True
    )
    monkeypatch.setattr(
        "clawhum_audio.io.load_audio", lambda p, target_sr: (b"\x00", target_sr), raising=True
    )
    monkeypatch.setattr(
        "clawhum_match.matcher.Matcher", matcher, raising=True
    )


def _hum(tmp_path: Path) -> Path:
    p = tmp_path / "hum.wav"
    p.write_bytes(b"\x00")
    return p


def test_default_query_id_is_a_uuid_when_flag_omitted(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["match", str(_hum(tmp_path))])
    assert result.exit_code == 0, result.output
    # the auto UUID is rendered in the hint block; a literal supplied id like
    # "run-42" should NOT appear by default.
    assert "run-42" not in result.output
    assert "query_id:" in result.output


def test_supplied_query_id_appears_in_table_hint(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["match", str(_hum(tmp_path)), "--query-id", "run-42"])
    assert result.exit_code == 0, result.output
    assert "query_id: run-42" in result.output
    assert "clawhum feedback run-42" in result.output


def test_supplied_query_id_short_flag(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["match", str(_hum(tmp_path)), "-q", "run-42"])
    assert result.exit_code == 0, result.output
    assert "query_id: run-42" in result.output


def test_supplied_query_id_flows_into_json_payload(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["match", str(_hum(tmp_path)), "--json", "--query-id", "run-42"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload and all(row["query_id"] == "run-42" for row in payload)


def test_supplied_query_id_flows_into_csv_payload(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    out = tmp_path / "hits.csv"
    result = runner.invoke(
        app,
        ["match", str(_hum(tmp_path)), "-o", str(out), "--query-id", "run-42"],
    )
    assert result.exit_code == 0, result.output
    body = out.read_text(encoding="utf-8")
    assert "query_id" in body.splitlines()[0]
    assert "run-42" in body


def test_supplied_query_id_appears_in_empty_table_hint(tmp_path, monkeypatch):
    # even with no matches, the supplied id should still be the one printed
    # so a follow-up "did this run miss?" investigation has the right anchor.
    _patch_common(monkeypatch, matcher=_EmptyMatcher)
    runner = CliRunner()
    result = runner.invoke(app, ["match", str(_hum(tmp_path)), "--query-id", "run-42"])
    assert result.exit_code == 0, result.output
    assert "query_id: run-42" in result.output


def test_blank_query_id_is_rejected(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["match", str(_hum(tmp_path)), "--query-id", "   "])
    assert result.exit_code != 0
    assert "--query-id" in result.output


def test_whitespace_inside_query_id_is_rejected(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["match", str(_hum(tmp_path)), "--query-id", "run 42"])
    assert result.exit_code != 0
    assert "--query-id" in result.output


def test_overlong_query_id_is_rejected(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app, ["match", str(_hum(tmp_path)), "--query-id", "x" * 129]
    )
    assert result.exit_code != 0
    assert "--query-id" in result.output


def test_max_length_query_id_is_accepted(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    runner = CliRunner()
    ident = "x" * 128
    result = runner.invoke(
        app, ["match", str(_hum(tmp_path)), "--query-id", ident]
    )
    assert result.exit_code == 0, result.output
    # rich may soft-wrap a 128-char id across terminal-width lines; strip
    # whitespace to compare the rendered id content against the input.
    flat = "".join(result.output.split())
    assert ident in flat
    assert "query_id:" in result.output
