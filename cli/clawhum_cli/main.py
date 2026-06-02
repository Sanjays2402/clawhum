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
    table.add_column("Track ID")
    for i, m in enumerate(results, 1):
        table.add_row(str(i), f"{m.score:.3f}", m.track.title, m.track.artist, m.track.id)
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
    s = get_settings()
    from clawhum_library.feedback import record_feedback
    record_feedback(s.feedback_path, query_id, track_id, score, vote)
    console.print("[green]ok[/green]")


if __name__ == "__main__":
    app()
