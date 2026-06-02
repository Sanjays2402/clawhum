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
        raise typer.Exit("index empty; run `clawhum index <dir>` first")
    x, sr = load_audio(query, target_sr=state.embedder.sr)
    matcher = Matcher(state.embedder, state.index, state.tracks)
    results = matcher.match(x, sr, top_k=top_k, threshold=threshold)
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
    dry_run: bool = typer.Option(False, "--dry-run", help="Report how many rows would be deleted without writing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Delete recorded feedback rows (undo a misclicked vote).

    At least one of --query-id, --track-id, or --vote must be supplied so a
    bare invocation can never wipe the whole feedback log.
    """
    if query_id is None and track_id is None and vote is None:
        raise typer.BadParameter("supply at least one of --query-id, --track-id, --vote")
    if vote is not None and vote not in (1, -1):
        raise typer.BadParameter("--vote must be 1 or -1")

    s = get_settings()
    from clawhum_library.feedback import read_feedback, delete_feedback as _delete_feedback

    rows = read_feedback(s.feedback_path)
    matches = _filter_feedback(rows, query_id=query_id, track_id=track_id, vote=vote)
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
        s.feedback_path, query_id=query_id, track_id=track_id, vote=vote
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
    # numeric epoch seconds (int or float)
    try:
        return float(v)
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
    vote: int | None = None,
    since: float | None = None,
    until: float | None = None,
) -> list[dict]:
    out = rows
    if query_id is not None:
        out = [r for r in out if r.get("query_id") == query_id]
    if track_id is not None:
        out = [r for r in out if r.get("track_id") == track_id]
    if vote is not None:
        out = [r for r in out if r.get("vote") == vote]
    if since is not None:
        out = [r for r in out if isinstance(r.get("ts"), (int, float)) and r["ts"] >= since]
    if until is not None:
        out = [r for r in out if isinstance(r.get("ts"), (int, float)) and r["ts"] < until]
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
    vote: int | None = typer.Option(None, "--vote", help="Filter by vote: 1 (up) or -1 (down)."),
    since: str | None = typer.Option(None, "--since", help="Only entries at/after this time (unix seconds or ISO-8601, naive = UTC)."),
    until: str | None = typer.Option(None, "--until", help="Only entries strictly before this time (unix seconds or ISO-8601, naive = UTC)."),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv."),
    enrich: bool = typer.Option(False, "--enrich", help="Join with the indexed library to add title/artist columns. Unknown tracks show blank values."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
):
    """List recorded feedback (most recent first)."""
    chosen = fmt.lower()
    if chosen not in {"table", "json", "csv"}:
        raise typer.BadParameter("--format must be one of: table, json, csv")
    if vote is not None and vote not in (1, -1):
        raise typer.BadParameter("--vote must be 1 or -1")
    if limit < 0:
        raise typer.BadParameter("--limit must be >= 0")
    if output is not None and chosen == "table":
        chosen = "csv"
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    s = get_settings()
    from clawhum_library.feedback import read_feedback
    rows = read_feedback(s.feedback_path)
    rows = _filter_feedback(
        rows, query_id=query_id, track_id=track_id, vote=vote,
        since=since_ts, until=until_ts,
    )
    # most recent first; entries without ts sort last
    rows.sort(key=lambda r: r.get("ts") or 0.0, reverse=True)
    if limit > 0:
        rows = rows[:limit]
    if enrich:
        _enrich_feedback_rows(rows, _load_track_metadata())

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
        out.append({
            "track_id": a["track_id"],
            "up": a["up"],
            "down": a["down"],
            "total": total,
            "net": net,
            "avg_score": avg,
            "approval": approval,
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
    else:
        raise typer.BadParameter("--sort must be one of: net, up, down, total, track_id, avg_score, approval")
    return rows


def _feedback_stats_as_csv(rows: list[dict], enrich: bool = False) -> str:
    buf = io.StringIO()
    fields = ["track_id", "up", "down", "total", "net", "avg_score", "approval"]
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
    sort: str = typer.Option("net", "--sort", "-s", help="Sort by: net, up, down, total, track_id, avg_score, approval."),
    limit: int = typer.Option(0, "--limit", "-n", help="Show top N rows (0 for all)."),
    min_total: int = typer.Option(0, "--min-total", help="Only include tracks with at least this many votes."),
    min_approval: float | None = typer.Option(None, "--min-approval", help="Only include tracks whose up/total ratio is at least this (0.0 to 1.0). Tracks with no votes are excluded."),
    min_avg_score: float | None = typer.Option(None, "--min-avg-score", help="Only include tracks whose avg_score is at least this (0.0 to 1.0). Tracks with no numeric scores are excluded."),
    track_id: str | None = typer.Option(None, "--track-id", help="Only show this track_id."),
    since: str | None = typer.Option(None, "--since", help="Only aggregate entries at/after this time (unix seconds or ISO-8601, naive = UTC)."),
    until: str | None = typer.Option(None, "--until", help="Only aggregate entries strictly before this time (unix seconds or ISO-8601, naive = UTC)."),
    enrich: bool = typer.Option(False, "--enrich", help="Join with the indexed library to add title/artist columns. Unknown tracks show blank values."),
    fmt: str = typer.Option("table", "--format", "-f", help="Output format: table, json, csv."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
):
    """Aggregate recorded feedback per track (up / down / net / avg score)."""
    chosen = fmt.lower()
    if chosen not in {"table", "json", "csv"}:
        raise typer.BadParameter("--format must be one of: table, json, csv")
    if limit < 0:
        raise typer.BadParameter("--limit must be >= 0")
    if min_total < 0:
        raise typer.BadParameter("--min-total must be >= 0")
    if min_approval is not None and not (0.0 <= min_approval <= 1.0):
        raise typer.BadParameter("--min-approval must be between 0.0 and 1.0")
    if min_avg_score is not None and not (0.0 <= min_avg_score <= 1.0):
        raise typer.BadParameter("--min-avg-score must be between 0.0 and 1.0")
    if output is not None and chosen == "table":
        chosen = "csv"
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    s = get_settings()
    from clawhum_library.feedback import read_feedback
    rows = read_feedback(s.feedback_path)
    if track_id is not None:
        rows = [r for r in rows if r.get("track_id") == track_id]
    if since_ts is not None:
        rows = [r for r in rows if isinstance(r.get("ts"), (int, float)) and r["ts"] >= since_ts]
    if until_ts is not None:
        rows = [r for r in rows if isinstance(r.get("ts"), (int, float)) and r["ts"] < until_ts]
    stats = _aggregate_feedback(rows)
    if min_total > 0:
        stats = [r for r in stats if r["total"] >= min_total]
    if min_approval is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("approval"), (int, float)) and r["approval"] >= min_approval
        ]
    if min_avg_score is not None:
        stats = [
            r for r in stats
            if isinstance(r.get("avg_score"), (int, float)) and r["avg_score"] >= min_avg_score
        ]
    _sort_feedback_stats(stats, sort)
    if limit > 0:
        stats = stats[:limit]
    if enrich:
        _enrich_stats(stats, _load_track_metadata())

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
    for r in stats:
        avg = r["avg_score"]
        avg_cell = f"{avg:.3f}" if isinstance(avg, (int, float)) else ""
        ap = r.get("approval")
        ap_cell = f"{ap * 100:.1f}%" if isinstance(ap, (int, float)) else ""
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
        ]
        table.add_row(*cells)
    console.print(table)


if __name__ == "__main__":
    app()
