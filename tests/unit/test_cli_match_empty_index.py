"""Regression: `clawhum match` on an empty index must print a helpful
message and exit non-zero. Previously it passed the message string into
typer.Exit(code=...), which uses the string as the exit code and never
shows the user why the command failed."""

from pathlib import Path

from typer.testing import CliRunner

from cli.clawhum_cli.main import app


class _EmptyState:
    def __init__(self):
        self.tracks = {}
        self.embedder = None
        self.index = None

    @classmethod
    def boot(cls, prefer_clap: bool = True):  # noqa: ARG003
        return cls()


def test_match_empty_index_prints_message_and_exits_nonzero(tmp_path, monkeypatch):
    # AppState is imported inside the command body, so patch the module attr.
    monkeypatch.setattr(
        "services.api.clawhum_api.state.AppState", _EmptyState, raising=True
    )

    # We never reach load_audio because the empty-index check fires first,
    # but typer requires the path to exist for the Argument validator.
    fake = tmp_path / "hum.wav"
    fake.write_bytes(b"\x00")

    runner = CliRunner()
    result = runner.invoke(app, ["match", str(fake)])

    assert result.exit_code == 1, result.output
    assert "index empty" in result.output
    assert "clawhum index" in result.output
