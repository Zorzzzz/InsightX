import hashlib
import os
import re
import time
from collections import Counter

from flask import current_app

from core.csv_analyzer import is_structured_dataset_report
from core.summarizer_prompt import apply_style, get_pipeline_kwargs
from core.text_extractor import _looks_like_garbage, clean_text, extract_and_summarize, read_file_object
from database.summary_repository import create_summary
from services.category_service import get_category_ui_meta, resolve_category

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "model")
MAX_INPUT_WORDS = 700
INFERENCE_WORD_LIMIT = 5000
SPREADSHEET_EXTENSIONS = (".csv", ".xlsx")

_SUMMARY_CACHE: dict[str, str] = {}
_CACHE_MAX_SIZE = 256


class SummaryServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class LocalBartSummarizer:
    """Pipeline-compatible wrapper around local seq2seq models."""

    def __init__(self, model, tokenizer, torch_module, device):
        self.model = model
        self.tokenizer = tokenizer
        self.torch = torch_module
        self.device = torch_module.device("cuda:0" if device == 0 else "cpu")
        self.max_input_tokens = min(
            getattr(model.config, "max_position_embeddings", 1024),
            1024,
        )

    def __call__(self, text, truncation=True, **generate_kwargs):
        generate_kwargs.setdefault("num_beams", 1)
        generate_kwargs.setdefault("do_sample", False)
        generate_kwargs.setdefault("early_stopping", True)
        generate_kwargs.setdefault("no_repeat_ngram_size", 3)

        texts = [text] if isinstance(text, str) else list(text)
        outputs = []

        for item in texts:
            inputs = self.tokenizer(
                item,
                return_tensors="pt",
                max_length=self.max_input_tokens,
                truncation=truncation,
            )
            inputs = {key: value.to(self.device) for key, value in inputs.items()}

            with self.torch.inference_mode():
                generated_ids = self.model.generate(**inputs, **generate_kwargs)

            outputs.append({
                "summary_text": self.tokenizer.decode(
                    generated_ids[0],
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=True,
                )
            })

        return outputs


class ExtractiveFallbackSummarizer:
    """Offline fallback summarizer when the transformer model is unavailable."""

    STOPWORDS = {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
        "by", "for", "from", "had", "has", "have", "he", "her", "hers",
        "him", "his", "i", "if", "in", "into", "is", "it", "its", "itself",
        "me", "more", "most", "my", "of", "on", "or", "our", "ours", "she",
        "so", "that", "the", "their", "theirs", "them", "themselves", "they",
        "this", "those", "to", "too", "us", "was", "we", "were", "what",
        "when", "where", "which", "who", "why", "will", "with", "you",
        "your", "yours",
    }

    backend_name = "extractive-fallback"

    def __call__(self, text, truncation=True, **generate_kwargs):
        texts = [text] if isinstance(text, str) else list(text)
        outputs = []

        for item in texts:
            outputs.append({
                "summary_text": self._summarize(
                    item,
                    min_length=generate_kwargs.get("min_length", 32),
                    max_length=generate_kwargs.get("max_length", 120),
                )
            })

        return outputs

    @staticmethod
    def _normalise_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    @classmethod
    def _word_tokens(cls, text: str) -> list[str]:
        return re.findall(r"[A-Za-z][A-Za-z'-]*", text.lower())

    @staticmethod
    def _truncate_to_words(text: str, limit: int) -> str:
        words = text.split()
        if len(words) <= limit:
            return text.strip()

        trimmed = " ".join(words[:limit]).rstrip(" ,;:-")
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."
        return trimmed

    def _summarize(self, text: str, min_length: int, max_length: int) -> str:
        clean_source = self._normalise_whitespace(text)
        if not clean_source:
            return ""

        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", clean_source)
            if sentence.strip()
        ]
        if not sentences:
            return self._truncate_to_words(clean_source, max_length)
        if len(sentences) == 1:
            return self._truncate_to_words(sentences[0], max_length)

        content_tokens = [
            token
            for sentence in sentences
            for token in self._word_tokens(sentence)
            if token not in self.STOPWORDS
        ]
        if not content_tokens:
            return self._truncate_to_words(" ".join(sentences[:2]), max_length)

        frequencies = Counter(content_tokens)
        ranked_sentences = []

        for index, sentence in enumerate(sentences):
            tokens = [
                token
                for token in self._word_tokens(sentence)
                if token not in self.STOPWORDS
            ]
            if not tokens:
                continue

            average_weight = sum(frequencies[token] for token in tokens) / len(tokens)
            position_bonus = 1.12 if index == 0 else 1.06 if index < 3 else 1.0
            length_penalty = min(1.0, 28 / max(len(tokens), 1))
            score = average_weight * position_bonus * max(length_penalty, 0.55)
            ranked_sentences.append((score, index, sentence))

        if not ranked_sentences:
            return self._truncate_to_words(" ".join(sentences[:2]), max_length)

        target_words = min(max_length, max(min_length, max_length // 2))
        selected_indices = set()
        selected_word_total = 0

        for _, index, sentence in sorted(ranked_sentences, key=lambda item: item[0], reverse=True):
            if index in selected_indices:
                continue

            selected_indices.add(index)
            selected_word_total += len(sentence.split())
            if selected_word_total >= target_words:
                break

        ordered_sentences = [sentences[index] for index in sorted(selected_indices)]
        summary = self._normalise_whitespace(" ".join(ordered_sentences))

        if len(summary.split()) < min_length:
            for sentence in sentences:
                if sentence in ordered_sentences:
                    continue

                ordered_sentences.append(sentence)
                summary = self._normalise_whitespace(" ".join(ordered_sentences))
                if len(summary.split()) >= min_length:
                    break

        return self._truncate_to_words(summary, max_length)


def _cache_key(text: str, category: str, min_len: int, max_len: int) -> str:
    text_hash = hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()
    return f"{text_hash}:{category}:{min_len}:{max_len}"


def _cache_get(key: str) -> str | None:
    return _SUMMARY_CACHE.get(key)


def _cache_set(key: str, value: str) -> None:
    if len(_SUMMARY_CACHE) >= _CACHE_MAX_SIZE:
        _SUMMARY_CACHE.pop(next(iter(_SUMMARY_CACHE)))
    _SUMMARY_CACHE[key] = value


def _activate_fallback_summarizer(reason: str):
    print(f"WARNING: {reason}")
    print("         Using built-in extractive fallback summarizer.")
    current_app.summarizer = ExtractiveFallbackSummarizer()
    current_app.summarizer_backend = current_app.summarizer.backend_name
    return current_app.summarizer


def load_summarizer():
    if hasattr(current_app, "summarizer"):
        return current_app.summarizer

    if not os.path.isdir(MODEL_PATH):
        print(f"WARNING: Model folder not found at {MODEL_PATH}")
        print("         Run scripts/download_model.py first (one-time, requires internet).")
        return None

    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        try:
            torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "4")))
        except Exception:
            pass

        use_gpu = torch.cuda.is_available()
        print(f"Loading model ({'GPU' if use_gpu else 'CPU'}) from {MODEL_PATH} ...")

        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_PATH, local_files_only=True)
        model.eval()

        if use_gpu:
            model = model.to("cuda")
            try:
                model = model.half()
                print("GPU detected - running fp16.")
            except Exception:
                print("GPU detected - running fp32.")
            device = 0
        else:
            print("Applying INT8 quantization for CPU speed ...")
            torch.quantization.quantize_dynamic(
                model,
                {torch.nn.Linear},
                dtype=torch.qint8,
                inplace=True,
            )
            device = -1
            print("Quantization applied.")

        current_app.summarizer = LocalBartSummarizer(
            model=model,
            tokenizer=tokenizer,
            torch_module=torch,
            device=device,
        )
        current_app.summarizer_backend = "local-transformer"

        try:
            print("Warming up model ...")
            start = time.time()
            current_app.summarizer(
                "This is a warm up sentence used only to initialize the model. "
                "It contains enough content to trigger the full generation path "
                "so the first real request is fast.",
                min_length=8,
                max_length=20,
            )
            print(f"Warm-up done in {time.time() - start:.1f}s.")
        except Exception as warmup_error:
            print(f"WARNING: Warm-up skipped: {warmup_error}")

        return current_app.summarizer
    except Exception as error:
        print(f"WARNING: Could not load model: {error}")
        return None


def load_available_summarizer():
    if hasattr(current_app, "fallback_summarizer"):
        return current_app.fallback_summarizer

    summarizer = load_summarizer()
    if summarizer is not None:
        return summarizer

    if hasattr(current_app, "fallback_summarizer"):
        return current_app.fallback_summarizer

    print("WARNING: Local transformer model unavailable.")
    print("         Using built-in extractive fallback summarizer.")
    current_app.fallback_summarizer = ExtractiveFallbackSummarizer()
    current_app.summarizer = current_app.fallback_summarizer
    current_app.summarizer_backend = current_app.fallback_summarizer.backend_name
    return current_app.fallback_summarizer


def get_summarizer_backend_label() -> str:
    backend_name = getattr(current_app, "summarizer_backend", "")
    if backend_name == "local-transformer":
        return "local model"
    if backend_name == "extractive-fallback":
        return "offline fallback"
    return "summarizer"


def _is_spreadsheet(file_name: str | None) -> bool:
    lower_name = (file_name or "").lower()
    return lower_name.endswith(SPREADSHEET_EXTENSIONS)


def extract_text_from_upload(file_obj) -> tuple[str, bool]:
    file_name = getattr(file_obj, "filename", "")
    raw_text = read_file_object(file_obj)
    is_data_report = _is_spreadsheet(file_name) or is_structured_dataset_report(raw_text)

    if is_data_report:
        return raw_text.strip(), True
    return clean_text(raw_text), False


def _make_summarizer_fn(summarizer, category: str):
    def _run(text: str) -> str:
        kwargs = get_pipeline_kwargs(len(text.split()), category)
        cache_key = _cache_key(
            text,
            category,
            kwargs.get("min_length", 0),
            kwargs.get("max_length", 0),
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        result = summarizer(text, truncation=True, **kwargs)
        styled = apply_style(result[0]["summary_text"], text, category)
        _cache_set(cache_key, styled)
        return styled

    return _run


def _build_dataset_response(text: str, user_id: int, file_name: str | None = None) -> dict:
    word_count = len(text.split())
    stats = {
        "confidence": 1.0,
        "compression": 0.0,
        "original_words": word_count,
        "summary_words": word_count,
        "time_sec": 0.0,
    }
    create_summary(
        user_id=user_id,
        file_name=file_name,
        summary=text,
        category="dataset",
        stats=stats,
    )
    return {
        "summary": text,
        "domain_type": "Dataset Analysis",
        "confidence_score": 1.0,
        "compression_ratio": 1.0,
        "keywords": [],
        "explanation": (
            "Detected a spreadsheet dataset and generated a structured analysis "
            "instead of a prose summary."
        ),
    }


def summarize_input(
    *,
    text: str,
    user_id: int,
    file_name: str | None = None,
    category_override: str | None = None,
    is_data_report: bool = False,
) -> dict:
    if not text:
        raise SummaryServiceError("No text provided.", 400)

    word_count = len(text.split())

    if is_data_report:
        return _build_dataset_response(text, user_id=user_id, file_name=file_name)

    if word_count < 30:
        if _is_spreadsheet(file_name):
            raise SummaryServiceError(
                f"CSV is too small to summarize ({word_count} words after parsing). "
                "Add more rows or longer text fields (descriptions, comments) and try again.",
                400,
            )
        raise SummaryServiceError(
            f"Text too short ({word_count} words). Provide at least 30 words.",
            400,
        )

    if (file_name or "").lower().endswith(".pdf") and _looks_like_garbage(text):
        raise SummaryServiceError(
            "This PDF looks data-heavy (lots of numbers, tables, or citations) "
            "or may be a scanned/image PDF. Text summarization works best on "
            "regular prose. Try one of these instead:\n"
            "- Copy-paste the actual paragraph(s) you want summarized into the text box\n"
            "- If it is a scanned PDF, run OCR first: ocrmypdf input.pdf output.pdf",
            400,
        )

    summarizer = load_available_summarizer()
    category, confidence, _scores = resolve_category(text, category_override)

    start = time.time()
    working_text = text

    if word_count > MAX_INPUT_WORDS:
        summarizer_fn = _make_summarizer_fn(summarizer, category)
        result_dict = extract_and_summarize(text, summarizer_fn)
        styled_summary = result_dict["summary"]
        chunks_used = result_dict["chunks_count"]
    else:
        words = text.split()
        if len(words) > INFERENCE_WORD_LIMIT:
            working_text = " ".join(words[:INFERENCE_WORD_LIMIT])
        kwargs = get_pipeline_kwargs(word_count, category)

        cache_key = _cache_key(
            working_text,
            category,
            kwargs.get("min_length", 0),
            kwargs.get("max_length", 0),
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            styled_summary = cached
        else:
            result = summarizer(working_text, truncation=True, **kwargs)
            styled_summary = apply_style(result[0]["summary_text"], working_text, category)
            _cache_set(cache_key, styled_summary)
        chunks_used = 1

    elapsed = round(time.time() - start, 2)
    summary_words = len(styled_summary.split())
    compression = round(summary_words / max(word_count, 1), 4)

    stats = {
        "confidence": float(round(confidence, 4)),
        "compression": float(round((1 - compression) * 100, 1)),
        "original_words": word_count,
        "summary_words": summary_words,
        "time_sec": elapsed,
    }
    create_summary(
        user_id=user_id,
        input_text=(working_text[:500] + "...") if not file_name else None,
        file_name=file_name,
        summary=styled_summary,
        category=category,
        stats=stats,
    )

    ui_meta = get_category_ui_meta(category)
    return {
        "summary": styled_summary,
        "domain_type": ui_meta["label"],
        "confidence_score": round(confidence, 4),
        "compression_ratio": compression,
        "keywords": [],
        "explanation": (
            f"Category: {ui_meta['label']} | {elapsed}s | "
            f"{word_count}->{summary_words} words | {chunks_used} chunk(s)"
        ),
    }


def extract_document_summary(*, uploaded_file, user_id: int, category_override: str | None = "other") -> dict:
    try:
        raw_text = read_file_object(uploaded_file)
    except ValueError as error:
        raise SummaryServiceError(str(error), 400) from error

    file_name = uploaded_file.filename
    if _is_spreadsheet(file_name) or is_structured_dataset_report(raw_text):
        response = _build_dataset_response(raw_text.strip(), user_id=user_id, file_name=file_name)
        response["chunks_count"] = 1
        response["extracted_words"] = len(raw_text.split())
        return response

    cleaned = clean_text(raw_text)
    word_count = len(cleaned.split())
    summarizer = load_available_summarizer()

    category, confidence, _scores = resolve_category(cleaned, category_override)
    summarizer_fn = _make_summarizer_fn(summarizer, category)

    start = time.time()
    try:
        result_dict = extract_and_summarize(cleaned, summarizer_fn, verbose=True)
    except Exception as error:
        raise SummaryServiceError(str(error), 500) from error

    elapsed = round(time.time() - start, 2)
    final_summary = result_dict["summary"]
    summary_words = result_dict["summary_words"]
    chunks_count = result_dict["chunks_count"]
    extracted_words = result_dict["extracted_words"]
    compression = round(summary_words / max(word_count, 1), 4)

    stats = {
        "confidence": float(round(confidence, 4)),
        "compression": float(round((1 - compression) * 100, 1)),
        "original_words": word_count,
        "extracted_words": extracted_words,
        "summary_words": summary_words,
        "chunks_count": chunks_count,
        "time_sec": elapsed,
    }
    create_summary(
        user_id=user_id,
        file_name=file_name,
        summary=final_summary,
        category=category,
        stats=stats,
    )

    ui_meta = get_category_ui_meta(category)
    return {
        "summary": final_summary,
        "domain_type": ui_meta["label"],
        "confidence_score": round(confidence, 4),
        "compression_ratio": compression,
        "keywords": [],
        "explanation": (
            f"Deep extract | {chunks_count} chunk(s) | "
            f"{elapsed}s | {word_count}->{summary_words} words"
        ),
        "chunks_count": chunks_count,
        "extracted_words": extracted_words,
    }
