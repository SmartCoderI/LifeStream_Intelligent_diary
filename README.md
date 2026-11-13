# LifeStream Intelligent Diary

Turns video recordings (vlogs, meetings, body-cam footage) into a timestamped
script and a structured daily diary.

**Status:** Stage 1 (core processing engine), Stage 2 (memory: search, answers with citations,
speaker names) and a web dashboard run locally. Stage 3 deploys the same dashboard to AWS:
uploads go to S3 and start a GPU job on AWS Batch automatically (see [infra/](infra/README.md)).

## Pipeline

```
video ─► F1 ffprobe check + FFmpeg audio (16 kHz mono WAV)
      ├─► audio branch:  F3 WhisperX (ASR + word timestamps) ─► F2 pyannote diarization
      └─► visual branch: F4 PySceneDetect cuts ─► FFmpeg keyframes      (separate process)
      ─► F3 merge words + speakers into turns ─► F5 attach keyframes to turns
      ─► F6 classify meeting | vlog ─► LLM summary (JSON) ─► Markdown ─► daily diary
      ─► F8 chunk + embed (bge-small, local) ─► Postgres + pgvector

question ─► F9 date phrase -> date filter ─► vector search ─► Claude answer with citations
```

Speaker identification uses acoustic models only (pyannote); no LLM decides who is speaking.

Code is organized by the architecture layers in the requirements doc:

```
src/lifestream/
├── cli.py, config.py       command line, settings (.env)
├── schemas.py              data shapes shared by every layer
├── pipeline.py             runs the steps in order, saves each step
├── timing.py               latency timeline, run history, query log
├── processing/             Processing Core (F1-F5), no LLM
│   ├── media.py            F1 validation, metadata, audio and frame extraction
│   ├── diarize.py          F2 speaker diarization
│   ├── asr.py, merge.py    F3 speech recognition, merge into Time + Speaker + Text
│   ├── scenes.py           F4 scene detection and keyframe selection
│   └── sync.py             F5 frames <-> text by timestamp
├── intelligence/           Intelligence Layer (F6)
│   ├── llm.py              Claude client (swappable)
│   ├── summarize.py        classification, summaries, daily diaries
│   └── templates/          Markdown templates
├── api/                    Ingestion + API (F11/F12)
│   ├── app.py              REST API for the dashboard (local or cloud mode)
│   └── jobs.py             background processing queue (local mode)
├── cloud/                  AWS (F7, Stage 3)
│   ├── worker.py           the Batch GPU job: S3 in -> pipeline -> S3 out, progress in status.json
│   ├── sync.py             cloud mode: S3 upload links, follows jobs, downloads + indexes results
│   └── prefetch.py         bakes the models into the worker image
├── memory/                 Memory Layer (Stage 2: F8-F10)
│   ├── registry.py         F10 speaker names (YAML, per video)
│   ├── chunking.py         F8 transcript windows + summary paragraphs
│   ├── embeddings.py       local embedding model
│   ├── store.py            Postgres + pgvector: schema, indexing, search
│   ├── indexer.py          connects processed videos to the index
│   ├── query.py            F9 "last week" -> date range
│   └── answer.py           F9 answers with citations
└── evaluation/             success metrics (M1, M2, M3b) and generated test data

tests/                      same layout as src/lifestream/
docs/rag-notes.md           RAG background and the choices made here
web/src/
├── pages/                  Videos, Video (summary), Ask, Latency
├── components/             layout/, library/ (upload + list), video/ (video page tabs), latency/
└── lib/                    API client, formatting, polling
```

## Setup

```bash
brew install ffmpeg uv
uv sync
cp .env.example .env    # then fill in the two keys
```

- `HF_TOKEN`: accept the terms at https://huggingface.co/pyannote/speaker-diarization-community-1,
  then create a read token at https://huggingface.co/settings/tokens.
- `ANTHROPIC_API_KEY`: from https://console.anthropic.com.

The first run downloads the speech models (~2 GB).

## Dashboard

```bash
./dev.sh build    # builds the web app and serves everything: open http://127.0.0.1:8000
./dev.sh          # development: open http://localhost:5173 (live reload while editing web/)
```

Ctrl+C stops both the backend and the web server.

- **Videos**: upload a video with its recording date, watch processing progress, retry or delete.
- **Video page**: player plus Summary / Transcript / Speakers / Scenes tabs; every timestamp
  and key moment jumps the player to that point.
- **Ask / Search**: questions in plain language ("What did we discuss about the frontend
  last week?"); answers cite the video and moment, and each source opens the video there.
- **Speakers tab**: name speakers; summaries, transcripts and search use the names.
- **Latency** (sidebar) and the video page's **Timing** tab: how long each run took, step by step
  (see below).

Uploads are saved in `data/uploads/` and processed one at a time in the background.
Web tests: `cd web && npm test`.

## Command line

```bash
# process videos (date = recording date)
uv run lifestream process ../multiple_student.mp4 --date 2026-11-04

# several videos on the same day -> one daily summary
uv run lifestream process ../data_center_one_person.mp4 ../Under-the-hood-*.mp4 --date 2026-11-03

# only the script, no LLM
uv run lifestream process video.mp4 --no-llm
```

Output per video in `data/out/<video_id>/`:

| File | Content |
|---|---|
| `metadata.json` | duration, codecs, fps, date |
| `audio.wav` | 16 kHz mono audio |
| `words.json`, `diarization.json` | raw ASR words, raw speaker segments |
| `scenes.json`, `keyframes.json`, `keyframes/` | scene cuts and keyframe JPGs |
| `script.json`, `script.md` | Time + Speaker + Text, with frames per turn |
| `summary.json`, `summary.md` | classification + vlog or meeting summary |
| `timings.json` | seconds per step |

Daily summaries go to `data/out/diaries/<date>.md`.

Re-running reuses saved steps (e.g. change a prompt without re-transcribing).
Use `--force` to redo everything.

## Tests

```bash
uv run pytest              # fast tests (~5 s): F1, F3, F4, F5, F6, metrics, pipeline wiring
uv run lifestream download-ami
uv run pytest -m slow      # F2 data test: diarization DER on a labeled AMI meeting (needs HF_TOKEN)
```

## Success metrics (Stage 1)

```bash
uv run lifestream make-testdata    # formats, scene cuts, 3-speaker overlap conversation (V1)

# M1: > 90% of speech segments with the correct speaker (3 overlapping speakers)
uv run lifestream process data/test/v1/conversation.mp4 --date 2026-11-05 --num-speakers 3
uv run lifestream eval-speakers data/out/conversation data/test/v1/conversation.rttm

# M3b: meeting/vlog classification + detailed meeting summaries
uv run lifestream eval-classify --labels labels
```

V1 uses macOS text-to-speech voices, which are easier than real voices. Replace it with
a hand-labeled real recording (RTTM file) for the final M1 check.

Test video slots and their expected answers are in `labels/`.

## Latency monitoring

Every run records a timeline: upload (measured from when the browser starts sending), save,
queue wait, each processing step with start/end times, model loading as sub-steps, each
Claude call with model and token counts, and search indexing. Steps that reused saved
results are marked "reused". Each Ask / Search query records time-phrase parsing, search and
answer generation.

- **Video page → Timing tab**: the latest run as a timeline (live while processing).
- **Latency page**: run history (including failed runs and deleted videos) with total, upload,
  queue, processing (× video length), Claude time and the slowest step; click a run for its
  timeline. Below it, recent queries.
- Files: `data/out/<video>/timeline.json` (latest run), `data/out/_logs/runs.jsonl` and
  `data/out/_logs/queries.jsonl` (history, one JSON object per line). `timings.json` keeps the
  simple step → seconds form.
- `lifestream ask` prints its timing too.

## Search and speaker names (Stage 2)

The search database runs in Docker (`./dev.sh` starts it, or `docker compose up -d db`).
Finished videos are indexed automatically.

```bash
uv run lifestream index                     # (re)index all processed videos
uv run lifestream ask "What did we discuss about the frontend last week?"
uv run lifestream speakers multiple-student SPEAKER_04="Ms. Lee"   # names live in data/speakers.yaml
uv run lifestream eval-retrieval            # F9 recall@k / MRR + M2 on generated videos
uv run lifestream eval-questions            # 16 questions about the real videos (labels/questions.yaml)
```

Database tests start a temporary Postgres container, so Docker must be running for `uv run pytest`
(they are skipped otherwise). See [docs/rag-notes.md](docs/rag-notes.md) for how search works.

## Cloud deployment (Stage 3)

```
browser ──HTTPS──► CloudFront ──► EC2 t4g.small: this FastAPI app (+ web app) and Postgres/pgvector
   │  POST /api/videos → presigned S3 link; the browser PUTs the file straight to S3
   ▼
S3 uploads/<id>/<id>.mp4 ──EventBridge rule──► Batch GPU job (cloud/worker.py)
                                                 └─► S3 out/<id>/ (+ status.json after every step)
the server follows status.json; on "done" it downloads the results, rebuilds the diary, indexes for search
```

```bash
infra/deploy.sh --worker v2   # first time: build the worker image, create everything, start the server
infra/deploy.sh               # later: redeploy the server and web app
```

The script prints the dashboard URL and how to read the API key; the web app asks for the key
once. Details, costs and teardown: [infra/README.md](infra/README.md).

API (cloud mode; every `/api` route needs the `X-API-Key` header, otherwise 401):

| Spec | Route |
|---|---|
| upload_video | `POST /api/videos` `{date, filename, size_bytes}` → `{video_id, presigned_put_url, upload_headers}`; PUT the file with `upload_headers`, then `POST /api/videos/{id}/uploaded` |
| get_status | `GET /api/videos/{id}/status` → `{status, step, progress, type, step_timings, error}` |
| get_summary | `GET /api/videos/{id}/summary`, `GET /api/days/{date}/summary` |
| query_memory | `POST /api/query` `{question}` → `{answer, citations[]}` |

Locally nothing changes: without `S3_BUCKET` in `.env` the app processes videos on this computer.

## Settings

All in `.env` (see `.env.example`). For the cloud GPU later: `WHISPER_MODEL=large-v3`,
`DEVICE=cuda`, `COMPUTE_TYPE=float16`, `DIARIZATION_DEVICE=cuda`.
