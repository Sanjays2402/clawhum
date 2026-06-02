"""`clawhum match --exclude-track <id>` drops matches with the given track_id
so a user can peek past a known-wrong top hit (e.g. a duplicate edition)
without re-humming. The pure helper is unit-tested here; the CLI wiring
fetches extra candidates so the post-filter list still has ~top_k rows."""

from clawhum_core.types import Match, Track

from cli.clawhum_cli.main import _filter_excluded_tracks


def _results():
    return [
        Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.91, segment_index=0),
        Match(track=Track(id="b", title="Beta", artist="B1"), score=0.80, segment_index=1),
        Match(track=Track(id="c", title="Gamma", artist="C1"), score=0.72, segment_index=2),
    ]


def test_exclude_none_or_empty_is_passthrough():
    rows = _results()
    assert [m.track.id for m in _filter_excluded_tracks(rows, None)] == ["a", "b", "c"]
    assert [m.track.id for m in _filter_excluded_tracks(rows, [])] == ["a", "b", "c"]
    # whitespace-only entries are treated as no-op, not as a blanket drop
    assert [m.track.id for m in _filter_excluded_tracks(rows, ["  ", ""])] == ["a", "b", "c"]


def test_exclude_drops_named_track():
    rows = _results()
    out = _filter_excluded_tracks(rows, ["a"])
    assert [m.track.id for m in out] == ["b", "c"]


def test_exclude_is_repeatable_and_trims_whitespace():
    rows = _results()
    out = _filter_excluded_tracks(rows, ["a", "  c  "])
    assert [m.track.id for m in out] == ["b"]


def test_exclude_unknown_id_is_noop_not_error():
    rows = _results()
    out = _filter_excluded_tracks(rows, ["does-not-exist"])
    assert [m.track.id for m in out] == ["a", "b", "c"]


def test_exclude_returns_a_new_list_not_a_mutated_view():
    rows = _results()
    out = _filter_excluded_tracks(rows, ["a"])
    # caller's list is untouched
    assert [m.track.id for m in rows] == ["a", "b", "c"]
    # and the returned list is independent
    out.clear()
    assert [m.track.id for m in rows] == ["a", "b", "c"]


def test_exclude_is_case_sensitive():
    rows = _results()
    # IDs in this codebase are opaque strings; don't silently lowercase
    out = _filter_excluded_tracks(rows, ["A"])
    assert [m.track.id for m in out] == ["a", "b", "c"]
