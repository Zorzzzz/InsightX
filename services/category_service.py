from core.classifier import classify
from core.summarizer_prompt import get_ui_meta

SUPPORTED_CATEGORIES = {
    "education",
    "technology",
    "business",
    "health",
    "sports",
    "entertainment",
    "other",
    "dataset",
    "medical",
    "general",
    "news",
}
CONFIDENCE_OVERRIDE_THRESHOLD = 0.15
CATEGORY_ALIASES = {
    "general": "other",
    "medical": "health",
}


def normalize_category(category: str | None) -> str:
    normalized = (category or "").strip().lower()
    if not normalized:
        return "other"
    return CATEGORY_ALIASES.get(normalized, normalized)


def resolve_category(text: str, category_override: str | None = None) -> tuple[str, float, dict[str, float]]:
    detected_category, scores = classify(text)
    best_score = max(scores.values()) if scores else 0.0

    normalized_override = normalize_category(category_override)
    if category_override and normalized_override in SUPPORTED_CATEGORIES and best_score < CONFIDENCE_OVERRIDE_THRESHOLD:
        return normalized_override, float(best_score), scores

    return normalize_category(detected_category), float(best_score), scores


def get_category_ui_meta(category: str) -> dict[str, str]:
    return get_ui_meta(normalize_category(category))
