"""Download every model the worker needs at image build time (M3a: a job never waits on downloads).

    python -m lifestream.cloud.prefetch --whisper-model large-v3        (needs HF_TOKEN for pyannote)
    HF_HUB_OFFLINE=1 python -m lifestream.cloud.prefetch --verify       (fails if anything is missing)

Where the files land is set by HF_HOME (Whisper, pyannote), TORCH_HOME (word alignment)
and NLTK_DATA (sentence splitter). The Dockerfile points all three at /opt/models.
The voice-activity model ships inside the whisperx package, so it needs no download.
"""

from __future__ import annotations

import argparse
import os
import sys


def prefetch(whisper_model: str, diarization_model: str, language: str, hf_token: str | None) -> None:
    from faster_whisper.utils import download_model
    from huggingface_hub import snapshot_download
    import nltk
    import whisperx

    print(f"Whisper: {whisper_model}", flush=True)
    download_model(whisper_model)
    print(f"Alignment model: {language}", flush=True)
    whisperx.load_align_model(language_code=language, device="cpu")
    print("Sentence splitter: punkt_tab", flush=True)
    if not nltk.download("punkt_tab", download_dir=os.environ.get("NLTK_DATA"), quiet=True):
        raise RuntimeError("Couldn't download NLTK punkt_tab")
    print(f"Diarization: {diarization_model}", flush=True)
    if not hf_token:
        raise RuntimeError("HF_TOKEN is needed to download the pyannote model (pass it as a build secret)")
    snapshot_download(diarization_model, token=hf_token)


def verify(whisper_model: str, diarization_model: str, language: str) -> None:
    """Load everything with downloads disabled, the way a job will."""
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("Run --verify with HF_HUB_OFFLINE=1")
    from faster_whisper.utils import download_model
    from pyannote.audio import Pipeline
    import nltk
    import whisperx

    download_model(whisper_model, local_files_only=True)
    whisperx.load_align_model(language_code=language, device="cpu", model_cache_only=True)
    nltk.data.find("tokenizers/punkt_tab/english/")
    Pipeline.from_pretrained(diarization_model)
    print("All models load offline.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--whisper-model", default=os.environ.get("WHISPER_MODEL", "large-v3"))
    parser.add_argument("--diarization-model", default=os.environ.get(
        "DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1"))
    parser.add_argument("--language", default=os.environ.get("LANGUAGE", "en"))
    parser.add_argument("--verify", action="store_true", help="Check that everything loads offline.")
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.whisper_model, args.diarization_model, args.language)
        else:
            prefetch(args.whisper_model, args.diarization_model, args.language, os.environ.get("HF_TOKEN"))
        return 0
    except Exception as e:  # noqa: BLE001 - top-level: fail the image build with a clear message
        print(f"Model prefetch failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
