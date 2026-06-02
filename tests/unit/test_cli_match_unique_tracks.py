"""`clawhum match --unique-tracks` collapses multiple segment hits from the
same track into one row (the best-scoring segment), so a single song with
several strong segments cannot fill the whole top-K and crowd out other
candidates. The pure helper is unit-tested here; the CLI wiring fetches extra
candidates so the post-dedupe list still has ~top_k rows."""

from clawhum_core.types import Match, Track

from cli.clawhum_cli.main import _dedupe_by_track


def _results():
    # Score-descending, matcher contract: same track 'a' appears twice with
    # different segment hits, plus two other tracks.
    return [
        Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.91, segment_index=0),
        Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.88, segment_index=4),
        Match(track=Track(id="b", title="Beta", artist="B1"), score=0.80, segment_index=1),
        Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.75, segment_index=9),
        Match(track=Track(id="c", title="Gamma", artist="C1"), score=0.72, segment_index=2),
    ]


def test_dedupe_keeps_best_segment_per_track():
    out = _dedupe_by_track(_results())
    assert [(m.track.id, m.segment_index) for m in out] == [
        ("a", 0),
        ("b", 1),
        ("c", 2),
    ]


def test_dedupe_preserves_score_descending_order():
    out = _dedupe_by_track(_results())
    scores = [m.score for m in out]
    assert scores == sorted(scores, reverse=True)


def test_dedupe_empty_input_returns_empty():
    assert _dedupe_by_track([]) == []


def test_dedupe_already_unique_is_passthrough():
    rows = [
        Match(track=Track(id="a", title="A", artist="x"), score=0.9, segment_index=0),
        Match(track=Track(id="b", title="B", artist="x"), score=0.8, segment_index=0),
    ]
    out = _dedupe_by_track(rows)
    assert [m.track.id for m in out] == ["a", "b"]


def test_dedupe_does_not_mutate_input():
    rows = _results()
    out = _dedupe_by_track(rows)
    assert len(rows) == 5  # caller's list untouched
    out.clear()
    assert len(rows) == 5


def test_dedupe_track_id_is_case_sensitive():
    # IDs are opaque strings; 'A' and 'a' are distinct tracks
    rows = [
        Match(track=Track(id="a", title="A", artist="x"), score=0.9, segment_index=0),
        Match(track=Track(id="A", title="A", artist="x"), score=0.8, segment_index=0),
        Match(track=Track(id="a", title="A", artist="x"), score=0.7, segment_index=1),
    ]
    out = _dedupe_by_track(rows)
    assert [(m.track.id, m.score) for m in out] == [("a", 0.9), ("A", 0.8)]
