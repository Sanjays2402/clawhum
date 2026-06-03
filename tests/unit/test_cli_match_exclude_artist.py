"""`clawhum match --exclude-artist <name>` drops matches whose track artist
matches the given name (case-insensitive, whitespace-trimmed), so a user can
peek past a top-K saturated by one artist's discography (covers, remasters,
alternate editions) without re-humming. The pure helper is unit-tested here;
the CLI wiring fetches extra candidates so the post-filter list still has
~top_k rows."""

from clawhum_core.types import Match, Track

from cli.clawhum_cli.main import _filter_excluded_artists


def _results():
    return [
        Match(track=Track(id="a", title="Hey Jude", artist="The Beatles"), score=0.91, segment_index=0),
        Match(track=Track(id="b", title="Yesterday", artist="The Beatles"), score=0.88, segment_index=1),
        Match(track=Track(id="c", title="Imagine", artist="John Lennon"), score=0.80, segment_index=2),
        Match(track=Track(id="d", title="Bohemian Rhapsody", artist="Queen"), score=0.72, segment_index=3),
    ]


def test_exclude_none_or_empty_is_passthrough():
    rows = _results()
    assert [m.track.id for m in _filter_excluded_artists(rows, None)] == ["a", "b", "c", "d"]
    assert [m.track.id for m in _filter_excluded_artists(rows, [])] == ["a", "b", "c", "d"]
    # whitespace-only entries are treated as no-op, not as a blanket drop
    assert [m.track.id for m in _filter_excluded_artists(rows, ["  ", ""])] == ["a", "b", "c", "d"]


def test_exclude_drops_all_tracks_by_artist():
    rows = _results()
    out = _filter_excluded_artists(rows, ["The Beatles"])
    assert [m.track.id for m in out] == ["c", "d"]


def test_exclude_is_case_insensitive_and_trims_whitespace():
    rows = _results()
    out = _filter_excluded_artists(rows, ["  the beatles  "])
    assert [m.track.id for m in out] == ["c", "d"]
    out2 = _filter_excluded_artists(rows, ["QUEEN"])
    assert [m.track.id for m in out2] == ["a", "b", "c"]


def test_exclude_is_repeatable():
    rows = _results()
    out = _filter_excluded_artists(rows, ["The Beatles", "queen"])
    assert [m.track.id for m in out] == ["c"]


def test_exclude_unknown_artist_is_noop_not_error():
    rows = _results()
    out = _filter_excluded_artists(rows, ["Nobody In Particular"])
    assert [m.track.id for m in out] == ["a", "b", "c", "d"]


def test_exclude_does_not_match_substring():
    # "Beatles" alone should not drop "The Beatles": match is whole-string, not substring.
    rows = _results()
    out = _filter_excluded_artists(rows, ["Beatles"])
    assert [m.track.id for m in out] == ["a", "b", "c", "d"]


def test_exclude_handles_blank_artist_field():
    rows = [
        Match(track=Track(id="x", title="Untitled", artist=""), score=0.5, segment_index=0),
        Match(track=Track(id="y", title="Track", artist="Real Artist"), score=0.4, segment_index=0),
    ]
    # Asking to exclude blank-string artist by passing whitespace is a no-op
    # (we already saw that); explicitly excluding "Real Artist" leaves the
    # blank-artist row alone.
    out = _filter_excluded_artists(rows, ["Real Artist"])
    assert [m.track.id for m in out] == ["x"]


def test_exclude_returns_a_new_list_not_a_mutated_view():
    rows = _results()
    out = _filter_excluded_artists(rows, ["Queen"])
    assert [m.track.id for m in rows] == ["a", "b", "c", "d"]
    out.clear()
    assert [m.track.id for m in rows] == ["a", "b", "c", "d"]
