"""`clawhum match --only-track-file` / `--exclude-track-file` load
newline-delimited track id shortlists from disk. Shortlists routinely outgrow
the comfortable command-line width (and quoting story) so reading them from a
file makes the existing --only-track / --exclude-track flags useful in real
scripts and CI without changing their semantics.
"""

from pathlib import Path

import pytest
import typer

from cli.clawhum_cli.main import _load_track_ids_from_file


def test_loads_ids_one_per_line(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    assert _load_track_ids_from_file(p) == ["a", "b", "c"]


def test_skips_blank_lines_and_comments(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text(
        "\n"
        "# a header comment\n"
        "a\n"
        "\n"
        "   # indented comment with leading spaces\n"
        "b\n"
        "  \n"
        "c\n",
        encoding="utf-8",
    )
    assert _load_track_ids_from_file(p) == ["a", "b", "c"]


def test_trims_surrounding_whitespace(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text("  a  \n\tb\t\n", encoding="utf-8")
    assert _load_track_ids_from_file(p) == ["a", "b"]


def test_preserves_duplicates_and_order(tmp_path: Path):
    # downstream filters de-dup via a set, but the loader itself should not
    # silently drop dupes; that would mask a user error in their shortlist
    # (and would diverge from how the CLI option list behaves).
    p = tmp_path / "ids.txt"
    p.write_text("b\na\nb\n", encoding="utf-8")
    assert _load_track_ids_from_file(p) == ["b", "a", "b"]


def test_rejects_id_with_embedded_whitespace(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text("good\nbad id\n", encoding="utf-8")
    with pytest.raises(typer.BadParameter) as excinfo:
        _load_track_ids_from_file(p)
    msg = str(excinfo.value)
    assert "line 2" in msg
    assert "whitespace" in msg


def test_empty_file_returns_empty_list(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text("", encoding="utf-8")
    assert _load_track_ids_from_file(p) == []


def test_only_file_just_comments_returns_empty(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text("# just a header\n# nothing else\n", encoding="utf-8")
    assert _load_track_ids_from_file(p) == []


def test_cli_rejects_only_and_exclude_file_combo(tmp_path: Path):
    # the file-sourced ids must respect the same mutual exclusion as the
    # in-line options; if a user supplies --only-track-file AND
    # --exclude-track the existing guard should still trip.
    from typer.testing import CliRunner
    from cli.clawhum_cli.main import app

    runner = CliRunner()
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    sample = next(fixtures.rglob("*.wav"), None)
    if sample is None:
        pytest.skip("no wav fixture available to exercise CLI wiring")

    only_file = tmp_path / "only.txt"
    only_file.write_text("a\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "match",
            str(sample),
            "--only-track-file",
            str(only_file),
            "--exclude-track",
            "b",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in (result.output + str(result.exception)).lower()
