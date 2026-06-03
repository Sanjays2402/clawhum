"""`clawhum match --only-upvoted` restricts matches to tracks whose recorded
feedback is net positive (up > down) so a returning user can re-discover
songs they've already liked that also match a hum. A single up-vote later
cancelled by a down-vote does not qualify; the threshold is strict so only
tracks the user has clearly stood by survive.

The pure helper is unit-tested here; the CLI wiring unions the resulting
ids with any user-supplied `--only-track` values."""

from cli.clawhum_cli.main import _upvoted_track_ids


def test_empty_input_is_empty_set():
    assert _upvoted_track_ids([]) == set()


def test_lone_upvote_admits_track():
    rows = [{"track_id": "a", "vote": 1}]
    assert _upvoted_track_ids(rows) == {"a"}


def test_lone_downvote_does_not_admit():
    rows = [{"track_id": "a", "vote": -1}]
    assert _upvoted_track_ids(rows) == set()


def test_net_negative_track_is_not_admitted():
    # two down, one up -> net -1, not admitted
    rows = [
        {"track_id": "a", "vote": -1},
        {"track_id": "a", "vote": -1},
        {"track_id": "a", "vote": 1},
    ]
    assert _upvoted_track_ids(rows) == set()


def test_tie_is_not_admitted():
    # strict inequality: equal up/down does not qualify so a vote the user
    # later cancelled with a down-vote is not silently treated as a like
    rows = [
        {"track_id": "a", "vote": 1},
        {"track_id": "a", "vote": -1},
    ]
    assert _upvoted_track_ids(rows) == set()


def test_net_positive_track_is_admitted():
    rows = [
        {"track_id": "a", "vote": 1},
        {"track_id": "a", "vote": 1},
        {"track_id": "a", "vote": -1},
    ]
    assert _upvoted_track_ids(rows) == {"a"}


def test_independent_tracks_are_scored_independently():
    rows = [
        {"track_id": "a", "vote": 1},
        {"track_id": "b", "vote": -1},
        {"track_id": "c", "vote": 1},
        {"track_id": "c", "vote": 1},
        {"track_id": "c", "vote": -1},
    ]
    # a: net +1 -> admit. b: net -1 -> drop. c: net +1 -> admit.
    assert _upvoted_track_ids(rows) == {"a", "c"}


def test_missing_or_malformed_rows_are_ignored_not_raised():
    rows = [
        {},  # no track_id, no vote
        {"vote": 1},  # no track_id
        {"track_id": "", "vote": 1},  # blank track_id
        {"track_id": 42, "vote": 1},  # non-string track_id
        {"track_id": "a", "vote": "up"},  # non-integer vote
        {"track_id": "a", "vote": 0},  # unrecognised vote value
        {"track_id": "a", "vote": 1},  # finally a real up-vote
    ]
    assert _upvoted_track_ids(rows) == {"a"}


def test_case_sensitive_track_ids():
    rows = [
        {"track_id": "A", "vote": 1},
        {"track_id": "a", "vote": -1},
    ]
    # 'A' and 'a' are distinct ids; only 'A' is admitted
    assert _upvoted_track_ids(rows) == {"A"}


def test_disjoint_with_downvoted_set():
    # the two helpers carve the population into three disjoint buckets:
    # net-positive (admit), net-negative (block), and ties/unvoted (neither).
    # a single track cannot appear in both --only-upvoted and
    # --exclude-downvoted, which is why the CLI forbids combining them.
    from cli.clawhum_cli.main import _downvoted_track_ids
    rows = [
        {"track_id": "a", "vote": 1},
        {"track_id": "b", "vote": -1},
        {"track_id": "c", "vote": 1},
        {"track_id": "c", "vote": -1},
    ]
    up = _upvoted_track_ids(rows)
    down = _downvoted_track_ids(rows)
    assert up == {"a"}
    assert down == {"b"}
    assert up.isdisjoint(down)
