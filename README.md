# LifeStream Intelligent Diary

Turns video recordings (vlogs, meetings, body-cam footage) into a timestamped
script and a structured daily diary.

**Status:** Stage 1 (core processing engine, local), without F7 (cloud infrastructure).

## Pipeline

```
video ─► F1 ffprobe check + FFmpeg audio (16 kHz mono WAV)
      ├─► audio branch:  F3 WhisperX (ASR + word timestamps) ─► F2 pyannote diarization
      └─► visual branch: F4 PySceneDetect cuts ─► FFmpeg keyframes      (separate process)
      ─► F3 merge words + speakers into turns ─► F5 attach keyframes to turns
      ─► F6 classify meeting | vlog ─► LLM summary (JSON) ─► Markdown ─► daily diary
```

Speaker identification uses acoustic models only (pyannote); no LLM decides who is speaking.

| Module | Feature |
|---|---|
| `media.py` | F1 validation, metadata, audio and frame extraction |
| `diarize.py` | F2 speaker diarization |
| `asr.py`, `merge.py` | F3 speech recognition, merge into Time + Speaker + Text |
| `scenes.py` | F4 scene detection and keyframe selection |
| `sync.py` | F5 frames ↔ text by timestamp |
| `summarize.py`, `llm.py`, `templates/` | F6 classification, summaries, Markdown |
| `pipeline.py` | Runs everything; saves each step; records step timings |
| `evals.py` | M1 speaker accuracy, M3b classification checks |
| `testdata.py` | Generated test data with known answers |

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

## Use

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

## Settings

All in `.env` (see `.env.example`). For the cloud GPU later: `WHISPER_MODEL=large-v3`,
`DEVICE=cuda`, `COMPUTE_TYPE=float16`, `DIARIZATION_DEVICE=cuda`.
