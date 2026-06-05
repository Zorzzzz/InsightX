"""
Lightweight offline classifier for InsightX categories.

Priority order:
    education -> technology -> business -> health -> sports -> entertainment -> other
"""

import re

FORCED_EDUCATION_TERMS = (
    "lecture",
    "course",
    "assignment",
    "exam",
    "university",
    "student",
    "professor",
    "tutorial",
    "linux",
    "shell programming",
    "machine learning",
    "deep learning",
    "cnn",
    "academic",
)

PRIORITY_ORDER = (
    "education",
    "technology",
    "business",
    "health",
    "sports",
    "entertainment",
)

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "education": [
        "lecture", "course", "assignment", "exam", "university", "student",
        "professor", "tutorial", "linux", "shell programming",
        "machine learning", "deep learning", "cnn", "academic",
        "classroom", "homework", "quiz", "semester", "curriculum",
        "lab", "worksheet", "faculty", "study", "learning",
    ],
    "technology": [
        "software", "hardware", "algorithm", "code", "programming",
        "developer", "engineer", "platform", "application", "api",
        "database", "server", "cloud", "network", "protocol",
        "artificial intelligence", "neural network", "model", "dataset",
        "llm", "gpt", "robot", "automation", "cybersecurity",
        "encryption", "privacy", "startup", "product launch", "release",
        "smartphone", "laptop", "device", "chip", "processor", "gpu",
        "open source", "github", "repository", "framework", "library",
        "deployment", "blockchain", "cryptocurrency", "web3",
    ],
    "business": [
        "revenue", "profit", "loss", "earnings", "quarterly", "annual",
        "market", "stock", "share", "investor", "investment", "fund",
        "acquisition", "merger", "partnership", "ceo", "cfo", "cto",
        "executive", "board", "shareholder", "valuation", "ipo",
        "equity", "debt", "funding", "capital", "inflation", "recession",
        "economic", "supply chain", "retail", "sales", "marketing",
        "brand", "strategy", "growth", "forecast", "guidance",
        "competitor", "market share", "industry", "sector", "trade",
    ],
    "health": [
        "patient", "doctor", "physician", "nurse", "hospital", "clinic",
        "diagnosis", "treatment", "therapy", "surgery", "procedure",
        "medication", "prescription", "symptom", "disease", "condition",
        "infection", "virus", "immune", "vaccine", "clinical trial",
        "health", "wellness", "mental health", "anxiety", "depression",
        "cancer", "diabetes", "hypertension", "stroke", "cardiac",
        "oncology", "neurology", "cardiology", "pediatrics",
    ],
    "sports": [
        "game", "match", "tournament", "championship", "league", "season",
        "score", "goal", "win", "loss", "victory", "player", "team",
        "coach", "athlete", "stadium", "playoffs", "draft", "basketball",
        "football", "soccer", "baseball", "tennis", "cricket", "rugby",
        "hockey", "golf", "marathon", "olympics", "world cup", "nba",
        "nfl", "mlb", "nhl", "fifa", "touchdown", "penalty", "foul",
    ],
    "entertainment": [
        "movie", "film", "music", "album", "song", "concert", "festival",
        "actor", "actress", "celebrity", "show", "series", "episode",
        "streaming", "trailer", "box office", "director", "producer",
        "cinema", "television", "tv", "podcast", "gaming", "esports",
        "performance", "comedy", "drama", "documentary", "award",
    ],
}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _count_keyword_occurrences(text: str, keyword: str) -> int:
    return len(re.findall(r"\b" + re.escape(keyword) + r"\b", text))


def _forced_education_match(text: str) -> bool:
    return any(_count_keyword_occurrences(text, term) > 0 for term in FORCED_EDUCATION_TERMS)


def classify(text: str) -> tuple[str, dict[str, float]]:
    normalised = _normalise(text)
    scores = {category: 0.0 for category in (*PRIORITY_ORDER, "other")}

    if not normalised:
        return "other", scores

    if _forced_education_match(normalised):
        scores["education"] = 1.0
        return "education", scores

    ranking: list[tuple[float, int, str]] = []
    for index, category in enumerate(PRIORITY_ORDER):
        weighted_hits = 0.0
        matched_terms = 0

        for keyword in CATEGORY_KEYWORDS[category]:
            occurrences = _count_keyword_occurrences(normalised, keyword)
            if not occurrences:
                continue
            matched_terms += 1
            weighted_hits += occurrences * (1.5 if " " in keyword else 1.0)

        confidence = min(1.0, round((weighted_hits * 0.08) + (matched_terms * 0.02), 4))
        scores[category] = confidence
        ranking.append((weighted_hits, -index, category))

    best_hits, _priority_bias, best_category = max(ranking, default=(0.0, 0, "other"))
    if best_hits < 1.0 or scores[best_category] < 0.10:
        return "other", scores

    return best_category, scores
