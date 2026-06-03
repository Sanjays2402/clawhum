"""`clawhum match --exclude-downvoted` auto-drops tracks whose recorded
feedback is net negative (down > up) so a returning user does not see the
same rejected songs at the top of the list. A single stray down-vote does
not blacklist a track; only tracks where down-votes strictly outnumber
up-votes qualify, so an accidental thumbs-down that was later corrected
will still surface.

The pure helper is unit-tested here; the CLI wiring unions the resulting
ids with any user-supplied `--exclude-track` values."""

from cli.clawhum_cli.main import _downvoted_track_ids


def test_empty_input_is_empty_set():
    assert _downvoted_track_ids([]) == set()


def test_lone_downvote_blacklists_track():
    rows = [{"track_id": "a", "vote": -1}]
    assert _downvoted_track_ids(rows) == {"a"}


def test_lone_upvote_does_not_blacklist():
    rows = [{"track_id": "a", "vote": 1}]
    assert _downvoted_track_ids(rows) == set()


def test_net_positive_track_is_not_blacklisted():
    # one down, two up -> net +1, not blacklisted (accidental misclick recovery)
    rows = [
        {"track_id": "a", "vote": -1},
        {"track_id": "a", "vote": 1},
        {"track_id": "a", "vote": 1},
    ]
    assert _downvoted_track_ids(rows) == set()


def test_tie_is_not_blacklisted():
    # strict inequality: equal up/down does not qualify
    rows = [
        {"track_id": "a", "vote": -1},
        {"track_id": "a", "vote": 1},
    ]
    assert _downvoted_track_ids(rows) == set()


def test_net_negative_track_is_blacklisted():
    rows = [
        {"track_id": "a", "vote": -1},
        {"track_id": "a", "vote": -1},
        {"track_id": "a", "vote": 1},
    ]
    assert _downvoted_track_ids(rows) == {"a"}


def test_independent_tracks_are_scored_independently():
    rows = [
        {"track_id": "a", "vote": -1},
        {"track_id": "b", "vote": 1},
        {"track_id": "c", "vote": -1},
        {"track_id": "c", "vote": -1},
        {"track_id": "c", "vote": 1},
    ]
    # a: net -1 -> blocked. b: net +1 -> not blocked. c: net -1 -> blocked.
    assert _downvoted_track_ids(rows) == {"a", "c"}


def test_missing_or_malformed_rows_are_ignored_not_raised():
    rows = [
        {},  # no track_id, no vote
        {"vote": -1},  # no track_id
        {"track_id": "", "vote": -1},  # blank track_id
        {"track_id": 42, "vote": -1},  # non-string track_id
        {"track_id": "a", "vote": "down"},  # non-integer vote
        {"track_id": "a", "vote": 0},  # unrecognised vote value
        {"track_id": "a", "vote": -1},  # finally a real down-vote
    ]
    assert _downvoted_track_ids(rows) == {"a"}


def test_case_sensitive_track_ids():
    rows = [
        {"track_id": "A", "vote": -1},
        {"track_id": "a", "vote": 1},
    ]
    # 'A' and 'a' are distinct ids; only 'A' is blacklisted
    assert _downvoted_track_ids(rows) == {"A"}
