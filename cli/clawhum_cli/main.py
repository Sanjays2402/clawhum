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


def _downvoted_track_ids(rows: list[dict]) -> set[str]:
    """Return track_ids whose recorded feedback is net negative (down > up).

    Used by ``clawhum match --exclude-downvoted`` to auto-drop tracks the user
    has previously rejected. A single stray down-vote does not blacklist a
    track: only tracks where down-votes strictly outnumber up-votes qualify,
    so an accidental thumbs-down on a track the user later up-voted twice
    will not silently disappear from future matches. Rows with missing or
    non-integer ``track_id`` / ``vote`` fields are ignored rather than
    raising, so a malformed feedback line can never crash ``match``.
    """
    up: dict[str, int] = {}
    down: dict[str, int] = {}
    for r in rows:
        tid = r.get("track_id")
        if not isinstance(tid, str) or not tid:
            continue
        v = r.get("vote")
        if v == 1:
            up[tid] = up.get(tid, 0) + 1
        elif v == -1:
            down[tid] = down.get(tid, 0) + 1
    return {tid for tid, d in down.items() if d > up.get(tid, 0)}


def _upvoted_track_ids(rows: list[dict]) -> set[str]:
    """Return track_ids whose recorded feedback is net positive (up > down).

    Used by ``clawhum match --only-upvoted`` to restrict matches to tracks the
    user has previously liked. The threshold is strict: a single up-vote that
    is later cancelled by an equal down-vote does not qualify, so a track only
    survives once the user has clearly stood by it. Rows with missing or
    non-integer ``track_id`` / ``vote`` fields are ignored rather than raising,
    so a malformed feedback line can never crash ``match``.
    """
    up: dict[str, int] = {}
    down: dict[str, int] = {}
    for r in rows:
        tid = r.get("track_id")
        if not isinstance(tid, str) or not tid:
            continue
        v = r.get("vote")
        if v == 1:
            up[tid] = up.get(tid, 0) + 1
        elif v == -1:
            down[tid] = down.get(tid, 0) + 1
    return {tid for tid, u in up.items() if u > down.get(tid, 0)}


def _load_track_ids_from_file(path: Path) -> list[str]:
    """Read newline-delimited track ids from ``path``.

    Empty lines and lines whose first non-whitespace character is ``#`` are
    skipped so users can comment shortlists. Leading/trailing whitespace on
    each id is trimmed (matching the in-memory filter semantics) and any
    embedded whitespace in an id is rejected with ``typer.BadParameter`` since
    track ids in this codebase are opaque tokens and a space almost always
    means the file was malformed (e.g. a CSV row pasted in). Duplicates are
    preserved in input order so the caller's de-dup behaviour matches
    ``--only-track``/``--exclude-track`` (which de-dup via a set downstream).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"could not read track id file {path}: {exc}")
    ids: list[str] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if any(ch.isspace() for ch in stripped):
            raise typer.BadParameter(
                f"track id file {path} line {lineno}: ids must not contain whitespace (got {stripped!r})"
            )
        ids.append(stripped)
    return ids


def _load_artist_names_from_file(path: Path) -> list[str]:
    """Read newline-delimited artist names from ``path``.

    Parallel to :func:`_load_track_ids_from_file` but tuned for artist names,
    which (unlike opaque track ids) routinely contain embedded whitespace
    ("the beatles", "earth wind and fire"). Empty lines and lines whose first
    non-whitespace character is ``#`` are skipped so users can comment
    allow/deny lists. Each kept line is stripped of leading/trailing
    whitespace; the downstream filter casefolds for comparison, so casing in
    the file does not matter. Duplicates and order are preserved so the
    loader behaves like a sequence of repeated ``--only-artist`` /
    ``--exclude-artist`` options.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise typer.BadParameter(f"could not read artist file {path}: {exc}")
    names: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.append(stripped)
    return names


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


def _filter_excluded_artists(results, exclude: list[str] | None):
    """Drop matches whose track.artist matches any name in ``exclude``.

    Comparison is case-insensitive and whitespace-trimmed so users can pass
    ``--exclude-artist " the beatles "`` and still drop tracks tagged
    ``"The Beatles"``. Entries that are empty or whitespace-only are ignored
    rather than treated as a blanket drop (which would silently wipe every
    match). ``None`` or empty list is a no-op so callers don't need to
    special-case it.
    """
    if not exclude:
        return list(results)
    blocked = {a.strip().casefold() for a in exclude if a and a.strip()}
    if not blocked:
        return list(results)
    return [m for m in results if (m.track.artist or "").strip().casefold() not in blocked]


def _filter_only_artists(results, only: list[str] | None):
    """Keep only matches whose ``track.artist`` matches a name in ``only``.

    Symmetric to :func:`_filter_excluded_artists`: comparison is
    case-insensitive and whitespace-trimmed so ``--only-artist " the beatles "``
    keeps tracks tagged ``"The Beatles"``. Empty / whitespace-only entries are
    ignored rather than treated as a blanket pass (which would silently turn
    the flag into a no-op and surprise the caller). ``None`` or an all-blank
    list is itself a no-op so callers don't need to special-case it.
    """
    if not only:
        return list(results)
    allowed = {a.strip().casefold() for a in only if a and a.strip()}
    if not allowed:
        return list(results)
    return [m for m in results if (m.track.artist or "").strip().casefold() in allowed]


def _filter_only_tracks(results, only: list[str] | None):
    """Keep only matches whose track_id is in ``only``.

    Comparison is case-sensitive and whitespace-trimmed so users can pass
    ``--only-track " abc "`` without surprises. ``None`` or an empty list (or
    a list of whitespace-only entries) is a no-op so callers don't need to
    special-case it; this preserves the matcher's full top-K.
    """
    if not only:
        return list(results)
    allowed = {t.strip() for t in only if t and t.strip()}
    if not allowed:
        return list(results)
    return [m for m in results if m.track.id in allowed]


def _dedupe_by_track(results):
    """Keep only the best-scoring match per track_id, preserving input order.

    The matcher returns one row per (track, segment), so a single song with
    several strong segments can fill the whole top-K and crowd out other
    candidates. Callers can opt in to one row per track via ``--unique-tracks``.
    Input is assumed to be score-descending (matcher's contract); the first hit
    for each track_id therefore has the highest score and is the one kept.
    """
    seen: set[str] = set()
    out = []
    for m in results:
        if m.track.id in seen:
            continue
        seen.add(m.track.id)
        out.append(m)
    return out


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


def _resolve_output_format(fmt: str | None, json_out: bool = False, output: Path | None = None) -> str:
    """Resolve the effective output format for table/json/csv-producing commands.

    Precedence: --json shortcut > explicit --format > inferred from --output
    extension > stdout default (table). When an explicit --format=table is
    paired with --output we downgrade to csv since rich tables do not
    round-trip cleanly to disk.
    """
    chosen = fmt.lower() if fmt else None
    if json_out:
        chosen = "json"
    if chosen is not None and chosen not in {"table", "json", "csv"}:
        raise typer.BadParameter("--format must be one of: table, json, csv")
    if chosen is None:
        if output is not None:
            suffix = output.suffix.lower()
            if suffix == ".json":
                return "json"
            if suffix in {".csv", ".tsv", ".txt"}:
                return "csv"
            # Unknown extension going to a file: fall back to csv rather than
            # dumping ANSI table escapes.
            return "csv"
        return "table"
    if output is not None and chosen == "table":
        return "csv"
    return chosen


@app.command()
def match(
    query: Path = typer.Argument(..., exists=True, dir_okay=False, file_okay=True),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    threshold: float = typer.Option(0.0, "--threshold", "-t"),
    no_clap: bool = typer.Option(False, "--no-clap"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON (shortcut for --format json)."),
    fmt: str | None = typer.Option(None, "--format", "-f", help="Output format: table, json, csv. Defaults to table on stdout, or inferred from --output extension (.json/.csv) when writing to a file."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write results to file instead of stdout."),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        "-x",
        help="Drop matches with this track_id. Repeatable. Useful to peek past a known-wrong top hit (e.g. a duplicate edition) without re-humming.",
    ),
    only_track: list[str] = typer.Option(
        None,
        "--only-track",
        "-O",
        help="Restrict matches to this track_id. Repeatable. Useful to ask 'does this hum match one of these specific tracks?' (e.g. comparing a clip against a shortlist of suspected songs) without scanning the whole top-K. Mutually exclusive with --exclude-track.",
    ),
    only_track_file: Path | None = typer.Option(
        None,
        "--only-track-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --only-track ids from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --only-track values. Useful when the shortlist has more ids than fit cleanly on a command line, or when the same shortlist is reused across many match runs in a script.",
    ),
    exclude_track_file: Path | None = typer.Option(
        None,
        "--exclude-track-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-track ids from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-track values and with --exclude-downvoted. Useful for a persistent 'never show me this again' list maintained outside the feedback log.",
    ),
    unique_tracks: bool = typer.Option(
        False,
        "--unique-tracks",
        "-u",
        help="Collapse multiple segment hits from the same track into one row (the best-scoring segment). Useful when one song's segments fill the whole top-K and crowd out other candidates.",
    ),
    no_hint: bool = typer.Option(
        False,
        "--no-hint",
        "-Q",
        help="Suppress the trailing query_id and vote-command hint in table mode. Useful when piping output to a pager or capturing terminal sessions in scripts.",
    ),
    fail_on_empty: bool = typer.Option(
        False,
        "--fail-on-empty",
        "-E",
        help="Exit non-zero (code 2) when no matches survive the threshold and filters. Useful in scripts and CI so a silent miss does not look like a successful match.",
    ),
    min_results: int | None = typer.Option(
        None,
        "--min-results",
        "-N",
        help="Exit non-zero (code 2) when fewer than N matches survive the threshold and filters. Useful in CI to gate on a minimum candidate count (e.g. require at least 3 above-threshold hits before promoting an auto-tag). Must be a positive integer. --fail-on-empty is equivalent to --min-results 1; if both are set the stricter wins.",
    ),
    query_id_opt: str | None = typer.Option(
        None,
        "--query-id",
        "-q",
        help="Tag this match run with a caller-supplied query id instead of a fresh UUID. Useful to (1) re-run the same hum and have follow-up `clawhum feedback` votes line up against a stable id, (2) thread a higher-level request id (e.g. `user_42_attempt_3`) through to the feedback log, or (3) make table/JSON/CSV output deterministic in tests. Must be a non-blank string of at most 128 characters; raw whitespace is rejected so it round-trips cleanly through shells and CSV.",
    ),
    exclude_artist: list[str] = typer.Option(
        None,
        "--exclude-artist",
        "-X",
        help="Drop matches whose artist name matches this value. Repeatable. Comparison is case-insensitive and whitespace-trimmed so '--exclude-artist beatles' drops tracks tagged 'The Beatles' is not collapsed, but 'The Beatles' and ' the beatles ' are treated as the same artist. Useful when the top hits are saturated by one artist (covers, remasters, alternate editions) and the user wants to see what else the hum matches.",
    ),
    only_artist: list[str] = typer.Option(
        None,
        "--only-artist",
        "-A",
        help="Restrict matches to tracks whose artist name matches this value. Repeatable. Comparison is case-insensitive and whitespace-trimmed so '--only-artist \"the beatles\"' keeps tracks tagged 'The Beatles'. Useful as a 'does this hum match anything by <artist>?' filter without scanning the whole top-K. Combines as an intersection with --only-track (a match must satisfy both allowlists). Mutually exclusive with --exclude-artist.",
    ),
    only_artist_file: Path | None = typer.Option(
        None,
        "--only-artist-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --only-artist names from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --only-artist values. Useful when the allowlist outgrows the command line (e.g. a saved list of favourite artists piped into many match runs) and case/whitespace of each name does not need to match the catalog exactly.",
    ),
    exclude_artist_file: Path | None = typer.Option(
        None,
        "--exclude-artist-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-artist names from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-artist values. Useful for a persistent 'never show me anything by these artists' list maintained outside the feedback log (e.g. covers/tribute artists that crowd the top-K).",
    ),
    exclude_downvoted: bool = typer.Option(
        False,
        "--exclude-downvoted",
        "-D",
        help="Drop matches for tracks whose recorded feedback is net negative (down-votes > up-votes). Lets a returning user re-hum a melody without the songs they already rejected crowding the top of the list. A single stray down-vote does not blacklist a track: only tracks where down-votes strictly outnumber up-votes are dropped, so an accidental thumbs-down that was later corrected with two thumbs-up will still appear. Unions with any --exclude-track ids; mutually exclusive with --only-track (where the user has explicitly opted into a shortlist).",
    ),
    only_upvoted: bool = typer.Option(
        False,
        "--only-upvoted",
        "-U",
        help="Restrict matches to tracks whose recorded feedback is net positive (up-votes > down-votes). Useful as a 'find something I already like that matches this hum' filter, e.g. re-discovering a song from a playlist of past favourites. The threshold is strict: a track that has been up- and down-voted exactly once does not qualify, so only tracks the user has clearly stood by survive. Unions with --only-track (the resulting allowlist is the union of the two sets); mutually exclusive with --exclude-downvoted (redundant: a net-positive set never overlaps a net-negative set).",
    ),
):
    """Match an audio file (hum/clip) against the index."""
    from clawhum_audio.io import load_audio
    from clawhum_match.matcher import Matcher
    from services.api.clawhum_api.state import AppState  # type: ignore

    chosen = _resolve_output_format(fmt, json_out, output)

    # Merge file-sourced ids into the option lists before any filter logic so
    # the downstream fetch_k accounting and mutual-exclusion checks see one
    # combined view. File ids are appended after CLI ids; the downstream
    # filters de-dup via sets so order only matters for accounting.
    if only_track_file is not None:
        only_track = list(only_track or []) + _load_track_ids_from_file(only_track_file)
    if exclude_track_file is not None:
        exclude_track = list(exclude_track or []) + _load_track_ids_from_file(exclude_track_file)
    if only_artist_file is not None:
        only_artist = list(only_artist or []) + _load_artist_names_from_file(only_artist_file)
    if exclude_artist_file is not None:
        exclude_artist = list(exclude_artist or []) + _load_artist_names_from_file(exclude_artist_file)

    only_ids = {t.strip() for t in (only_track or []) if t and t.strip()}
    excl_ids = {t.strip() for t in (exclude_track or []) if t and t.strip()}
    excl_artists = {a.strip().casefold() for a in (exclude_artist or []) if a and a.strip()}
    only_artists = {a.strip().casefold() for a in (only_artist or []) if a and a.strip()}
    if only_artists and excl_artists:
        raise typer.BadParameter("--only-artist and --exclude-artist are mutually exclusive")
    if only_ids and excl_ids:
        raise typer.BadParameter("--only-track and --exclude-track are mutually exclusive")
    if exclude_downvoted and only_ids and not only_upvoted:
        raise typer.BadParameter("--exclude-downvoted and --only-track are mutually exclusive")
    if exclude_downvoted and only_upvoted:
        raise typer.BadParameter("--exclude-downvoted and --only-upvoted are mutually exclusive (a net-positive set never overlaps a net-negative set)")
    if only_upvoted and excl_ids:
        # An upvoted allowlist combined with an explicit exclude list is
        # legitimate: "things I've liked, except this one edition". Allow it.
        pass
    if exclude_downvoted:
        from clawhum_library.feedback import read_feedback
        s_fb = get_settings()
        dv_ids = _downvoted_track_ids(read_feedback(s_fb.feedback_path))
        if dv_ids:
            # Union with the user-supplied exclude list so the downstream
            # filter and fetch_k accounting see one combined set; the user's
            # explicit ids still get whitespace-trimmed there.
            excl_ids = excl_ids | dv_ids
            exclude_track = list((exclude_track or [])) + sorted(dv_ids - {t.strip() for t in (exclude_track or []) if t and t.strip()})
    if only_upvoted:
        from clawhum_library.feedback import read_feedback
        s_fb = get_settings()
        uv_ids = _upvoted_track_ids(read_feedback(s_fb.feedback_path))
        # Union with any user-supplied --only-track ids so the downstream
        # filter and fetch_k accounting see one combined allowlist.
        # If the user passed no --only-track and there are no up-voted tracks
        # yet, fall through to an empty allowlist so the filter produces an
        # empty result rather than silently returning the full top-K (which
        # would defeat the point of --only-upvoted).
        new_only = sorted(uv_ids - only_ids)
        only_ids = only_ids | uv_ids
        only_track = list((only_track or [])) + new_only
        if not only_ids:
            # Sentinel: a value that will never match any real track id, so the
            # filter returns empty instead of being a no-op.
            only_ids = {"__clawhum_no_upvoted_tracks__"}
            only_track = ["__clawhum_no_upvoted_tracks__"]

    if min_results is not None and min_results < 1:
        raise typer.BadParameter("--min-results must be a positive integer")

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
    n_excl = len(excl_ids)
    n_excl_artist = len(excl_artists)
    n_only_artist = len(only_artists)
    # When deduping by track or excluding ids, pull extra candidates so the
    # post-filter list still has ~top_k rows. For --unique-tracks we don't know
    # the duplication factor up front, so fetch a generous multiple capped at a
    # sane ceiling; the matcher caps to the index size internally. For
    # --only-track the hits we want may sit far down the ranking, so we ask
    # the matcher for the whole index and let the filter slice the top_k off
    # the end; the matcher caps fetch_k to the index size internally.
    fetch_k = top_k + n_excl if n_excl else top_k
    if n_excl_artist:
        # Artist filter can drop an unbounded share of the top-K (a whole
        # discography), so widen the fetch generously and let the matcher cap
        # to the index size.
        fetch_k = max(fetch_k, top_k * 8)
    if n_only_artist:
        # Restricting to one artist may push the matching hits far down the
        # ranking, so ask the matcher for the whole index and let the filter
        # slice the top_k off the survivors; the matcher caps to index size.
        fetch_k = max(fetch_k, state.index.size())
    if unique_tracks:
        fetch_k = max(fetch_k, top_k * 8)
    if only_ids:
        fetch_k = max(fetch_k, state.index.size())
    results = matcher.match(x, sr, top_k=fetch_k, threshold=threshold)
    if only_ids:
        results = _filter_only_tracks(results, only_track)
    if n_excl:
        results = _filter_excluded_tracks(results, exclude_track)
    if n_excl_artist:
        results = _filter_excluded_artists(results, exclude_artist)
    if n_only_artist:
        results = _filter_only_artists(results, only_artist)
    if unique_tracks:
        results = _dedupe_by_track(results)
    if n_excl or n_excl_artist or unique_tracks or only_ids or n_only_artist:
        results = results[:top_k]
    if query_id_opt is not None:
        # Caller-supplied id: validate aggressively so a malformed value can
        # never poison the feedback log or table/CSV output. Reject blanks
        # (would look like the auto-uuid was skipped), reject any whitespace
        # (breaks shell pipelines and CSV reliably), and cap the length so a
        # runaway value can't bloat every row.
        if not query_id_opt or not query_id_opt.strip():
            raise typer.BadParameter("--query-id must not be empty")
        if any(ch.isspace() for ch in query_id_opt):
            raise typer.BadParameter("--query-id must not contain whitespace")
        if len(query_id_opt) > 128:
            raise typer.BadParameter("--query-id must be at most 128 characters")
        query_id = query_id_opt
    else:
        query_id = str(uuid.uuid4())

    def _under_min_results() -> bool:
        # --min-results N gates the exit code: exit 2 if fewer than N matches
        # survive. Treat --fail-on-empty as min_results=1 so the two flags
        # compose cleanly (the stricter floor wins).
        floor = min_results if min_results is not None else (1 if fail_on_empty else 0)
        return floor > 0 and len(results) < floor

    if chosen == "json":
        payload = json.dumps(_results_as_dicts(results, query_id=query_id))
        if output is not None:
            output.write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]wrote {len(results)} match(es) to {output} (query_id={query_id})[/green]")
        else:
            console.print_json(payload)
        if _under_min_results():
            raise typer.Exit(code=2)
        return
    if chosen == "csv":
        payload = _results_as_csv(results, query_id=query_id)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            console.print(f"[green]wrote {len(results)} match(es) to {output} (query_id={query_id})[/green]")
        else:
            sys.stdout.write(payload)
            sys.stdout.flush()
        if _under_min_results():
            raise typer.Exit(code=2)
        return

    if not results:
        # Empty table mode: print an actionable message instead of an
        # empty table followed by a vote hint pointing at no track id.
        hints = []
        if threshold > 0.0:
            hints.append(f"lowering --threshold (currently {threshold})")
        if n_excl:
            hints.append("removing --exclude-track filters")
        if n_excl_artist:
            hints.append("removing --exclude-artist filters")
        if n_only_artist:
            hints.append("widening or dropping --only-artist filters")
        if only_ids:
            hints.append("widening or dropping --only-track filters")
        suffix = f" (try {', '.join(hints)})" if hints else ""
        console.print(f"[yellow]no matches{suffix}[/yellow]")
        if not no_hint:
            console.print(f"[dim]query_id: {query_id}[/dim]")
        if _under_min_results():
            raise typer.Exit(code=2)
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
    if not no_hint:
        console.print(
            f"[dim]query_id: {query_id}\n"
            f"vote a match: clawhum feedback {query_id} <track_id> <score> <1|-1>[/dim]"
        )
    if _under_min_results():
        # Result table was shown so the user can see what did survive, but exit
        # non-zero so scripts gating on --min-results still fail.
        console.print(
            f"[yellow]only {len(results)} match(es) survived (need at least "
            f"{min_results if min_results is not None else 1})[/yellow]"
        )
        raise typer.Exit(code=2)


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
def feedback(
    query_id: str,
    track_id: str,
    score: float,
    vote: int,
    require_known_track: bool = typer.Option(
        False,
        "--require-known-track",
        "-K",
        help="Refuse to record the vote (exit 2) if track_id is not present in the indexed library. Guards against fat-fingered track ids that would otherwise pile up as orphan feedback only visible via `feedback-list --orphaned`.",
    ),
):
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
    meta: dict[str, tuple[str, str]] | None = None
    if require_known_track:
        meta = _load_track_metadata()
        if track_id not in meta:
            console.print(
                f"[red]unknown track_id {track_id!r}; not in indexed library (use `clawhum stats` to confirm the index is loaded, or drop --require-known-track to force-record)[/red]"
            )
            raise typer.Exit(code=2)
    from clawhum_library.feedback import record_feedback
    record_feedback(s.feedback_path, query_id, track_id, score, vote)
    if meta is not None and track_id in meta:
        title, artist = meta[track_id]
        label = " - ".join(p for p in (title, artist) if p) or track_id
        console.print(f"[green]ok[/green] [dim]({label})[/dim]")
    else:
        console.print("[green]ok[/green]")


@app.command("feedback-delete")
def feedback_delete(
    query_id: list[str] = typer.Option(
        None,
        "--query-id",
        help="Delete entries with this query_id. Repeatable: pass multiple --query-id values to purge several sessions in one call (e.g. `--query-id q-a --query-id q-b`) instead of looping the command per id. Whitespace-trimmed; blank entries ignored; unknown ids are a no-op. Must not overlap with --exclude-query-id.",
    ),
    query_id_file: Path | None = typer.Option(
        None,
        "--query-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --query-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --query-id values. Useful for scripted purges where the session id set is too long to pass on the command line or is reused across many runs (e.g. a saved list of evaluation sessions to retire after a model swap).",
    ),
    track_id: list[str] = typer.Option(
        None,
        "--track-id",
        help="Delete entries with this track_id. Repeatable: pass multiple --track-id values to purge several tracks' votes in one call instead of looping. Whitespace-trimmed; blank entries ignored; unknown ids are a no-op. Must not overlap with --exclude-track.",
    ),
    track_id_file: Path | None = typer.Option(
        None,
        "--track-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --track-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --track-id values. Useful for scripted purges where the track id set is too long to pass on the command line or is reused across many runs (e.g. a saved list of duplicate-edition ids to wipe votes for).",
    ),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        help="Never delete entries for this track_id. Repeatable. Use as a safety net when bulk-purging by --vote, --since/--until, or score range so a track you still care about is preserved (e.g. --vote -1 --until 30d --exclude-track t-keepme ages out old down-votes while leaving that track untouched). Whitespace-trimmed; blank entries ignored; unknown ids are a no-op. Must not overlap with --track-id.",
    ),
    exclude_track_file: Path | None = typer.Option(
        None,
        "--exclude-track-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-track values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-track values. Useful for a persistent 'never purge these tracks' denylist maintained outside the feedback log so bulk purges (e.g. `--vote -1 --until 30d`) cannot accidentally wipe votes you still care about.",
    ),
    exclude_query_id: list[str] = typer.Option(
        None,
        "--exclude-query-id",
        help="Never delete entries with this query_id. Repeatable. Useful for scoping a bulk purge to skip a curated set of sessions (e.g. votes recorded during a known-good evaluation run). Whitespace-trimmed; blank entries ignored; unknown ids are a no-op. Must not overlap with --query-id.",
    ),
    exclude_query_id_file: Path | None = typer.Option(
        None,
        "--exclude-query-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-query-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-query-id values. Useful for a persistent 'never purge these sessions' denylist (e.g. a list of evaluation-run query ids to keep intact across bulk purges).",
    ),
    vote: int | None = typer.Option(None, "--vote", help="Restrict deletion to this vote (1 or -1)."),
    since: str | None = typer.Option(None, "--since", help="Only delete entries at/after this time (unix seconds, ISO-8601 naive = UTC, or relative offset from now like 24h/7d/30m/45s/2w)."),
    until: str | None = typer.Option(None, "--until", help="Only delete entries strictly before this time (unix seconds, ISO-8601 naive = UTC, or relative offset from now like 24h/7d/30m/45s/2w). Pair with no other filter to purge old feedback (e.g. --until 30d to age out anything older than 30 days, or --until 2024-01-01 to age out last year)."),
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
    log. --query-id and --track-id are repeatable so a single call can
    target multiple sessions or tracks (e.g. `--query-id q-a --query-id q-b`)
    instead of looping the command. --exclude-track and --exclude-query-id
    (both repeatable) act as a
    denylist on top of those positive filters so a bulk purge (e.g.
    `--vote -1 --until 30d`) can skip tracks or sessions you still want to
    keep; they cannot stand alone. Rows without a numeric ts are never
    matched by --since / --until and rows without a numeric score are never
    matched by --min-score / --max-score so undated or unscored entries are
    not silently purged.
    --title / --artist are resolved against the indexed library, so feedback
    whose track has been removed from the index is never deleted by a
    name-based purge. --orphaned deletes only votes pointing at track_ids
    no longer in the index (typical use: clean up after a library prune);
    --in-index is its inverse and is mutually exclusive with --orphaned.
    """
    # Merge file-sourced ids into the option lists before the no-positive-filter
    # guard and the overlap check so a positive filter supplied only via
    # --track-id-file / --query-id-file is accepted, and overlap checks see one
    # combined view. File ids union with any CLI ids; the downstream filter
    # de-dups via sets so order only matters for accounting.
    if query_id_file is not None:
        query_id = list(query_id or []) + _load_track_ids_from_file(query_id_file)
    if track_id_file is not None:
        track_id = list(track_id or []) + _load_track_ids_from_file(track_id_file)
    if exclude_track_file is not None:
        exclude_track = list(exclude_track or []) + _load_track_ids_from_file(exclude_track_file)
    if exclude_query_id_file is not None:
        exclude_query_id = list(exclude_query_id or []) + _load_track_ids_from_file(exclude_query_id_file)

    if (
        not query_id and not track_id and vote is None
        and since is None and until is None
        and min_score is None and max_score is None
        and title is None and artist is None
        and not orphaned and not in_index
    ):
        raise typer.BadParameter("supply at least one of --query-id, --track-id, --vote, --since, --until, --min-score, --max-score, --title, --artist, --orphaned, --in-index")
    excluded_track_set = {t.strip() for t in (exclude_track or []) if t and t.strip()}
    excluded_query_set = {q.strip() for q in (exclude_query_id or []) if q and q.strip()}
    track_id_set = {t.strip() for t in (track_id or []) if t and t.strip()}
    query_id_set = {q.strip() for q in (query_id or []) if q and q.strip()}
    if track_id_set & excluded_track_set:
        raise typer.BadParameter("--track-id and --exclude-track must not overlap")
    if query_id_set & excluded_query_set:
        raise typer.BadParameter("--query-id and --exclude-query-id must not overlap")
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
    # If the user supplied both --track-id and a resolver-based allowlist
    # (--title/--artist/--in-index/--orphaned), the effective allowlist is the
    # intersection: only tracks named explicitly AND matching the resolver.
    if track_id_set and resolved_track_ids is not None:
        effective_track_ids: set[str] | None = track_id_set & resolved_track_ids
        if not effective_track_ids:
            console.print("[dim]no matching feedback entries[/dim]")
            return
    elif track_id_set:
        effective_track_ids = track_id_set
    else:
        effective_track_ids = resolved_track_ids

    matches = _filter_feedback(
        rows,
        query_ids=query_id_set or None,
        track_ids=effective_track_ids,
        exclude_track_ids=excluded_track_set or None,
        exclude_query_ids=excluded_query_set or None,
        vote=vote,
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
        s.feedback_path,
        query_ids=query_id_set or None,
        track_ids=effective_track_ids,
        exclude_track_ids=excluded_track_set or None,
        exclude_query_ids=excluded_query_set or None,
        vote=vote,
        since=since_ts, until=until_ts,
        min_score=min_score, max_score=max_score,
    )
    console.print(f"[green]deleted {removed} entry(s)[/green]")


_RELATIVE_TIME_UNITS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


def _parse_time_bound(value: str, *, flag: str, now: float | None = None) -> float:
    """Parse a CLI time bound as a unix epoch seconds value, an ISO-8601
    date/datetime, or a relative offset from now like ``24h``, ``7d``, ``30m``,
    ``45s`` or ``2w``. Relative offsets resolve to ``now - offset`` so users can
    say ``--since 24h`` instead of computing a timestamp. Naive ISO inputs are
    treated as UTC. Raises typer.BadParameter on bad input so the user gets a
    clean error instead of a traceback.
    """
    import time
    from datetime import datetime, timezone

    v = value.strip()
    if not v:
        raise typer.BadParameter(f"{flag} must not be empty")
    # relative offset shorthand: <number><unit> where unit is one of s/m/h/d/w.
    # Resolves to (now - offset). A bare "0d"/"0h" means "now" which is a useful
    # upper bound ("everything up to this instant"). Negative offsets are not
    # accepted because the only well-defined meaning is "future", which feedback
    # filters never want.
    if len(v) >= 2 and v[-1] in _RELATIVE_TIME_UNITS:
        head = v[:-1]
        try:
            n = float(head)
        except ValueError:
            n = None
        if n is not None and n >= 0:
            base = time.time() if now is None else now
            return base - n * _RELATIVE_TIME_UNITS[v[-1]]
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
    query_ids: set[str] | None = None,
    track_id: str | None = None,
    track_ids: set[str] | None = None,
    exclude_track_ids: set[str] | None = None,
    exclude_query_ids: set[str] | None = None,
    vote: int | None = None,
    since: float | None = None,
    until: float | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
) -> list[dict]:
    out = rows
    if exclude_query_ids:
        out = [r for r in out if str(r.get("query_id", "")) not in exclude_query_ids]
    if exclude_track_ids:
        out = [r for r in out if str(r.get("track_id", "")) not in exclude_track_ids]
    if query_id is not None:
        out = [r for r in out if r.get("query_id") == query_id]
    if query_ids is not None:
        out = [r for r in out if str(r.get("query_id", "")) in query_ids]
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
    query_id: list[str] = typer.Option(
        None,
        "--query-id",
        "-q",
        help="Only show entries for this query_id. Repeatable. Useful for scoping the listing to a small set of related hum attempts (e.g. a single user session, or the qids returned by a few back-to-back `clawhum match` runs) without grepping the table. Mutually exclusive with --exclude-query-id on the same id.",
    ),
    query_id_file: Path | None = typer.Option(
        None,
        "--query-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --query-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --query-id values. Useful when the session id shortlist (e.g. a saved set of qids from a recent evaluation run) has more ids than fit cleanly on a command line, or when the same shortlist is reused across many feedback-list runs in a script.",
    ),
    exclude_query_id: list[str] = typer.Option(
        None,
        "--exclude-query-id",
        "-X",
        help="Drop entries with this query_id from the listing. Repeatable. Useful for hiding a known-noisy session (e.g. a smoke-test run that flooded the log with junk votes) without rewriting the feedback file.",
    ),
    exclude_query_id_file: Path | None = typer.Option(
        None,
        "--exclude-query-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-query-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-query-id values. Useful for a persistent 'never show these sessions in the listing' denylist (e.g. smoke-test or evaluation run qids that crowd day-to-day feedback review).",
    ),
    track_id: list[str] = typer.Option(
        None,
        "--track-id",
        help="Only show entries for this track_id. Repeatable. Useful for scoping the listing to a curated shortlist of tracks (e.g. the top candidates from a recent `clawhum match`) without grepping the table. Mutually exclusive with --exclude-track on the same id.",
    ),
    track_id_file: Path | None = typer.Option(
        None,
        "--track-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --track-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --track-id values. Useful when the shortlist (e.g. a saved set of candidate ids from an earlier `clawhum match`) has more ids than fit cleanly on a command line, or when the same shortlist is reused across many feedback-list runs in a script.",
    ),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        "-x",
        help="Drop entries for this track_id from the listing. Repeatable. Useful for looking past a known-noisy track (e.g. a duplicate edition that dominates recent votes) without re-querying.",
    ),
    exclude_track_file: Path | None = typer.Option(
        None,
        "--exclude-track-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-track values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-track values. Useful for a persistent 'never include these tracks in the listing' set maintained outside the feedback log (e.g. duplicate editions or smoke-test seeds).",
    ),
    vote: int | None = typer.Option(None, "--vote", help="Filter by vote: 1 (up) or -1 (down)."),
    since: str | None = typer.Option(None, "--since", help="Only entries at/after this time (unix seconds, ISO-8601 naive = UTC, or relative offset from now like 24h/7d/30m/45s/2w)."),
    until: str | None = typer.Option(None, "--until", help="Only entries strictly before this time (unix seconds, ISO-8601 naive = UTC, or relative offset from now like 24h/7d/30m/45s/2w)."),
    min_score: float | None = typer.Option(None, "--min-score", help="Only entries whose recorded score is at least this. Entries without a numeric score are excluded."),
    max_score: float | None = typer.Option(None, "--max-score", help="Only entries whose recorded score is at most this. Entries without a numeric score are excluded. Combine with --vote -1 to find down-votes on high-confidence matches (false positives)."),
    title: str | None = typer.Option(None, "--title", help="Only entries whose track title contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    artist: str | None = typer.Option(None, "--artist", help="Only entries whose track artist contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    exclude_artist: list[str] = typer.Option(
        None,
        "--exclude-artist",
        help="Drop entries whose track artist matches this value (case-insensitive, whitespace-trimmed exact match). Repeatable. Implies --enrich. Useful to hide one noisy artist that dominates the listing (covers, alternate editions, smoke-test seeds) without rewriting the feedback file. Orphan tracks (no metadata) are kept so they remain visible for cleanup; pair with --in-index to drop them too.",
    ),
    exclude_artist_file: Path | None = typer.Option(
        None,
        "--exclude-artist-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-artist names from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-artist values. Useful for a persistent 'never show me anything by these artists' list maintained outside the feedback log (e.g. covers/tribute artists that crowd the listing run after run).",
    ),
    only_artist: list[str] = typer.Option(
        None,
        "--only-artist",
        help="Restrict the listing to entries whose track artist matches this value (case-insensitive, whitespace-trimmed exact match). Repeatable. Implies --enrich. Useful as a 'show me only votes on <artist>' filter without grepping the table. Orphan tracks (no metadata) are dropped because we cannot prove they belong to the allowlisted artist; pair with --orphaned separately to inspect them. Mutually exclusive with --exclude-artist.",
    ),
    only_artist_file: Path | None = typer.Option(
        None,
        "--only-artist-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --only-artist names from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --only-artist values. Useful when the allowlist outgrows the command line (e.g. a saved set of favourite artists piped into a scheduled feedback-list run) and the same list is reused across many invocations.",
    ),
    fmt: str | None = typer.Option(None, "--format", "-f", help="Output format: table, json, csv. Defaults to table on stdout, or inferred from --output extension (.json/.csv) when writing to a file."),
    enrich: bool = typer.Option(False, "--enrich", help="Join with the indexed library to add title/artist columns. Unknown tracks show blank values."),
    orphaned: bool = typer.Option(False, "--orphaned", help="Only show entries whose track_id is no longer in the indexed library. Useful after pruning the library to find stale votes to delete with feedback-delete."),
    in_index: bool = typer.Option(False, "--in-index", help="Only show entries whose track_id is still present in the indexed library. Skips orphaned feedback so the list reflects the live catalog."),
    sort: str = typer.Option("ts", "--sort", help="Sort order: ts (newest first, default), ts-asc (oldest first), score (highest first), score-asc (lowest first), track_id (asc). Entries missing a numeric ts/score sort last."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
    fail_on_empty: bool = typer.Option(
        False,
        "--fail-on-empty",
        "-E",
        help="Exit non-zero (code 2) when no entries survive the filters. Useful in scripts and CI (e.g. nightly checks that votes are being recorded) so an empty list does not look like a successful no-op.",
    ),
):
    """List recorded feedback (most recent first)."""
    if orphaned and in_index:
        raise typer.BadParameter("--orphaned and --in-index are mutually exclusive")
    sort_key = sort.lower()
    if sort_key not in {"ts", "ts-asc", "score", "score-asc", "track_id"}:
        raise typer.BadParameter("--sort must be one of: ts, ts-asc, score, score-asc, track_id")
    chosen = _resolve_output_format(fmt, output=output)
    if vote is not None and vote not in (1, -1):
        raise typer.BadParameter("--vote must be 1 or -1")
    if limit < 0:
        raise typer.BadParameter("--limit must be >= 0")
    if min_score is not None and max_score is not None and min_score > max_score:
        raise typer.BadParameter("--min-score must be <= --max-score")
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    # Merge file-sourced ids into the option lists before any filter logic so
    # the downstream overlap check and filter see one combined view. File ids
    # are appended after CLI ids; the downstream filters de-dup via sets so
    # order only matters for accounting.
    if track_id_file is not None:
        track_id = list(track_id or []) + _load_track_ids_from_file(track_id_file)
    if exclude_track_file is not None:
        exclude_track = list(exclude_track or []) + _load_track_ids_from_file(exclude_track_file)
    if query_id_file is not None:
        query_id = list(query_id or []) + _load_track_ids_from_file(query_id_file)
    if exclude_query_id_file is not None:
        exclude_query_id = list(exclude_query_id or []) + _load_track_ids_from_file(exclude_query_id_file)
    if exclude_artist_file is not None:
        exclude_artist = list(exclude_artist or []) + _load_artist_names_from_file(exclude_artist_file)
    if only_artist_file is not None:
        only_artist = list(only_artist or []) + _load_artist_names_from_file(only_artist_file)

    only_ids = {t.strip() for t in (track_id or []) if t and t.strip()}
    excluded_ids = {t.strip() for t in (exclude_track or []) if t and t.strip()}
    if only_ids and excluded_ids and only_ids & excluded_ids:
        raise typer.BadParameter(
            "--track-id and --exclude-track must not overlap"
        )
    only_qids = {q.strip() for q in (query_id or []) if q and q.strip()}
    excluded_qids = {q.strip() for q in (exclude_query_id or []) if q and q.strip()}
    if only_qids and excluded_qids and only_qids & excluded_qids:
        raise typer.BadParameter(
            "--query-id and --exclude-query-id must not overlap"
        )

    s = get_settings()
    from clawhum_library.feedback import read_feedback
    rows = read_feedback(s.feedback_path)
    rows = _filter_feedback(
        rows, query_ids=only_qids or None, track_ids=only_ids or None, vote=vote,
        since=since_ts, until=until_ts,
        min_score=min_score, max_score=max_score,
    )
    if excluded_ids:
        rows = [r for r in rows if str(r.get("track_id", "")) not in excluded_ids]
    if excluded_qids:
        rows = [r for r in rows if str(r.get("query_id", "")) not in excluded_qids]
    # --title/--artist/--exclude-artist/--orphaned/--in-index need metadata to
    # filter on, so auto-enrich. --title/--artist additionally drop rows whose
    # track isn't in the indexed library (can't match a blank).
    excluded_artists = {a.strip().casefold() for a in (exclude_artist or []) if a and a.strip()}
    only_artists = {a.strip().casefold() for a in (only_artist or []) if a and a.strip()}
    if only_artists and excluded_artists:
        raise typer.BadParameter(
            "--only-artist and --exclude-artist are mutually exclusive"
        )
    if artist is not None and excluded_artists and artist.strip().casefold() in excluded_artists:
        raise typer.BadParameter(
            "--artist and --exclude-artist must not target the same artist"
        )
    needs_meta = enrich or title is not None or artist is not None or excluded_artists or only_artists or orphaned or in_index
    meta = _load_track_metadata() if needs_meta else None
    if orphaned:
        known = set((meta or {}).keys())
        rows = [r for r in rows if str(r.get("track_id", "")) not in known]
    elif in_index:
        known = set((meta or {}).keys())
        rows = [r for r in rows if str(r.get("track_id", "")) in known]
    if only_artists:
        # Drop rows whose artist (case-insensitive, whitespace-trimmed) is not
        # in the allowlist. Orphan tracks (no metadata row) are dropped because
        # we cannot prove they belong to the allowlisted artist.
        kept = []
        for r in rows:
            entry = (meta or {}).get(str(r.get("track_id", "")))
            if entry is None:
                continue
            _, a_val = entry
            if (a_val or "").strip().casefold() in only_artists:
                kept.append(r)
        rows = kept
    if excluded_artists:
        # Drop rows whose artist (case-insensitive, whitespace-trimmed) matches
        # any excluded value. Orphan tracks (no metadata row) are kept so they
        # remain visible for cleanup; pair with --in-index to drop them too.
        kept = []
        for r in rows:
            entry = (meta or {}).get(str(r.get("track_id", "")))
            if entry is None:
                kept.append(r)
                continue
            _, a_val = entry
            if (a_val or "").strip().casefold() in excluded_artists:
                continue
            kept.append(r)
        rows = kept
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
        if fail_on_empty and not rows:
            raise typer.Exit(code=2)
        return
    if chosen == "csv":
        payload = _feedback_as_csv(rows, enrich=enrich)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            console.print(f"[green]wrote {len(rows)} entry(s) to {output}[/green]")
        else:
            sys.stdout.write(payload)
            sys.stdout.flush()
        if fail_on_empty and not rows:
            raise typer.Exit(code=2)
        return

    if not rows:
        console.print("[dim]no feedback recorded yet[/dim]")
        if fail_on_empty:
            raise typer.Exit(code=2)
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
    track_id: list[str] = typer.Option(
        None,
        "--track-id",
        help="Only aggregate entries for this track_id. Repeatable. Useful for scoping stats to a curated shortlist of tracks (e.g. the top candidates from a recent `clawhum match`) without sifting through the full list. Mutually exclusive with --exclude-track on the same id.",
    ),
    track_id_file: Path | None = typer.Option(
        None,
        "--track-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --track-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --track-id values. Useful when the shortlist (e.g. a saved set of candidate ids from an earlier `clawhum match`) has more ids than fit cleanly on a command line, or when the same shortlist is reused across many feedback-stats runs in a script.",
    ),
    exclude_track: list[str] = typer.Option(
        None,
        "--exclude-track",
        "-x",
        help="Drop this track_id from the aggregation. Repeatable. Useful for looking past known-good or known-bad tracks (e.g. a duplicate edition that dominates the top of the list) without re-querying.",
    ),
    exclude_track_file: Path | None = typer.Option(
        None,
        "--exclude-track-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-track values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-track values. Useful for a persistent 'never include these tracks in the aggregation' set maintained outside the feedback log (e.g. duplicate editions or smoke-test seeds).",
    ),
    query_id: list[str] = typer.Option(
        None,
        "--query-id",
        "-q",
        help="Only aggregate entries for this query_id. Repeatable. Useful for slicing stats down to a single hum attempt or a small set of related attempts (e.g. one user session) without exporting the raw feedback log.",
    ),
    query_id_file: Path | None = typer.Option(
        None,
        "--query-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --query-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --query-id values. Useful when the session id shortlist (e.g. a saved set of evaluation-run query ids) is too long to pass on the command line or is reused across many feedback-stats invocations in a script.",
    ),
    exclude_query_id: list[str] = typer.Option(
        None,
        "--exclude-query-id",
        "-X",
        help="Drop entries with this query_id from the aggregation. Repeatable. Useful for excluding a known-noisy session (e.g. a smoke-test run that flooded the log with junk votes) without rewriting the feedback file.",
    ),
    exclude_query_id_file: Path | None = typer.Option(
        None,
        "--exclude-query-id-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-query-id values from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-query-id values. Useful for a persistent 'never include these sessions in aggregated stats' denylist (e.g. smoke-test session ids that flood the feedback log).",
    ),
    title: str | None = typer.Option(None, "--title", help="Only show tracks whose title contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    artist: str | None = typer.Option(None, "--artist", help="Only show tracks whose artist contains this substring (case-insensitive). Implies --enrich. Tracks missing from the indexed library are excluded."),
    exclude_artist: list[str] = typer.Option(
        None,
        "--exclude-artist",
        help="Drop tracks whose artist matches this value (case-insensitive, whitespace-trimmed exact match). Repeatable. Implies --enrich. Useful to hide one noisy artist that dominates the stats (covers, alternate editions, smoke-test seeds) without rewriting the feedback file. Orphan tracks (no metadata) are kept so they remain visible for cleanup; pair with --in-index to drop them too.",
    ),
    exclude_artist_file: Path | None = typer.Option(
        None,
        "--exclude-artist-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --exclude-artist names from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --exclude-artist values. Useful for a persistent 'never show me anything by these artists' list maintained outside the feedback log (e.g. covers/tribute artists that crowd the aggregated stats run after run).",
    ),
    only_artist: list[str] = typer.Option(
        None,
        "--only-artist",
        help="Restrict aggregation to tracks whose artist matches this value (case-insensitive, whitespace-trimmed exact match). Repeatable. Implies --enrich. Useful as a 'how does the audience feel about <artist>?' filter without sifting through the full list. Orphan tracks (no metadata) are dropped because we cannot prove they belong to the allowlisted artist. Mutually exclusive with --exclude-artist.",
    ),
    only_artist_file: Path | None = typer.Option(
        None,
        "--only-artist-file",
        exists=True,
        dir_okay=False,
        file_okay=True,
        help="Load --only-artist names from a newline-delimited file (blank lines and lines starting with '#' are ignored). Unions with any --only-artist values. Useful when the allowlist outgrows the command line (e.g. a saved set of favourite artists piped into a scheduled feedback-stats run) and the same list is reused across many invocations.",
    ),
    since: str | None = typer.Option(None, "--since", help="Only aggregate entries at/after this time (unix seconds, ISO-8601 naive = UTC, or relative offset from now like 24h/7d/30m/45s/2w)."),
    until: str | None = typer.Option(None, "--until", help="Only aggregate entries strictly before this time (unix seconds, ISO-8601 naive = UTC, or relative offset from now like 24h/7d/30m/45s/2w)."),
    enrich: bool = typer.Option(False, "--enrich", help="Join with the indexed library to add title/artist columns. Unknown tracks show blank values."),
    orphaned: bool = typer.Option(False, "--orphaned", help="Only show tracks with feedback that are no longer in the indexed library. Useful after pruning the library to find stale feedback to delete."),
    in_index: bool = typer.Option(False, "--in-index", help="Only show tracks that are still present in the indexed library. Skips orphaned feedback so stats reflect the live catalog."),
    fmt: str | None = typer.Option(None, "--format", "-f", help="Output format: table, json, csv. Defaults to table on stdout, or inferred from --output extension (.json/.csv) when writing to a file."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
    fail_on_empty: bool = typer.Option(
        False,
        "--fail-on-empty",
        "-E",
        help="Exit non-zero (code 2) when no rows survive the filters. Useful in monitoring scripts and CI so a silent empty result (e.g. no tracks above --min-wilson, or no tracks with --max-net rejection) does not look like a successful no-op.",
    ),
):
    """Aggregate recorded feedback per track (up / down / net / avg score)."""
    if orphaned and in_index:
        raise typer.BadParameter("--orphaned and --in-index are mutually exclusive")
    chosen = _resolve_output_format(fmt, output=output)
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
    since_ts = _parse_time_bound(since, flag="--since") if since is not None else None
    until_ts = _parse_time_bound(until, flag="--until") if until is not None else None
    if since_ts is not None and until_ts is not None and since_ts > until_ts:
        raise typer.BadParameter("--since must be <= --until")

    if track_id_file is not None:
        track_id = list(track_id or []) + _load_track_ids_from_file(track_id_file)
    if exclude_track_file is not None:
        exclude_track = list(exclude_track or []) + _load_track_ids_from_file(exclude_track_file)
    if query_id_file is not None:
        query_id = list(query_id or []) + _load_track_ids_from_file(query_id_file)
    if exclude_query_id_file is not None:
        exclude_query_id = list(exclude_query_id or []) + _load_track_ids_from_file(exclude_query_id_file)
    if exclude_artist_file is not None:
        exclude_artist = list(exclude_artist or []) + _load_artist_names_from_file(exclude_artist_file)
    if only_artist_file is not None:
        only_artist = list(only_artist or []) + _load_artist_names_from_file(only_artist_file)

    only_ids = {t.strip() for t in (track_id or []) if t and t.strip()}
    excluded_ids = {t.strip() for t in (exclude_track or []) if t and t.strip()}
    if only_ids and excluded_ids and only_ids & excluded_ids:
        raise typer.BadParameter(
            "--track-id and --exclude-track must not overlap"
        )
    only_qids = {q.strip() for q in (query_id or []) if q and q.strip()}
    excluded_qids = {q.strip() for q in (exclude_query_id or []) if q and q.strip()}
    if only_qids and excluded_qids and only_qids & excluded_qids:
        raise typer.BadParameter(
            "--query-id and --exclude-query-id must not overlap"
        )

    s = get_settings()
    from clawhum_library.feedback import read_feedback
    rows = read_feedback(s.feedback_path)
    if only_ids:
        rows = [r for r in rows if str(r.get("track_id", "")) in only_ids]
    if excluded_ids:
        rows = [r for r in rows if str(r.get("track_id", "")) not in excluded_ids]
    if only_qids:
        rows = [r for r in rows if str(r.get("query_id", "")) in only_qids]
    if excluded_qids:
        rows = [r for r in rows if str(r.get("query_id", "")) not in excluded_qids]
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
    # --title/--artist/--exclude-artist need metadata to filter on, so
    # auto-enrich. --title/--artist additionally drop orphans (can't prove a
    # track without metadata matches a name needle); --exclude-artist keeps
    # orphans so they stay visible for cleanup (pair with --in-index to drop).
    excluded_artists = {a.strip().casefold() for a in (exclude_artist or []) if a and a.strip()}
    only_artists = {a.strip().casefold() for a in (only_artist or []) if a and a.strip()}
    if only_artists and excluded_artists:
        raise typer.BadParameter("--only-artist and --exclude-artist are mutually exclusive")
    if artist is not None and excluded_artists and artist.strip().casefold() in excluded_artists:
        raise typer.BadParameter(
            "--artist and --exclude-artist must not target the same artist"
        )
    if artist is not None and only_artists and artist.strip().casefold() not in only_artists:
        raise typer.BadParameter(
            "--artist substring is not present in any --only-artist value (no rows can match both)"
        )
    needs_meta = enrich or title is not None or artist is not None or bool(excluded_artists) or bool(only_artists)
    if needs_meta and meta_cache is None:
        meta_cache = _load_track_metadata()
    if excluded_artists:
        kept = []
        for r in stats:
            entry = (meta_cache or {}).get(str(r.get("track_id", "")))
            if entry is None:
                kept.append(r)
                continue
            _, a_val = entry
            if (a_val or "").strip().casefold() in excluded_artists:
                continue
            kept.append(r)
        stats = kept
    if only_artists:
        kept = []
        for r in stats:
            entry = (meta_cache or {}).get(str(r.get("track_id", "")))
            if entry is None:
                # Orphan rows are dropped: we cannot prove they belong to an
                # allowlisted artist. Use feedback-stats without --only-artist
                # (and with --orphaned) to surface them for cleanup.
                continue
            _, a_val = entry
            if (a_val or "").strip().casefold() not in only_artists:
                continue
            kept.append(r)
        stats = kept
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
        if fail_on_empty and not stats:
            raise typer.Exit(code=2)
        return
    if chosen == "csv":
        payload = _feedback_stats_as_csv(stats, enrich=enrich)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
            console.print(f"[green]wrote {len(stats)} row(s) to {output}[/green]")
        else:
            sys.stdout.write(payload)
            sys.stdout.flush()
        if fail_on_empty and not stats:
            raise typer.Exit(code=2)
        return

    if not stats:
        console.print("[dim]no feedback recorded yet[/dim]")
        if fail_on_empty:
            raise typer.Exit(code=2)
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
