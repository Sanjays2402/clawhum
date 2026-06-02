import csv
import io
import json

from clawhum_core.types import Match, Track
from cli.clawhum_cli.main import _results_as_csv, _results_as_dicts


def _sample_results():
    return [
        Match(track=Track(id="a", title="Alpha", artist="A1"), score=0.91, segment_index=0),
        Match(track=Track(id="b", title="Beta, the", artist=""), score=0.42, segment_index=3),
    ]


def test_results_as_dicts_shape_and_rank():
    rows = _results_as_dicts(_sample_results())
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["track_id"] == "a"
    assert rows[0]["title"] == "Alpha"
    assert rows[0]["score"] == 0.91
    assert rows[0]["segment"] == 0
    # round-trips as JSON
    json.loads(json.dumps(rows))


def test_results_as_csv_header_and_quoting():
    payload = _results_as_csv(_sample_results())
    reader = list(csv.DictReader(io.StringIO(payload)))
    assert reader[0]["rank"] == "1"
    assert reader[0]["title"] == "Alpha"
    assert reader[0]["artist"] == "A1"
    # comma inside title must survive a CSV round trip
    assert reader[1]["title"] == "Beta, the"
    assert reader[1]["artist"] == ""
    assert reader[1]["segment"] == "3"


def test_results_as_csv_empty():
    payload = _results_as_csv([])
    # header line only
    lines = [ln for ln in payload.splitlines() if ln]
    assert lines == ["rank,track_id,title,artist,score,segment"]


def test_results_as_dicts_includes_query_id_when_set():
    rows = _results_as_dicts(_sample_results(), query_id="q-123")
    assert all(r["query_id"] == "q-123" for r in rows)
    # without query_id the field is omitted (backward compatible)
    rows_plain = _results_as_dicts(_sample_results())
    assert "query_id" not in rows_plain[0]


def test_results_as_csv_includes_query_id_column_when_set():
    payload = _results_as_csv(_sample_results(), query_id="q-abc")
    reader = list(csv.DictReader(io.StringIO(payload)))
    assert reader[0]["query_id"] == "q-abc"
    assert reader[1]["query_id"] == "q-abc"
    # header order keeps query_id at the end so existing column indexes are stable
    header = payload.splitlines()[0].split(",")
    assert header == ["rank", "track_id", "title", "artist", "score", "segment", "query_id"]
