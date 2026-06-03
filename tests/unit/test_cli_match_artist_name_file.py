"""`clawhum match --only-artist-file` / `--exclude-artist-file` load
newline-delimited artist names from disk. Long-lived allow/deny lists
(favourite artists, persistently-noisy cover artists) outgrow what fits on a
command line; reading them from a file makes the existing --only-artist /
--exclude-artist flags useful in real scripts and CI without changing their
case-insensitive, whitespace-trimmed semantics.
"""

from pathlib import Path

import pytest
import typer

from cli.clawhum_cli.main import _load_artist_names_from_file


def test_loads_names_one_per_line(tmp_path: Path):
    p = tmp_path / "artists.txt"
    p.write_text("The Beatles\nQueen\nABBA\n", encoding="utf-8")
    assert _load_artist_names_from_file(p) == ["The Beatles", "Queen", "ABBA"]


def test_skips_blank_lines_and_comments(tmp_path: Path):
    p = tmp_path / "artists.txt"
    p.write_text(
        "\n"
        "# favourites\n"
        "The Beatles\n"
        "\n"
        "   # indented comment\n"
        "Queen\n"
        "  \n"
        "ABBA\n",
        encoding="utf-8",
    )
    assert _load_artist_names_from_file(p) == ["The Beatles", "Queen", "ABBA"]


def test_trims_surrounding_whitespace(tmp_path: Path):
    p = tmp_path / "artists.txt"
    p.write_text("  The Beatles  \n\tQueen\t\n", encoding="utf-8")
    assert _load_artist_names_from_file(p) == ["The Beatles", "Queen"]


def test_preserves_embedded_whitespace(tmp_path: Path):
    # unlike track ids, artist names routinely contain spaces; the loader
    # must keep them so 'the beatles' survives and casefolds correctly
    # downstream against tracks tagged 'The Beatles'.
    p = tmp_path / "artists.txt"
    p.write_text("the beatles\nearth wind and fire\n", encoding="utf-8")
    assert _load_artist_names_from_file(p) == [
        "the beatles",
        "earth wind and fire",
    ]


def test_preserves_duplicates_and_order(tmp_path: Path):
    p = tmp_path / "artists.txt"
    p.write_text("Queen\nThe Beatles\nQueen\n", encoding="utf-8")
    assert _load_artist_names_from_file(p) == [
        "Queen",
        "The Beatles",
        "Queen",
    ]


def test_empty_file_returns_empty_list(tmp_path: Path):
    p = tmp_path / "artists.txt"
    p.write_text("", encoding="utf-8")
    assert _load_artist_names_from_file(p) == []


def test_only_file_just_comments_returns_empty(tmp_path: Path):
    p = tmp_path / "artists.txt"
    p.write_text("# favourites\n# nothing else\n", encoding="utf-8")
    assert _load_artist_names_from_file(p) == []


def test_cli_rejects_only_and_exclude_artist_file_combo(tmp_path: Path):
    # file-sourced names must respect the same mutual exclusion as the
    # in-line options; if a user supplies --only-artist-file AND
    # --exclude-artist the existing guard should still trip.
    from typer.testing import CliRunner
    from cli.clawhum_cli.main import app

    runner = CliRunner()
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    sample = next(fixtures.rglob("*.wav"), None)
    if sample is None:
        pytest.skip("no wav fixture available to exercise CLI wiring")

    only_file = tmp_path / "only.txt"
    only_file.write_text("Queen\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "match",
            str(sample),
            "--only-artist-file",
            str(only_file),
            "--exclude-artist",
            "ABBA",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in (result.output + str(result.exception)).lower()
