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


# ---------------- Stage 2: memory ----------------
def _store_or_exit(schema: str = "public"):
    from .memory.indexer import open_store
    from .memory.store import MemoryConfigError, MemoryUnavailable

    try:
        return open_store(get_settings(), schema)
    except (MemoryUnavailable, MemoryConfigError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(2)


@app.command()
def index(
    out: Path = typer.Option(DEFAULT_OUT),
    rebuild: bool = typer.Option(False, help="Drop the index and re-embed every video."),
):
    """Add every finished video to the search index (F8)."""
    from .memory.indexer import index_output

    settings = get_settings()
    with _store_or_exit() as store:
        if rebuild:
            store.reset()
        total = 0
        for folder in sorted(p for p in out.iterdir() if (p / "summary.json").exists()):
            n = index_output(folder, settings, store)
            total += n or 0
            typer.echo(f"  {folder.name}: {n} chunks")
        counts = store.count()
    typer.echo(f"Indexed {counts['videos']} videos, {counts['chunks']} chunks.")


@app.command()
def ask(
    question: str,
    today: Optional[str] = typer.Option(None, help="Pretend today is YYYY-MM-DD (for 'last week' etc.)."),
    show_sources: bool = typer.Option(True),
):
    """Ask a question about your videos (F9)."""
    from .intelligence.llm import make_llm
    from .memory.answer import answer_question
    from .timing import Timings, fmt_ts

    settings = get_settings()
    day = dt.date.fromisoformat(today) if today else dt.date.today()
    timings = Timings()
    with timings.step("connect.load_model", "Load embedding model", kind="load"):
        from .memory.embeddings import get_embedder
        get_embedder(settings.embed_model)
    with timings.step("connect.db", "Connect to database"):
        store = _store_or_exit()
    with store:
        result = answer_question(question, store, make_llm(settings), day, settings.search_k,
                                 timings=timings)
    tl = timings.finish()
    if result.date_from:
        typer.secho(f"(searched {result.date_from} to {result.date_to})", dim=True)
    typer.echo(result.answer)
    if show_sources and result.citations:
        typer.echo("\nSources:")
        for c in result.citations:
            typer.echo(f"  [{c.video_id} @ {fmt_ts(c.start)}] {c.title} · {c.date} · {c.section}")
    parts = ", ".join(f"{s['name']} {s['duration']:.2f}s" for s in tl["spans"])
    typer.secho(f"\n(took {tl['total']:.2f}s: {parts})", dim=True)


@app.command()
def speakers(
    video_id: str,
    names: list[str] = typer.Argument(None, help="LABEL=Name pairs, e.g. SPEAKER_00=Alice (empty name removes)."),
    out: Path = typer.Option(DEFAULT_OUT),
):
    """Show or set speaker names for a video (F10). Re-renders its diary and re-indexes it."""
    from .memory.indexer import index_output
    from .memory.registry import SpeakerRegistry
    from .memory.store import MemoryUnavailable
    from .pipeline import load_script, render_outputs

    settings = get_settings()
    if not (out / video_id / "script.json").exists():
        typer.secho(f"No processed video '{video_id}' in {out}", fg="red", err=True)
        raise typer.Exit(1)
    registry = SpeakerRegistry(settings.speakers_file)
    if names:
        pairs = {}
        for pair in names:
            label, sep, name = pair.partition("=")
            if not sep:
                typer.secho(f"Use LABEL=Name, got {pair!r}", fg="red", err=True)
                raise typer.Exit(1)
            pairs[label.strip()] = name
        registry.set_names(video_id, pairs)
        render_outputs(out, video_id, settings)
        try:
            index_output(out / video_id, settings)
            typer.echo("Saved. Diary re-rendered and search index updated.")
        except MemoryUnavailable:
            typer.echo("Saved. Diary re-rendered (search database not running; run `lifestream index` later).")
    current = registry.names_for(video_id)
    for label in dict.fromkeys(s.speaker for s in load_script(out / video_id)):
        typer.echo(f"  {label}: {current.get(label, '(no name)')}")


@app.command("eval-retrieval")
def eval_retrieval(k: int = typer.Option(3)):
    """F9 data test + M2, on generated videos (uses its own database schema)."""
    from .evaluation.retrieval import evaluate

    with _store_or_exit("eval_retrieval") as store:
        report = evaluate(store, k=k)
    for r in report.rows:
        typer.echo(f"  rank {r['rank'] or '-':>2}  {r['question']}  ->  {r['top']}")
    typer.echo(f"Recall@{k}: {report.recall_at_k:.0%}   MRR: {report.mrr:.2f}")
    typer.secho(f"M2 {'PASS' if report.m2_passed else 'FAIL'}: top result {report.m2_top}",
                fg="green" if report.m2_passed else "red")
    raise typer.Exit(0 if report.m2_passed else 1)


@app.command("eval-questions")
def eval_questions(
    labels: Path = typer.Option(Path("labels/questions.yaml")),
    k: int = typer.Option(3),
):
    """Hand-written questions about your real videos: is the right video in the top k?"""
    import yaml

    from .memory.query import parse_query

    spec = yaml.safe_load(labels.read_text())
    today = dt.date.fromisoformat(str(spec.get("today"))) if spec.get("today") else dt.date.today()
    hits = 0
    with _store_or_exit() as store:
        indexed = store.indexed_videos()
        for q in spec["questions"]:
            expected = set(q["videos"])
            if not expected & indexed:
                typer.echo(f"  skip  {q['question']}  (video not indexed)")
                continue
            parsed = parse_query(q["question"], today)
            found = store.search(parsed.text, k=k, date_from=parsed.date_from, date_to=parsed.date_to)
            rank = next((i for i, h in enumerate(found, 1) if h.video_id in expected), None)
            hits += bool(rank)
            typer.echo(f"  {'hit ' if rank else 'MISS'}  {q['question']}"
                       + (f"  (rank {rank}: {found[rank - 1].section})" if rank else
                          f"  (got {found[0].video_id if found else 'nothing'})"))
    asked = sum(1 for q in spec["questions"] if set(q["videos"]) & indexed)
    typer.echo(f"Top-{k} hit rate: {hits}/{asked}")


@app.command()
def worker(
    input_uri: str = typer.Argument(..., metavar="INPUT", help="s3://bucket/uploads/video.mp4"),
    output: str = typer.Option(..., help="s3://bucket/out/ (results go to <output>/<video_id>/)."),
    date: str = typer.Option("today", help="Recording date, YYYY-MM-DD ('today' = run date)."),
    num_speakers: Optional[int] = typer.Option(None),
    min_speakers: Optional[int] = typer.Option(None),
    max_speakers: Optional[int] = typer.Option(None),
    no_llm: bool = typer.Option(False, help="Stop after the script (skip classification/summary)."),
):
    """F7: process one video from S3 and upload the results (the AWS Batch job command)."""
    import boto3

    from .cloud.worker import run_job

    report = run_job(input_uri, output, date, get_settings(), s3=boto3.client("s3"),
                     run_llm=not no_llm, num_speakers=num_speakers,
                     min_speakers=min_speakers, max_speakers=max_speakers)
    typer.echo(json.dumps(report))


@app.command("eval-coldstart")
def eval_coldstart(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, help="A test video to upload."),
    date: str = typer.Option(dt.date.today().isoformat(), help="Recording date, YYYY-MM-DD."),
    infra: Path = typer.Option(Path("infra"), help="Terraform folder (names are read from its outputs)."),
    no_llm: bool = typer.Option(False, help="Skip the Claude steps in the test job."),
    save: Path = typer.Option(Path("data/eval/m3a"), help="Where to save the report."),
):
    """M3a: run one GPU job on AWS Batch and record the cold start (instance launch + image pull)."""
    import boto3

    from .evaluation.coldstart import run_coldstart, terraform_outputs

    outputs = terraform_outputs(infra)
    session = boto3.Session(region_name=outputs["region"])
    report = run_coldstart(video, date, outputs=outputs, session=session, run_llm=not no_llm,
                           log=typer.echo)
    save.mkdir(parents=True, exist_ok=True)
    path = save / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2))
    typer.echo(json.dumps(report, indent=2))
    typer.echo(f"Saved: {path}")
    raise typer.Exit(0 if report["status"] == "SUCCEEDED" else 1)


@app.command("eval-models")
def eval_models(
    model: list[str] = typer.Option(["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
                                    help="Claude models to compare (repeat the option); "
                                         "MODEL@EFFORT sets the summary effort, e.g. claude-opus-5-5@low."),
    video: list[str] = typer.Option(None, help="Video IDs in --out (default: every labelled, processed video)."),
    labels: Path = typer.Option(Path("labels")),
    out: Path = typer.Option(DEFAULT_OUT),
    save: Path = typer.Option(Path("data/eval/models"), help="Where summaries and results are saved."),
):
    """Compare Claude models on classification + summary: M3b checks, tokens, cost (calls the API)."""
    from .evaluation.models import compare_models, labelled_videos
    from .intelligence.llm import AnthropicLLM

    expected = labelled_videos(labels, out)
    videos = {v: expected.get(v) for v in video} if video else expected
    if not videos:
        typer.echo(f"No processed videos with labels in {labels}/ (or pass --video).")
        raise typer.Exit(1)
    save_root = save / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs = compare_models(list(model), videos, out, save_root, get_settings(), AnthropicLLM, log=typer.echo)

    typer.echo(f"\n{'video':18} {'model':22} {'type':8} {'M3b':5} {'points':>6} {'in':>7} {'out':>7} "
               f"{'cost $':>8} {'secs':>6}")
    for r in runs:
        cost = f"{r.cost_usd:.4f}" if r.cost_usd is not None else "?"
        typer.echo(f"{r.video_id[:18]:18} {r.model:22} {r.actual:8} {'PASS' if r.passed else 'FAIL':5} "
                   f"{r.points:>6} {r.input_tokens:>7} {r.output_tokens:>7} {cost:>8} {r.seconds:>6}")
        for p in r.problems[:3]:
            typer.echo(f"    - {p}")
    typer.echo(f"\nSummaries to read side by side: {save_root}/<model>/<video>/summary.md")


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
