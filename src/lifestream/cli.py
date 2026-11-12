"""Command line: `uv run lifestream --help`."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import typer

from .config import get_settings

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="LifeStream Stage 1: video -> script -> diary.")

DEFAULT_OUT = Path("data/out")


@app.command()
def process(
    videos: list[Path] = typer.Argument(..., help="One or more video files."),
    date: str = typer.Option(dt.date.today().isoformat(), help="Recording date, YYYY-MM-DD."),
    out: Path = typer.Option(DEFAULT_OUT, help="Output folder."),
    num_speakers: Optional[int] = typer.Option(None, help="Exact speaker count, if known."),
    min_speakers: Optional[int] = typer.Option(None),
    max_speakers: Optional[int] = typer.Option(None),
    force: bool = typer.Option(False, help="Redo every step instead of reusing saved results."),
    no_llm: bool = typer.Option(False, help="Stop after the script (skip classification/summary)."),
):
    """Run the Stage 1 pipeline on video files."""
    from .processing.diarize import DiarizationError
    from .intelligence.llm import LLMError
    from .processing.media import MediaError
    from .pipeline import process_video

    dt.date.fromisoformat(date)  # validate
    settings = get_settings()
    failed = 0
    for video in videos:
        try:
            folder = process_video(video, date, out, settings, num_speakers=num_speakers,
                                   min_speakers=min_speakers, max_speakers=max_speakers,
                                   force=force, run_llm=not no_llm)
            timings = json.loads((folder / "timings.json").read_text())
            typer.echo(f"Done: {folder}  ({timings.get('total', 0):.0f}s)")
        except MediaError as e:
            failed += 1
            typer.secho(f"Skipped {video}: {e}", fg="red", err=True)
        except (DiarizationError, LLMError) as e:
            typer.secho(f"Stopped on {video}: {e}\nFinished steps were saved; re-run to continue.",
                        fg="red", err=True)
            raise typer.Exit(2)
    if not no_llm:
        diary = out / "diaries" / f"{date}.md"
        if diary.exists():
            typer.echo(f"Daily summary: {diary}")
    raise typer.Exit(1 if failed else 0)


@app.command()
def diary(date: str, out: Path = typer.Option(DEFAULT_OUT)):
    """Rebuild the daily summary for DATE from processed videos."""
    from .pipeline import build_diary

    path = build_diary(out, date)
    typer.echo(path or f"No processed videos dated {date}.")


@app.command("eval-speakers")
def eval_speakers(
    output: Path = typer.Argument(..., help="A processed video's output folder."),
    rttm: Path = typer.Argument(..., help="Reference speaker labels (RTTM)."),
    target: float = typer.Option(0.90, help="Pass threshold for segment accuracy."),
):
    """M1: % of reference speech segments with the correct speaker, plus DER."""
    from .evaluation.metrics import der, load_rttm, segment_accuracy
    from .pipeline import _load_list, load_script
    from .schemas import SpeakerSegment

    reference = load_rttm(rttm)
    script = load_script(output)
    raw = _load_list(output / "diarization.json", SpeakerSegment)

    result = segment_accuracy(reference, script)
    typer.echo(f"Speaker mapping: {result.mapping}")
    typer.echo(f"Segment accuracy (final script): {result.correct}/{result.segments} = {result.accuracy:.1%}")
    typer.echo(f"DER, final script:      {der(reference, script):.1%}")
    typer.echo(f"DER, raw diarization:   {der(reference, raw):.1%}")
    for w in result.wrong[:10]:
        typer.echo(f"  wrong: {w}")
    passed = result.accuracy > target
    typer.secho(f"M1 {'PASS' if passed else 'FAIL'} (target > {target:.0%})",
                fg="green" if passed else "red")
    raise typer.Exit(0 if passed else 1)


@app.command("eval-classify")
def eval_classify(
    labels: Path = typer.Option(Path("labels"), help="Folder of <slot>.yaml label files."),
    out: Path = typer.Option(DEFAULT_OUT),
    min_points: float = typer.Option(3.0, help="Meeting summaries: min points per 10 minutes."),
):
    """M3b: classification correct + meeting summaries detailed enough."""
    from .evaluation.metrics import check_classification

    results = check_classification(labels, out, min_points)
    if not results:
        typer.echo(f"No label files with video_id and type in {labels}/")
        raise typer.Exit(1)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        typer.secho(f"{mark}  {r.video_id}: expected {r.expected}, got {r.actual}"
                    + (f"; {'; '.join(r.problems[:5])}" if r.problems else ""),
                    fg="green" if r.passed else "red")
    raise typer.Exit(0 if all(r.passed for r in results) else 1)


@app.command("make-testdata")
def make_testdata(out: Path = typer.Option(Path("data/test"), help="Where to write test data.")):
    """Generate synthetic test data: formats (F1), scene cuts (F4), 3-speaker overlap (M1)."""
    from .evaluation import testdata

    testdata.make_format_samples(out / "formats")
    cuts = testdata.make_scene_video(out / "scenes" / "cuts.mp4")
    (out / "scenes" / "cuts.json").write_text(json.dumps(cuts))
    conv = testdata.make_overlap_conversation(out / "v1")
    typer.echo(f"Formats: {out / 'formats'}\nScene video: {out / 'scenes'} (cuts at {cuts})\n"
               f"3-speaker conversation: {conv['mp4']} + {conv['rttm']}")


@app.command("download-ami")
def download_ami(out: Path = typer.Option(Path("data/ami")), meeting: str = "ES2004a"):
    """Download one labeled AMI meeting for the F2 diarization data test."""
    from .evaluation.testdata import download_ami as fetch

    typer.echo(fetch(out, meeting))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    out: Path = typer.Option(DEFAULT_OUT),
):
    """Start the dashboard API (and the built web app, if `web/dist` exists)."""
    import uvicorn

    from .api.app import create_app

    built = Path("web/dist/index.html").exists()
    typer.echo(f"Dashboard: http://{host}:{port}" if built else
               f"API on http://{host}:{port}. For the web app run `npm run dev` in web/ "
               "(or `npm run build` once to serve it from here).")
    uvicorn.run(create_app(out_root=out), host=host, port=port, log_level="warning")


def main() -> None:
    app()
