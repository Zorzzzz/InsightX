"""Category-aware summarization helpers."""

import re

CATEGORY_UI_META: dict[str, dict[str, str]] = {
    "education": {"label": "Education", "color": "#2563eb"},
    "technology": {"label": "Technology", "color": "#059669"},
    "business": {"label": "Business", "color": "#b45309"},
    "health": {"label": "Health", "color": "#dc2626"},
    "sports": {"label": "Sports", "color": "#1d4ed8"},
    "entertainment": {"label": "Entertainment", "color": "#7c3aed"},
    "other": {"label": "Other", "color": "#6b7280"},
    "dataset": {"label": "Dataset Analysis", "color": "#0f766e"},
    "medical": {"label": "Health", "color": "#dc2626"},
    "general": {"label": "Other", "color": "#6b7280"},
    "news": {"label": "News", "color": "#9a3412"},
}


def get_pipeline_kwargs(word_count: int, category: str) -> dict[str, int | float | bool]:
    if word_count < 30:
        word_count = 30

    min_ratio, max_ratio = 0.25, 0.40
    min_len = max(16, int(word_count * min_ratio))

    if word_count <= 1000:
        max_len = int(word_count * 0.40)
    elif word_count <= 5000:
        max_len = int(word_count * 0.35)
    elif word_count <= 20000:
        max_len = int(word_count * 0.30)
    else:
        max_len = int(word_count * 0.25)

    max_len = min(max_len, 900)
    max_len = max(64, max_len)

    if category == "health":
        min_len = max(min_len, 24)
        max_len = max(max_len, 64)
    elif category == "education":
        min_len = max(min_len, 22)
    elif category == "sports":
        max_len = min(max_len, 100)

    return {
        "min_length": min_len,
        "max_length": max_len,
        "num_beams": 2,
        "do_sample": False,
        "no_repeat_ngram_size": 4,
        "repetition_penalty": 1.2,
        "early_stopping": True,
        "length_penalty": 1.0,
    }


def _is_hallucinated_word(token: str) -> bool:
    core = token.strip(".,!?;:()[]\"'").lower()
    if not core or len(core) < 5:
        return False
    if re.search(r"([a-z]{2,4})\1{1,}", core):
        return True
    if re.search(r"([aeiou])\1{2,}", core):
        return True
    return False


def _dedupe_repetitions(text: str) -> str:
    cleaned_tokens = []
    for token in text.split():
        if "-" in token:
            parts = token.split("-")
            if any(_is_hallucinated_word(part) for part in parts):
                continue
        if _is_hallucinated_word(token):
            continue
        cleaned_tokens.append(token)
    text = " ".join(cleaned_tokens)

    text = re.sub(r"\b(\w{2,5})(?:-\1){1,}(?:-\w*)?\b", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\d+[.)]\s*){3,}", "", text)
    text = re.sub(r"(?:[-–]\s*\d+(?:\.\d+)?\s*){3,}", "", text)
    text = re.sub(r"\(\s*\?\s*\)", "", text)
    text = re.sub(r"\b(\w+)(?:\s+\1\b){2,}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(\w+\s+\w+)(?:\s+\1\b){1,}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"(\b\w+\b)([\s,.()!?]+\1\b){2,}", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s*\.\s*\.\s*", ". ", text)
    text = re.sub(r"^[\s,.\-–]+", "", text)

    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept_sentences = []
    for sentence in sentences:
        clean_letters = re.sub(r"[^a-zA-Z]", "", sentence)
        if len(clean_letters) >= 8:
            kept_sentences.append(sentence)
    return " ".join(kept_sentences).strip()


def apply_style(summary: str, source_text: str, category: str) -> str:
    trimmed = _dedupe_repetitions(summary.strip())

    prefixes = {
        "education": "[Education brief] ",
        "technology": "[Tech summary] ",
        "business": "[Business update] ",
        "health": "[Health brief] ",
        "sports": "[Sports summary] ",
        "entertainment": "[Entertainment brief] ",
        "medical": "[Health brief] ",
        "news": "[News summary] ",
    }
    return f"{prefixes.get(category, '')}{trimmed}"


def get_ui_meta(category: str) -> dict[str, str]:
    return CATEGORY_UI_META.get(category, CATEGORY_UI_META["other"])
