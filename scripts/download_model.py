"""
Download a local summarization model into ./model for InsightX.

Usage:
    python scripts/download_model.py

Optional env vars:
    BRIEFLY_MODEL_ID   Hugging Face model id
    BRIEFLY_MODEL_DIR  output directory
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MODEL_ID = "sshleifer/distilbart-cnn-12-6"
BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.environ.get("BRIEFLY_MODEL_DIR", BASE_DIR / "model"))
MODEL_ID = os.environ.get("BRIEFLY_MODEL_ID", DEFAULT_MODEL_ID)


def main() -> int:
    print(f"Downloading model: {MODEL_ID}")
    print(f"Saving to: {MODEL_DIR}")

    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:
        print("transformers is not installed.")
        print("Install it first, then rerun this script.")
        print(f"Details: {exc}")
        return 1

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID)

    tokenizer.save_pretrained(MODEL_DIR)
    model.save_pretrained(MODEL_DIR)

    print("Model download complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
