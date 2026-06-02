from __future__ import annotations
import csv
import io
import json
import sys
import uuid
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table

from clawhum_core.logging import configure_logging
from clawhum_core.settings import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ClawHum: hum songs, find matches.")
console = Console()


@app.callback()
def _root():
    configure_logging()


@app.command()
def index(
    path: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    spotify_playlist: str | None = typer.Option(None, "--spotify-playlist"),
    no_clap: bool = typer.Option(False, "--no-clap", help="Use fallback hash embedder"),
):
    """Build / extend the index from a local directory or a Spotify playlist."""
    from clawhum_indexer.build import build_index, IndexerOptions
    res = build_index(IndexerOptions(
        library_path=path, spotify_playlist=spotify_playlist, use_clap=not no_clap,
    ))
    console.print_json(json.dumps(res))


def _filter_excluded_tracks(results, exclude: list[str] | None):
    """Drop matches whose track_id is in ``exclude``.

    Comparison is case-sensitive and whitespace-trimmed so users can pass
    ``--exclude-track " abc "`` without surprises. ``None`` or empty list is a
    no-op so callers don't need to special-case it.
    """
    if not exclude:
        return list(results)
    blocked = {t.strip() for t in exclude if t and t.strip()}
    if not blocked:
        return list(results)
    return [m for m in results if m.track.id not in blocked]


def _results_as_dicts(results, query_id: str | None = None):
    rows = [
        {
            "rank": i,
            "track_id": m.track.id,
            "title": m.track.title,
            "artist": m.track.artist,
            "score": m.score,
            "segment": m.segment_index,
        }
        for i, m in enumerate(results, 1)
    ]
    if query_id is not None:
        for r in rows:
            r["query_id"] = query_id
    return rows


def _results_as_csv(results, query_id: str | None = None) -> str:
    buf = io.StringIO()
    fields = ["rank", "track_id", "title", "artist", "score", "segment"]
    if query_id is not None:
        fields.append("query_id")
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in _results_as_dicts(results, query_id=query_id):
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buf.getvalue()


@app.command()
def match(
    query: Path = typer.Argument(..., exists=True, dir_okay=False, file_okay=True),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    threshold: float = typer.Option(0.0, "--threshold", "-t"),
    no_clap: bool = typer.Option(False, "--no-clap"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON (shortcut for --format json)."),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write results to file instead of stdout."),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        "-x",
        help="Drop matches with this track_id. Repeatable. Useful to peek past a known-wrong top hit (e.g. a duplicate edition) without re-humming.",
    ),
):
    """Match an audio file (hum/clip) against the index."""
    from clawhum_audio.io import load_audio
    from clawhum_match.matcher import Matcher
    from services.api.clawhum_api.state import AppState  # type: ignore

    chosen = fmt.lower()
    if json_out:
        chosen = "json"
    if chosen not in {"table", "json", "csv"}:
        raise typer.BadParameter("--format must be one of: table, json, csv")
    if output is not None and chosen == "table":
        # writing rich tables to a file is rarely what users want; default to csv
        chosen = "csv"

    state = AppState.boot(prefer_clap=not no_clap)
    if not state.tracks:
        # typer.Exit takes a code, not a message. Print first, then exit non-zero,
        # so users actually see why match failed instead of a silent exit code.
        console.print("[red]index empty; run `clawhum index <dir>` first[/red]")
        raise typer.Exit(code=1)
    x, sr = load_audio(query, target_sr=state.embedder.sr)
    matcher = Matcher(state.embedder, state.index, state.tracks)
    # Pull a few extra candidates when excluding so the user still gets ~top_k
    # rows after filtering rather than a short list.
    n_excl = len({t.strip() for t in (exclude_track or []) if t and t.strip()})
    fetch_k = top_k + n_excl if n_excl else top_k
    results = matcher.match(x, sr, top_k=fetch_k, threshold=threshold)
    if n_excl:
        results = _filter_excluded_tracks(results, exclude_track)[:top_k]
    query_id = str(uuid.uuid4())

    if chosen == "json":
        payload = json.dumps(_results_as_dicts(results, query_id=query_id))
        if output is not None:
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]wrote {len(results)} match(es) to {output} (query_id={query_id})[/green]")
        else:
            console.print_json(payload)
        return
    if chosen == "csv":
        payload = _results_as_csv(results, query_id=query_id)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            console.print(f"[green]wrote {len(results)} match(es) to {output} (query_id={query_id})[/green]")
        else:
            sys.stdout.write(payload)
            sys.stdout.flush()
        return

    table = Table(title=f"Top {len(results)} matches")
    table.add_column("#", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Title")
    table.add_column("Artist")
    table.add_column("Seg", justify="right")
    table.add_column("Track ID")
    for i, m in enumerate(results, 1):
        table.add_row(
            str(i),
            f"{m.score:.3f}",
            m.track.title,
            m.track.artist,
            str(m.segment_index),
            m.track.id,
        )
    console.print(table)
    console.print(
        f"[dim]query_id: {query_id}\n"
        f"vote a match: clawhum feedback {query_id} <track_id> <score> <1|-1>[/dim]"
    )


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(7451),
    reload: bool = typer.Option(False),
):
    """Run the API server."""
    import uvicorn
    uvicorn.run("clawhum_api.app:app", host=host, port=port, reload=reload, log_config=None)


@app.command()
def stats():
    """Show index stats."""
    from services.api.clawhum_api.state import AppState  # type: ignore
    state = AppState.boot(prefer_clap=False)
    console.print_json(json.dumps({
        "tracks": len(state.tracks),
        "vectors": state.index.size(),
        "dim": state.embedder.dim,
        "backend": state.index.__class__.__name__,
    }))


@app.command()
def feedback(query_id: str, track_id: str, score: float, vote: int):
    """Record a thumbs-up (1) / thumbs-down (-1) for a match."""
    if vote not in (1, -1):
        raise typer.BadParameter("vote must be 1 (thumbs up) or -1 (thumbs down)")
    if not query_id or not query_id.strip():
        raise typer.BadParameter("query_id must not be empty")
    if not track_id or not track_id.strip():
        raise typer.BadParameter("track_id must not be empty")
    import math
    if math.isnan(score) or math.isinf(score):
        raise typer.BadParameter("score must be a finite number")
    s = get_settings()
    from clawhum_library.feedback import record_feedback
    record_feedback(s.feedback_path, query_id, track_id, score, vote)
    console.print("[green]ok[/green]")


@app.command("feedback-delete")
def feedback_delete(
    query_id: str | None = typer.Option(None, "--query-id", help="Delete entries with this query_id."),
    track_id: str | None = typer.Option(None, "--track-id", help="Delete entries with this track_id."),
    vote: int | None = typer.Option(None, "--vote", help="Restrict deletion to this vote (1 or -1)."),
    since: str | None = typer.Option(None, "--since", help="Only delete entries at/after this time (unix seconds or ISO-8601, naive = UTC)."),
    until: str | None = typer.Option(None, "--until", help="Only delete entries strictly before this time (unix seconds or ISO-8601, naive = UTC). Pair with no other filter to purge old feedback (e.g. --until 2024-01-01 to age out last year)."),
    min_score: float | None = typer.Option(None, "--min-score", help="Only delete entries whose recorded score is at least this. Entries without a numeric score are never matched."),
    max_score: float | None = typer.Option(None, "--max-score", help="Only delete entries whose recorded score is at most this. Entries without a numeric score are never matched. Combine with --vote -1 --max-score 0.2 to purge down-votes on weak matches."),
    title: str | None = typer.Option(None, "--title", help="Only delete entries whose track title contains this substring (case-insensitive). Resolved against the indexed library; tracks missing from the library are skipped. Pair with --dry-run first to preview which votes would be removed."),
    artist: str | None = typer.Option(None, "--artist", help="Only delete entries whose track artist contains this substring (case-insensitive). Resolved against the indexed library; tracks missing from the library are skipped."),
    orphaned: bool = typer.Option(False, "--orphaned", help="Only delete entries whose track_id is no longer in the indexed library. Intended for purging stale votes after pruning the library. Pair with --dry-run first."),
    in_index: bool = typer.Option(False, "--in-index", help="Only delete entries whose track_id is still present in the indexed library. Skips orphaned feedback so a name- or score-based purge cannot accidentally wipe votes whose tracks have already been pruned."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report how many rows would be deleted without writing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Delete recorded feedback rows (undo a misclicked vote, or age out old data).

    At least one of --query-id, --track-id, --vote, --since, --until,
    --min-score, --max-score, --title, --artist, --orphaned, or --in-index
    must be supplied so a bare invocation can never wipe the whole feedback
    log. Rows without a numeric ts are never matched by --since / --until
    and rows without a numeric score are never matched by --min-score /
    --max-score so undated or unscored entries are not silently purged.
    --title / --artist are resolved against the indexed library, so feedback
    whose track has been removed from the index is never deleted by a
    name-based purge. --orphaned deletes only votes pointing at track_ids
    no longer in the index (typical use: clean up after a library prune);
    --in-index is its inverse and is mutually exclusive with --orphaned.
    """
    if (
        query_id is None and track_id is None and vote is None
        and since is None and until is None
        and min_score is None and max_score is None
        and title is None and artist is None
        and not orphaned and not in_index
    ):
        raise typer.BadParameter("supply at least one of --query-id, --track-id, --vote, --since, --until, --min-score, --max-score, --title, --artist, --orphaned, --in-index")
    if orphaned and in_index:
        raise typer.BadParameter("--orphaned and --in-index are mutually exclusive")
    if vote is not None and vote not in (1, -1):
        raise typer.BadParameter("--vote must be 1 or -1")
    if min_score is not None and max_score is not None and min_score > max_score:
        raise typer.BadParameter("--min-score must be <= --max-score")
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    s = get_settings()
    from clawhum_library.feedback import read_feedback, delete_feedback as _delete_feedback

    # Resolve --title / --artist / --in-index to a concrete set of track_ids
    # by joining against the indexed library. An empty resolved set means
    # "no track matched", which we treat as "nothing to delete" rather than
    # silently degrading to no-filter (which would wipe everything).
    # --orphaned is the inverse: it builds the allowlist from feedback rows
    # whose track_id is NOT in the index, so it can clean up stale votes
    # after a library prune.
    resolved_track_ids: set[str] | None = None
    needs_meta = title is not None or artist is not None or orphaned or in_index
    meta = _load_track_metadata() if needs_meta else None
    if title is not None or artist is not None or in_index:
        t_needle = title.lower() if title is not None else None
        a_needle = artist.lower() if artist is not None else None
        resolved_track_ids = set()
        for tid, (t_val, a_val) in (meta or {}).items():
            if t_needle is not None and t_needle not in (t_val or "").lower():
                continue
            if a_needle is not None and a_needle not in (a_val or "").lower():
                continue
            resolved_track_ids.add(tid)
        if not resolved_track_ids:
            console.print("[dim]no matching feedback entries[/dim]")
            return

    rows = read_feedback(s.feedback_path)
    if orphaned:
        known = set((meta or {}).keys())
        orphan_ids = {str(r.get("track_id", "")) for r in rows if str(r.get("track_id", "")) not in known}
        orphan_ids.discard("")
        if not orphan_ids:
            console.print("[dim]no matching feedback entries[/dim]")
            return
        resolved_track_ids = orphan_ids
    matches = _filter_feedback(
        rows, query_id=query_id, track_id=track_id, track_ids=resolved_track_ids, vote=vote,
        since=since_ts, until=until_ts,
        min_score=min_score, max_score=max_score,
    )
    if not matches:
        console.print("[dim]no matching feedback entries[/dim]")
        return
    if dry_run:
        console.print(f"[yellow]would delete {len(matches)} entry(s)[/yellow]")
        return
    if not yes:
        confirm = typer.confirm(f"Delete {len(matches)} feedback entry(s)?", default=False)
        if not confirm:
            console.print("[dim]aborted[/dim]")
            raise typer.Exit(code=1)
    removed = _delete_feedback(
        s.feedback_path, query_id=query_id, track_id=track_id, track_ids=resolved_track_ids, vote=vote,
        since=since_ts, until=until_ts,
        min_score=min_score, max_score=max_score,
    )
    console.print(f"[green]deleted {removed} entry(s)[/green]")


def _parse_time_bound(value: str, *, flag: str) -> float:
    """Parse a CLI time bound as either a unix epoch seconds value or an ISO-8601
    date/datetime. Naive ISO inputs are treated as UTC. Raises typer.BadParameter
    on bad input so the user gets a clean error instead of a traceback.
    """
    from datetime import datetime, timezone

    v = value.strip()
    if not v:
        raise typer.BadParameter(f"{flag} must not be empty")
    # numeric epoch seconds (int or float). Reject implausibly small bare
    # integers (e.g. "2024" or "20240101") because a user almost certainly
    # meant a calendar year/date, not 2024 seconds after 1970-01-01. Without
    # this guard those inputs silently match nearly every row. Floats with an
    # explicit decimal point are accepted as-is so tests like 0.0 still work.
    try:
        n = float(v)
        if "." in v or "e" in v or "E" in v or n >= 100_000_000:
            return n
        raise typer.BadParameter(
            f"{flag}={value!r} looks like a year/date, not a unix timestamp "
            f"(epoch seconds must be >= 100000000, i.e. >= 1973-03-03). "
            f"Use an ISO-8601 date like 2024-01-01 instead."
        )
    except ValueError:
        pass
    # ISO-8601: accept trailing Z as UTC
    iso = v[:-1] + "+00:00" if v.endswith("Z") else v
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as e:
        raise typer.BadParameter(
            f"{flag} must be a unix timestamp or ISO-8601 date/datetime (got {value!r})"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _filter_feedback(
    rows: list[dict],
    query_id: str | None = None,
    track_id: str | None = None,
    track_ids: set[str] | None = None,
    vote: int | None = None,
    since: float | None = None,
    until: float | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
) -> list[dict]:
    out = rows
    if query_id is not None:
        out = [r for r in out if r.get("query_id") == query_id]
    if track_id is not None:
        out = [r for r in out if r.get("track_id") == track_id]
    if track_ids is not None:
        out = [r for r in out if str(r.get("track_id", "")) in track_ids]
    if vote is not None:
        out = [r for r in out if r.get("vote") == vote]
    if since is not None:
        out = [r for r in out if isinstance(r.get("ts"), (int, float)) and r["ts"] >= since]
    if until is not None:
        out = [r for r in out if isinstance(r.get("ts"), (int, float)) and r["ts"] < until]
    if min_score is not None:
        out = [r for r in out if isinstance(r.get("score"), (int, float)) and r["score"] >= min_score]
    if max_score is not None:
        out = [r for r in out if isinstance(r.get("score"), (int, float)) and r["score"] <= max_score]
    return out


def _feedback_as_csv(rows: list[dict], enrich: bool = False) -> str:
    buf = io.StringIO()
    fields = ["ts", "query_id", "track_id", "score", "vote"]
    if enrich:
        fields += ["title", "artist"]
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fields})
    return buf.getvalue()


def _enrich_feedback_rows(rows: list[dict], meta: dict[str, tuple[str, str]]) -> list[dict]:
    for r in rows:
        title, artist = meta.get(str(r.get("track_id", "")), ("", ""))
        r["title"] = title
        r["artist"] = artist
    return rows


@app.command("feedback-list")
def feedback_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Show the most recent N entries (0 for all)."),
    query_id: str | None = typer.Option(None, "--query-id", help="Only show entries for this query_id."),
    track_id: str | None = typer.Option(None, "--track-id", help="Only show entries for this track_id."),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        "-x",
        help="Drop entries for this track_id from the listing. Repeatable. Useful for looking past a known-noisy track (e.g. a duplicate edition that dominates recent votes) without re-querying.",
    ),
    vote: int | None = typer.Option(None, "--vote", help="Filter by vote: 1 (up) or -1 (down)."),
    since: str | None = typer.Option(None, "--since", help="Only entries at/after this time (unix seconds or ISO-8601, naive = UTC)."),
    until: str | None = typer.Option(None, "--until", help="Only entries strictly before this time (unix seconds or ISO-8601, naive = UTC)."),
    min_score: float | None = typer.Option(None, "--min-score", help="Only entries whose recorded score is at least this. Entries without a numeric score are excluded."),
    max_score: float | None = typer.Option(None, "--max-score", help="Only entries whose recorded score is at most this. Entries without a numeric score are excluded. Combine with --vote -1 to find down-votes on high-confidence matches (false positives)."),
    title: str | None = typer.Option(None, "--title", help="Only entries whose track title contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    artist: str | None = typer.Option(None, "--artist", help="Only entries whose track artist contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv."),
    enrich: bool = typer.Option(False, "--enrich", help="Join with the indexed library to add title/artist columns. Unknown tracks show blank values."),
    orphaned: bool = typer.Option(False, "--orphaned", help="Only show entries whose track_id is no longer in the indexed library. Useful after pruning the library to find stale votes to delete with feedback-delete."),
    in_index: bool = typer.Option(False, "--in-index", help="Only show entries whose track_id is still present in the indexed library. Skips orphaned feedback so the list reflects the live catalog."),
    sort: str = typer.Option("ts", "--sort", help="Sort order: ts (newest first, default), ts-asc (oldest first), score (highest first), score-asc (lowest first), track_id (asc). Entries missing a numeric ts/score sort last."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
):
    """List recorded feedback (most recent first)."""
    if orphaned and in_index:
        raise typer.BadParameter("--orphaned and --in-index are mutually exclusive")
    sort_key = sort.lower()
    if sort_key not in {"ts", "ts-asc", "score", "score-asc", "track_id"}:
        raise typer.BadParameter("--sort must be one of: ts, ts-asc, score, score-asc, track_id")
    chosen = fmt.lower()
    if chosen not in {"table", "json", "csv"}:
        raise typer.BadParameter("--format must be one of: table, json, csv")
    if vote is not None and vote not in (1, -1):
        raise typer.BadParameter("--vote must be 1 or -1")
    if limit < 0:
        raise typer.BadParameter("--limit must be >= 0")
    if min_score is not None and max_score is not None and min_score > max_score:
        raise typer.BadParameter("--min-score must be <= --max-score")
    if output is not None and chosen == "table":
        chosen = "csv"
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    excluded_ids = {t.strip() for t in (exclude_track or []) if t and t.strip()}

    s = get_settings()
    from clawhum_library.feedback import read_feedback
    rows = read_feedback(s.feedback_path)
    rows = _filter_feedback(
        rows, query_id=query_id, track_id=track_id, vote=vote,
        since=since_ts, until=until_ts,
        min_score=min_score, max_score=max_score,
    )
    if excluded_ids:
        rows = [r for r in rows if str(r.get("track_id", "")) not in excluded_ids]
    # --title/--artist/--orphaned/--in-index need metadata to filter on, so
    # auto-enrich. --title/--artist additionally drop rows whose track isn't in
    # the indexed library (can't match a blank).
    needs_meta = enrich or title is not None or artist is not None or orphaned or in_index
    meta = _load_track_metadata() if needs_meta else None
    if orphaned:
        known = set((meta or {}).keys())
        rows = [r for r in rows if str(r.get("track_id", "")) not in known]
    elif in_index:
        known = set((meta or {}).keys())
        rows = [r for r in rows if str(r.get("track_id", "")) in known]
    if title is not None or artist is not None:
        t_needle = title.lower() if title is not None else None
        a_needle = artist.lower() if artist is not None else None
        filtered = []
        for r in rows:
            entry = meta.get(str(r.get("track_id", ""))) if meta else None
            if entry is None:
                continue
            t_val, a_val = entry
            if t_needle is not None and t_needle not in (t_val or "").lower():
                continue
            if a_needle is not None and a_needle not in (a_val or "").lower():
                continue
            filtered.append(r)
        rows = filtered
    # default: most recent first; entries without a numeric ts always sort last
    def _num_key(r: dict, field: str) -> tuple[int, float]:
        v = r.get(field)
        if isinstance(v, (int, float)):
            return (0, float(v))
        return (1, 0.0)

    if sort_key == "ts":
        rows.sort(key=lambda r: (_num_key(r, "ts")[0], -_num_key(r, "ts")[1]))
    elif sort_key == "ts-asc":
        rows.sort(key=lambda r: (_num_key(r, "ts")[0], _num_key(r, "ts")[1]))
    elif sort_key == "score":
        rows.sort(key=lambda r: (_num_key(r, "score")[0], -_num_key(r, "score")[1]))
    elif sort_key == "score-asc":
        rows.sort(key=lambda r: (_num_key(r, "score")[0], _num_key(r, "score")[1]))
    else:  # track_id
        rows.sort(key=lambda r: str(r.get("track_id", "")))
    if limit > 0:
        rows = rows[:limit]
    if needs_meta:
        _enrich_feedback_rows(rows, meta or {})
    enrich = needs_meta

    if chosen == "json":
        payload = json.dumps(rows)
        if output is not None:
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]wrote {len(rows)} entry(s) to {output}[/green]")
        else:
            console.print_json(payload)
        return
    if chosen == "csv":
        payload = _feedback_as_csv(rows, enrich=enrich)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            console.print(f"[green]wrote {len(rows)} entry(s) to {output}[/green]")
        else:
            sys.stdout.write(payload)
            sys.stdout.flush()
        return

    if not rows:
        console.print("[dim]no feedback recorded yet[/dim]")
        return
    from datetime import datetime, timezone
    table = Table(title=f"Feedback ({len(rows)})")
    table.add_column("When")
    table.add_column("Vote", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Track ID")
    if enrich:
        table.add_column("Title")
        table.add_column("Artist")
    table.add_column("Query ID")
    for r in rows:
        ts = r.get("ts")
        when = (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
            if isinstance(ts, (int, float))
            else ""
        )
        v = r.get("vote")
        vote_cell = "+1" if v == 1 else ("-1" if v == -1 else str(v))
        score = r.get("score")
        score_cell = f"{score:.3f}" if isinstance(score, (int, float)) else ""
        cells = [when, vote_cell, score_cell, str(r.get("track_id", ""))]
        if enrich:
            cells += [str(r.get("title", "")), str(r.get("artist", ""))]
        cells.append(str(r.get("query_id", "")))
        table.add_row(*cells)
    console.print(table)


def _wilson_lower_bound(up: int, total: int, z: float = 1.96) -> float | None:
    """Wilson lower bound of the up/total ratio at the given z (default 95% CI).

    Returns None when there are no votes. Ranking by this value penalises tracks
    with few votes, which is the standard "best" sort for thumbs up/down data.
    """
    if total <= 0:
        return None
    phat = up / total
    z2 = z * z
    denom = 1.0 + z2 / total
    centre = phat + z2 / (2 * total)
    margin = z * ((phat * (1 - phat) + z2 / (4 * total)) / total) ** 0.5
    return (centre - margin) / denom


def _aggregate_feedback(rows: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        tid = r.get("track_id")
        if not tid:
            continue
        a = agg.setdefault(tid, {"track_id": tid, "up": 0, "down": 0, "score_sum": 0.0, "score_n": 0})
        v = r.get("vote")
        if v == 1:
            a["up"] += 1
        elif v == -1:
            a["down"] += 1
        s = r.get("score")
        if isinstance(s, (int, float)):
            a["score_sum"] += float(s)
            a["score_n"] += 1
    out = []
    for a in agg.values():
        total = a["up"] + a["down"]
        net = a["up"] - a["down"]
        avg = (a["score_sum"] / a["score_n"]) if a["score_n"] else None
        approval = (a["up"] / total) if total else None
        wilson = _wilson_lower_bound(a["up"], total)
        out.append({
            "track_id": a["track_id"],
            "up": a["up"],
            "down": a["down"],
            "total": total,
            "net": net,
            "avg_score": avg,
            "approval": approval,
            "wilson": wilson,
        })
    return out


def _sort_feedback_stats(rows: list[dict], sort: str) -> list[dict]:
    key = sort.lower()
    if key == "net":
        rows.sort(key=lambda r: (r["net"], r["total"]), reverse=True)
    elif key == "up":
        rows.sort(key=lambda r: r["up"], reverse=True)
    elif key == "down":
        rows.sort(key=lambda r: r["down"], reverse=True)
    elif key == "total":
        rows.sort(key=lambda r: r["total"], reverse=True)
    elif key == "track_id":
        rows.sort(key=lambda r: r["track_id"])
    elif key == "avg_score":
        # tracks with no avg_score (no numeric scores recorded) sort last
        rows.sort(
            key=lambda r: (
                0 if isinstance(r.get("avg_score"), (int, float)) else 1,
                -(r["avg_score"] if isinstance(r.get("avg_score"), (int, float)) else 0.0),
                -r["total"],
            )
        )
    elif key == "approval":
        # tracks with no votes (approval=None) sort last; ties broken by total then track_id
        rows.sort(
            key=lambda r: (
                0 if isinstance(r.get("approval"), (int, float)) else 1,
                -(r["approval"] if isinstance(r.get("approval"), (int, float)) else 0.0),
                -r["total"],
                r["track_id"],
            )
        )
    elif key == "wilson":
        # tracks with no votes (wilson=None) sort last; ties broken by total then track_id.
        # Wilson lower-bound ranks high-approval tracks with more votes above
        # high-approval tracks with only a vote or two, which is what you want
        # when triaging "best matches so far".
        rows.sort(
            key=lambda r: (
                0 if isinstance(r.get("wilson"), (int, float)) else 1,
                -(r["wilson"] if isinstance(r.get("wilson"), (int, float)) else 0.0),
                -r["total"],
                r["track_id"],
            )
        )
    else:
        raise typer.BadParameter("--sort must be one of: net, up, down, total, track_id, avg_score, approval, wilson")
    return rows


def _feedback_stats_as_csv(rows: list[dict], enrich: bool = False) -> str:
    buf = io.StringIO()
    fields = ["track_id", "up", "down", "total", "net", "avg_score", "approval", "wilson"]
    if enrich:
        fields += ["title", "artist"]
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        out = {k: row.get(k) for k in fields}
        if out["avg_score"] is None:
            out["avg_score"] = ""
        else:
            out["avg_score"] = f"{out['avg_score']:.6f}"
        if out["approval"] is None:
            out["approval"] = ""
        else:
            out["approval"] = f"{out['approval']:.6f}"
        if out.get("wilson") is None:
            out["wilson"] = ""
        else:
            out["wilson"] = f"{out['wilson']:.6f}"
        if enrich:
            out["title"] = "" if out.get("title") is None else out["title"]
            out["artist"] = "" if out.get("artist") is None else out["artist"]
        writer.writerow(out)
    return buf.getvalue()


def _load_track_metadata() -> dict[str, tuple[str, str]]:
    """Load track id -> (title, artist) from the configured metadata file.

    Returns an empty dict if metadata is unavailable so --enrich degrades
    gracefully (columns appear but values are blank) rather than crashing.
    """
    s = get_settings()
    try:
        from clawhum_index.persistence import read_metadata
        return {t.id: (t.title or "", t.artist or "") for t in read_metadata(s.metadata_path)}
    except Exception:
        return {}


def _enrich_stats(rows: list[dict], meta: dict[str, tuple[str, str]]) -> list[dict]:
    for r in rows:
        title, artist = meta.get(r.get("track_id", ""), ("", ""))
        r["title"] = title
        r["artist"] = artist
    return rows


@app.command("feedback-stats")
def feedback_stats(
    sort: str = typer.Option("net", "--sort", "-s", help="Sort by: net, up, down, total, track_id, avg_score, approval, wilson (Wilson 95% lower bound of approval, penalises few-vote tracks)."),
    limit: int = typer.Option(0, "--limit", "-n", help="Show top N rows (0 for all)."),
    min_total: int = typer.Option(0, "--min-total", help="Only include tracks with at least this many votes."),
    max_total: int | None = typer.Option(None, "--max-total", help="Only include tracks with at most this many votes. Useful for surfacing sparsely-voted tracks that need more feedback before their stats can be trusted. Pair with --min-total 1 to skip zero-vote rows."),
    min_up: int = typer.Option(0, "--min-up", help="Only include tracks with at least this many up-votes."),
    min_down: int = typer.Option(0, "--min-down", help="Only include tracks with at least this many down-votes. Useful for surfacing clearly problematic matches without needing a net-score threshold."),
    min_net: int | None = typer.Option(None, "--min-net", help="Only include tracks whose net score (up - down) is at least this. Negative values allowed."),
    max_net: int | None = typer.Option(None, "--max-net", help="Only include tracks whose net score (up - down) is at most this. Negative values allowed. Combine with --max-net -2 to surface tracks the audience clearly rejects."),
    min_approval: float | None = typer.Option(None, "--min-approval", help="Only include tracks whose up/total ratio is at least this (0.0 to 1.0). Tracks with no votes are excluded."),
    max_approval: float | None = typer.Option(None, "--max-approval", help="Only include tracks whose up/total ratio is at most this (0.0 to 1.0). Tracks with no votes are excluded. Useful for finding low-approval matches."),
    min_avg_score: float | None = typer.Option(None, "--min-avg-score", help="Only include tracks whose avg_score is at least this (0.0 to 1.0). Tracks with no numeric scores are excluded."),
    max_avg_score: float | None = typer.Option(None, "--max-avg-score", help="Only include tracks whose avg_score is at most this (0.0 to 1.0). Tracks with no numeric scores are excluded. Useful for finding weak-score matches."),
    min_wilson: float | None = typer.Option(None, "--min-wilson", help="Only include tracks whose Wilson 95% lower bound is at least this (0.0 to 1.0). Penalises few-vote tracks, so this surfaces tracks with both high approval and enough evidence to trust it."),
    max_wilson: float | None = typer.Option(None, "--max-wilson", help="Only include tracks whose Wilson 95% lower bound is at most this (0.0 to 1.0). Useful for surfacing tracks whose approval is statistically weak (low evidence or low ratio)."),
    track_id: str | None = typer.Option(None, "--track-id", help="Only show this track_id."),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        "-x",
        help="Drop this track_id from the aggregation. Repeatable. Useful for looking past known-good or known-bad tracks (e.g. a duplicate edition that dominates the top of the list) without re-querying.",
    ),
    title: str | None = typer.Option(None, "--title", help="Only show tracks whose title contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    artist: str | None = typer.Option(None, "--artist", help="Only show tracks whose artist contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    since: str | None = typer.Option(None, "--since", help="Only aggregate entries at/after this time (unix seconds or ISO-8601, naive = UTC)."),
    until: str | None = typer.Option(None, "--until", help="Only aggregate entries strictly before this time (unix seconds or ISO-8601, naive = UTC)."),
    enrich: bool = typer.Option(False, "--enrich", help="Join with the indexed library to add title/artist columns. Unknown tracks show blank values."),
    orphaned: bool = typer.Option(False, "--orphaned", help="Only show tracks with feedback that are no longer in the indexed library. Useful after pruning the library to find stale feedback to delete."),
    in_index: bool = typer.Option(False, "--in-index", help="Only show tracks that are still present in the indexed library. Skips orphaned feedback so stats reflect the live catalog."),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
):
    """Aggregate recorded feedback per track (up / down / net / avg score)."""
    if orphaned and in_index:
        raise typer.BadParameter("--orphaned and --in-index are mutually exclusive")
    chosen = fmt.lower()
    if chosen not in {"table", "json", "csv"}:
        raise typer.BadParameter("--format must be one of: table, json, csv")
    if limit < 0:
        raise typer.BadParameter("--limit must be >= 0")
    if min_total < 0:
        raise typer.BadParameter("--min-total must be >= 0")
    if max_total is not None and max_total < 0:
        raise typer.BadParameter("--max-total must be >= 0")
    if max_total is not None and min_total > max_total:
        raise typer.BadParameter("--min-total must be <= --max-total")
    if min_up < 0:
        raise typer.BadParameter("--min-up must be >= 0")
    if min_down < 0:
        raise typer.BadParameter("--min-down must be >= 0")
    if min_net is not None and max_net is not None and min_net > max_net:
        raise typer.BadParameter("--min-net must be <= --max-net")
    if min_approval is not None and not (0.0 <= min_approval <= 1.0):
        raise typer.BadParameter("--min-approval must be between 0.0 and 1.0")
    if max_approval is not None and not (0.0 <= max_approval <= 1.0):
        raise typer.BadParameter("--max-approval must be between 0.0 and 1.0")
    if min_approval is not None and max_approval is not None and min_approval > max_approval:
        raise typer.BadParameter("--min-approval must be <= --max-approval")
    if min_avg_score is not None and not (0.0 <= min_avg_score <= 1.0):
        raise typer.BadParameter("--min-avg-score must be between 0.0 and 1.0")
    if max_avg_score is not None and not (0.0 <= max_avg_score <= 1.0):
        raise typer.BadParameter("--max-avg-score must be between 0.0 and 1.0")
    if min_avg_score is not None and max_avg_score is not None and min_avg_score > max_avg_score:
        raise typer.BadParameter("--min-avg-score must be <= --max-avg-score")
    if min_wilson is not None and not (0.0 <= min_wilson <= 1.0):
        raise typer.BadParameter("--min-wilson must be between 0.0 and 1.0")
    if max_wilson is not None and not (0.0 <= max_wilson <= 1.0):
        raise typer.BadParameter("--max-wilson must be between 0.0 and 1.0")
    if min_wilson is not None and max_wilson is not None and min_wilson > max_wilson:
        raise typer.BadParameter("--min-wilson must be <= --max-wilson")
    if output is not None and chosen == "table":
        chosen = "csv"
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    excluded_ids = {t.strip() for t in (exclude_track or []) if t and t.strip()}

    s = get_settings()
    from clawhum_library.feedback import read_feedback
    rows = read_feedback(s.feedback_path)
    if track_id is not None:
        rows = [r for r in rows if r.get("track_id") == track_id]
    if excluded_ids:
        rows = [r for r in rows if str(r.get("track_id", "")) not in excluded_ids]
    if since_ts is not None:
        rows = [r for r in rows if isinstance(r.get("ts"), (int, float)) and r["ts"] >= since_ts]
    if until_ts is not None:
        rows = [r for r in rows if isinstance(r.get("ts"), (int, float)) and r["ts"] < until_ts]
    stats = _aggregate_feedback(rows)
    if min_total > 0:
        stats = [r for r in stats if r["total"] >= min_total]
    if max_total is not None:
        stats = [r for r in stats if r["total"] <= max_total]
    if min_up > 0:
        stats = [r for r in stats if r["up"] >= min_up]
    if min_down > 0:
        stats = [r for r in stats if r["down"] >= min_down]
    if min_net is not None:
        stats = [r for r in stats if r["net"] >= min_net]
    if max_net is not None:
        stats = [r for r in stats if r["net"] <= max_net]
    if min_approval is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("approval"), (int, float)) and r["approval"] >= min_approval
        ]
    if max_approval is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("approval"), (int, float)) and r["approval"] <= max_approval
        ]
    if min_avg_score is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("avg_score"), (int, float)) and r["avg_score"] >= min_avg_score
        ]
    if max_avg_score is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("avg_score"), (int, float)) and r["avg_score"] <= max_avg_score
        ]
    if min_wilson is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("wilson"), (int, float)) and r["wilson"] >= min_wilson
        ]
    if max_wilson is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("wilson"), (int, float)) and r["wilson"] <= max_wilson
        ]
    _sort_feedback_stats(stats, sort)
    # Filter by library membership before enrichment so we only load metadata once.
    meta_cache: dict[str, tuple[str, str]] | None = None
    if orphaned or in_index:
        meta_cache = _load_track_metadata()
        known = set(meta_cache.keys())
        if orphaned:
            stats = [r for r in stats if r.get("track_id") not in known]
        else:
            stats = [r for r in stats if r.get("track_id") in known]
    # --title/--artist need metadata to filter on, so auto-enrich and drop
    # orphans (we can't prove a track without metadata matches a name needle).
    needs_meta = enrich or title is not None or artist is not None
    if needs_meta and meta_cache is None:
        meta_cache = _load_track_metadata()
    if title is not None or artist is not None:
        t_needle = title.lower() if title is not None else None
        a_needle = artist.lower() if artist is not None else None
        filtered = []
        for r in stats:
            entry = (meta_cache or {}).get(str(r.get("track_id", "")))
            if entry is None:
                continue
            t_val, a_val = entry
            if t_needle is not None and t_needle not in (t_val or "").lower():
                continue
            if a_needle is not None and a_needle not in (a_val or "").lower():
                continue
            filtered.append(r)
        stats = filtered
    if limit > 0:
        stats = stats[:limit]
    if needs_meta:
        _enrich_stats(stats, meta_cache or {})
        enrich = True

    if chosen == "json":
        payload = json.dumps(stats)
        if output is not None:
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]wrote {len(stats)} row(s) to {output}[/green]")
        else:
            console.print_json(payload)
        return
    if chosen == "csv":
        payload = _feedback_stats_as_csv(stats, enrich=enrich)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            console.print(f"[green]wrote {len(stats)} row(s) to {output}[/green]")
        else:
            sys.stdout.write(payload)
            sys.stdout.flush()
        return

    if not stats:
        console.print("[dim]no feedback recorded yet[/dim]")
        return
    table = Table(title=f"Feedback stats ({len(stats)} track(s), sort={sort})")
    table.add_column("Track ID")
    if enrich:
        table.add_column("Title")
        table.add_column("Artist")
    table.add_column("Up", justify="right")
    table.add_column("Down", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Net", justify="right")
    table.add_column("Avg score", justify="right")
    table.add_column("Approval", justify="right")
    table.add_column("Wilson", justify="right")
    for r in stats:
        avg = r["avg_score"]
        avg_cell = f"{avg:.3f}" if isinstance(avg, (int, float)) else ""
        ap = r.get("approval")
        ap_cell = f"{ap * 100:.1f}%" if isinstance(ap, (int, float)) else ""
        w = r.get("wilson")
        w_cell = f"{w:.3f}" if isinstance(w, (int, float)) else ""
        cells = [str(r["track_id"])]
        if enrich:
            cells += [str(r.get("title", "")), str(r.get("artist", ""))]
        cells += [
            str(r["up"]),
            str(r["down"]),
            str(r["total"]),
            str(r["net"]),
            avg_cell,
            ap_cell,
            w_cell,
        ]
        table.add_row(*cells)
    console.print(table)


if __name__ == "__main__":
    app()
