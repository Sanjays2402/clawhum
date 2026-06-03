"""`clawhum match --only-artist <name>` restricts matches to tracks whose
artist matches the given name (case-insensitive, whitespace-trimmed), so a
user can ask 'does this hum match anything by <artist>?' without scanning the
whole top-K. The pure helper is unit-tested here; the CLI wiring fetches the
whole index and slices top_k off the survivors so the post-filter list still
has ~top_k rows even when the artist's hits sit deep in the ranking.
"""

from clawhum_core.types import Match, Track

from cli.clawhum_cli.main import _filter_only_artists


def _results():
    return [
        Match(track=Track(id="a", title="Hey Jude", artist="The Beatles"), score=0.91, segment_index=0),
        Match(track=Track(id="b", title="Yesterday", artist="The Beatles"), score=0.88, segment_index=1),
        Match(track=Track(id="c", title="Imagine", artist="John Lennon"), score=0.80, segment_index=2),
        Match(track=Track(id="d", title="Bohemian Rhapsody", artist="Queen"), score=0.72, segment_index=3),
    ]


def test_only_none_or_empty_is_passthrough():
    rows = _results()
    assert [m.track.id for m in _filter_only_artists(rows, None)] == ["a", "b", "c", "d"]
    assert [m.track.id for m in _filter_only_artists(rows, [])] == ["a", "b", "c", "d"]
    # whitespace-only entries are ignored, so the call is a no-op rather than
    # a blanket pass-through that would silently keep every row.
    assert [m.track.id for m in _filter_only_artists(rows, ["  ", ""])] == ["a", "b", "c", "d"]


def test_only_keeps_all_tracks_by_artist():
    rows = _results()
    out = _filter_only_artists(rows, ["The Beatles"])
    assert [m.track.id for m in out] == ["a", "b"]


def test_only_is_case_insensitive_and_trims_whitespace():
    rows = _results()
    out = _filter_only_artists(rows, ["  the beatles  "])
    assert [m.track.id for m in out] == ["a", "b"]
    out2 = _filter_only_artists(rows, ["QUEEN"])
    assert [m.track.id for m in out2] == ["d"]


def test_only_is_repeatable_union():
    rows = _results()
    out = _filter_only_artists(rows, ["The Beatles", "queen"])
    assert [m.track.id for m in out] == ["a", "b", "d"]


def test_only_unknown_artist_drops_everything():
    rows = _results()
    out = _filter_only_artists(rows, ["Nobody In Particular"])
    assert out == []


def test_only_does_not_match_substring():
    # "Beatles" alone should not keep "The Beatles": match is whole-string, not substring.
    rows = _results()
    out = _filter_only_artists(rows, ["Beatles"])
    assert out == []


def test_only_handles_blank_artist_field():
    rows = [
        Match(track=Track(id="x", title="Untitled", artist=""), score=0.5, segment_index=0),
        Match(track=Track(id="y", title="Track", artist="Real Artist"), score=0.4, segment_index=0),
    ]
    out = _filter_only_artists(rows, ["Real Artist"])
    assert [m.track.id for m in out] == ["y"]


def test_only_returns_a_new_list_not_a_mutated_view():
    rows = _results()
    out = _filter_only_artists(rows, ["Queen"])
    assert [m.track.id for m in rows] == ["a", "b", "c", "d"]
    out.clear()
    assert [m.track.id for m in rows] == ["a", "b", "c", "d"]
