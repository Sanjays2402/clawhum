from pathlib import Path

import pytest
import typer

from cli.clawhum_cli.main import _resolve_output_format


def test_default_is_table_on_stdout():
    assert _resolve_output_format(None, False, None) == "table"


def test_json_shortcut_wins_over_fmt_and_output():
    # --json beats explicit --format and any --output extension
    assert _resolve_output_format("csv", True, Path("hits.csv")) == "json"


def test_explicit_format_respected_on_stdout():
    assert _resolve_output_format("json", False, None) == "json"
    assert _resolve_output_format("csv", False, None) == "csv"
    assert _resolve_output_format("table", False, None) == "table"


def test_format_is_case_insensitive():
    assert _resolve_output_format("JSON", False, None) == "json"


def test_invalid_format_raises():
    with pytest.raises(typer.BadParameter):
        _resolve_output_format("yaml", False, None)


def test_output_json_extension_infers_json():
    assert _resolve_output_format(None, False, Path("out/hits.json")) == "json"


def test_output_csv_extension_infers_csv():
    assert _resolve_output_format(None, False, Path("out/hits.csv")) == "csv"
    assert _resolve_output_format(None, False, Path("hits.tsv")) == "csv"
    assert _resolve_output_format(None, False, Path("hits.txt")) == "csv"


def test_output_unknown_extension_falls_back_to_csv():
    # Avoid spraying ANSI table escapes into an opaque file.
    assert _resolve_output_format(None, False, Path("hits.dat")) == "csv"
    assert _resolve_output_format(None, False, Path("hits")) == "csv"


def test_output_jsonl_extension_infers_jsonl():
    # .jsonl and .ndjson are the canonical newline-delimited JSON suffixes;
    # users piping into jq / Spark / BigQuery expect one object per line.
    assert _resolve_output_format(None, False, Path("out/hits.jsonl")) == "jsonl"
    assert _resolve_output_format(None, False, Path("out/hits.ndjson")) == "jsonl"


def test_explicit_jsonl_format_accepted():
    assert _resolve_output_format("jsonl", False, None) == "jsonl"
    assert _resolve_output_format("JSONL", False, None) == "jsonl"
    # Explicit format wins over a mismatched output extension.
    assert _resolve_output_format("jsonl", False, Path("hits.csv")) == "jsonl"
    assert _resolve_output_format("csv", False, Path("hits.jsonl")) == "csv"


def test_explicit_format_overrides_output_extension():
    # If the user explicitly asks for json, honor it even when the file ends in .csv.
    assert _resolve_output_format("json", False, Path("hits.csv")) == "json"
    assert _resolve_output_format("csv", False, Path("hits.json")) == "csv"


def test_explicit_table_with_output_downgrades_to_csv():
    # Rich tables to disk are rarely what's wanted.
    assert _resolve_output_format("table", False, Path("hits.json")) == "csv"
