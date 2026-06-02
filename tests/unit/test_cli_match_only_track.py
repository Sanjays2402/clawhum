"""`clawhum match --only-track <id>` restricts matches to the given track_ids
so a user can ask 'does this hum match one of these specific tracks?' without
scanning the whole top-K. The pure helper is unit-tested here; the CLI wiring
asks the matcher for the whole index so the wanted hits can sit far down the
ranking and still surface."""

import pytest

from clawhum_core.types import Match, Track

from cli.clawhum_cli.main import _filter_only_tracks


def _results():
    return [
        Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.91, segment_index=0),
        Match(track=Track(id="b", title="Beta", artist="B1"), score=0.80, segment_index=1),
        Match(track=Track(id="c", title="Gamma", artist="C1"), score=0.72, segment_index=2),
    ]


def test_only_none_or_empty_is_passthrough():
    rows = _results()
    assert [m.track.id for m in _filter_only_tracks(rows, None)] == ["a", "b", "c"]
    assert [m.track.id for m in _filter_only_tracks(rows, [])] == ["a", "b", "c"]
    # whitespace-only entries are treated as no-op, not as a blanket drop
    assert [m.track.id for m in _filter_only_tracks(rows, ["  ", ""])] == ["a", "b", "c"]


def test_only_keeps_named_track():
    rows = _results()
    out = _filter_only_tracks(rows, ["b"])
    assert [m.track.id for m in out] == ["b"]


def test_only_is_repeatable_and_trims_whitespace():
    rows = _results()
    out = _filter_only_tracks(rows, ["a", "  c  "])
    assert [m.track.id for m in out] == ["a", "c"]


def test_only_unknown_id_yields_empty_not_error():
    rows = _results()
    out = _filter_only_tracks(rows, ["does-not-exist"])
    assert out == []


def test_only_returns_a_new_list_not_a_mutated_view():
    rows = _results()
    out = _filter_only_tracks(rows, ["a"])
    # caller's list is untouched
    assert [m.track.id for m in rows] == ["a", "b", "c"]
    # and the returned list is independent
    out.clear()
    assert [m.track.id for m in rows] == ["a", "b", "c"]


def test_only_is_case_sensitive():
    rows = _results()
    # IDs in this codebase are opaque strings; don't silently lowercase
    out = _filter_only_tracks(rows, ["A"])
    assert out == []


def test_only_preserves_input_order():
    rows = _results()
    # request order should NOT dictate output order; matcher's score order wins
    out = _filter_only_tracks(rows, ["c", "a"])
    assert [m.track.id for m in out] == ["a", "c"]


def test_only_and_exclude_are_mutually_exclusive_in_cli():
    # the CLI surfaces this as typer.BadParameter; assert the wiring exists
    # by exercising the match command with both flags. Import locally so the
    # pure-helper tests above stay dependency-free.
    from typer.testing import CliRunner
    from cli.clawhum_cli.main import app

    runner = CliRunner()
    # use an existing fixture path so the file-exists check passes before
    # we hit the mutual-exclusion guard
    import pathlib
    fixtures = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
    sample = next(fixtures.rglob("*.wav"), None)
    if sample is None:
        pytest.skip("no wav fixture available to exercise CLI wiring")
    result = runner.invoke(
        app,
        ["match", str(sample), "--only-track", "a", "--exclude-track", "b"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in (result.output + str(result.exception)).lower()
