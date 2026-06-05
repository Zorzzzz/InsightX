"""
text_extractor.py
=================
Smart text extraction + chunk-based summarization.

Improvements:
  - SMART CSV PARSER → treats CSV as a table, not raw text
  - Garbage-text detector → bypass smart extractor for noisy inputs
  - Multi-engine PDF reader with quality check
  - Parallel chunk processing
"""

import re
import io
import csv
import argparse
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHUNK_SIZE     = 700
CHUNK_OVERLAP  = 80
MAX_WORKERS    = 4
CLI_APP_URL    = "http://127.0.0.1:5000/api/summarize"


# ===========================================================================
# 0. GARBAGE DETECTOR
# ===========================================================================

def _looks_like_garbage(text: str) -> bool:
    """Detect broken OCR / image-PDF / structured-data-as-text / table-heavy PDFs."""
    if not text or not text.strip():
        return True

    words = text.split()
    if len(words) < 30:
        return True

    short = sum(1 for w in words if len(w) <= 2)
    if short / len(words) > 0.4:
        return True

    alpha = sum(1 for c in text if c.isalpha())
    if alpha / max(len(text), 1) < 0.55:
        return True

    # NEW: too many numeric tokens = table/bibliography-heavy PDF
    numeric_tokens = sum(
        1 for w in words
        if re.match(r'^[\d.,\-–()%/]+$', w)
    )
    if numeric_tokens / len(words) > 0.25:
        return True

    return False


def _strip_pdf_noise(text: str) -> str:
    """Remove patterns that come from PDF tables, footers, citations, etc.

    These confuse BART because they look like sentences but have no semantics.
    """
    # Numbered list runs: "1. 2. 3. 4. 5." (page numbers, table cells)
    text = re.sub(r'(?:\b\d+[.\)]\s+){4,}', ' ', text)
    # Decimal sequences: "2.1 - 2.2 - 1.2 - 1. 3."
    text = re.sub(r'(?:\d+(?:\.\d+)?\s*[-–]\s*){3,}\d+(?:\.\d+)?', ' ', text)
    # Long runs of bare numbers separated by spaces or commas
    text = re.sub(r'(?:\b\d+(?:\.\d+)?\s*[,\s]\s*){4,}\d+(?:\.\d+)?', ' ', text)
    # Citation markers: "(2024)", "[12]", "(p. 45)"
    text = re.sub(r'\(\s*\d{4}\s*\)', ' ', text)
    text = re.sub(r'\[\s*\d+\s*(?:[-,]\s*\d+\s*)*\]', ' ', text)
    text = re.sub(r'\(\s*p\.?\s*\d+\s*\)', ' ', text, flags=re.IGNORECASE)
    # Page-number-like fragments "Page 12 of 45"
    text = re.sub(r'\bpage\s+\d+(?:\s+of\s+\d+)?\b', ' ', text, flags=re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ===========================================================================
# 1. CSV PARSER  (smart — turns rows into natural sentences)
# ===========================================================================

def _detect_csv_dialect(sample: str):
    """Try to detect the delimiter; fallback to comma."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class _D:
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\r\n"
            quoting = csv.QUOTE_MINIMAL
        return _D()


def _is_text_column(values: list[str]) -> bool:
    """A column is 'text' if its values are mostly multi-word strings."""
    non_empty = [v for v in values if v and v.strip()]
    if len(non_empty) < 2:
        return False

    # Skip ID-like columns: all numeric, or all very short
    avg_len = sum(len(v) for v in non_empty) / len(non_empty)
    if avg_len < 5:                        # was 8 — more lenient
        return False

    # Skip mostly-numeric columns
    numeric = sum(1 for v in non_empty if v.replace(".", "").replace("-", "").replace(",", "").isdigit())
    if numeric / len(non_empty) > 0.6:    # was 0.5
        return False

    # Has at least some multi-word values OR is generally long text
    multi_word = sum(1 for v in non_empty if len(v.split()) >= 2)   # was >=3
    if multi_word / len(non_empty) < 0.2:                            # was 0.3
        # Last chance: maybe single long words (titles, names)?
        if avg_len < 15:
            return False

    return True


def parse_csv_to_text(raw: str) -> str:
    """Turn a CSV string into clean prose suitable for summarization.

    Strategy:
      1. Parse as CSV with auto-detected delimiter
      2. Identify which columns contain real text (vs IDs, numbers, ratings)
      3. Concatenate text columns row-by-row into sentences
      4. Drop duplicate rows
    """
    # Decode if bytes were passed
    if not raw or not raw.strip():
        return raw

    sample = raw[:4096]
    dialect = _detect_csv_dialect(sample)

    try:
        reader = list(csv.reader(io.StringIO(raw), dialect=dialect))
    except Exception:
        # Fallback to comma
        reader = list(csv.reader(io.StringIO(raw)))

    if len(reader) < 2:
        return raw  # not a real CSV

    header = reader[0]
    rows   = reader[1:]

    # Check that this actually looks like a CSV (header row has multiple fields)
    if len(header) < 2:
        return raw

    # For each column, collect values
    col_values: list[list[str]] = [[] for _ in header]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_values):
                col_values[i].append((cell or "").strip())

    # Find text columns
    text_col_indices = [i for i, vals in enumerate(col_values) if _is_text_column(vals)]

    # If no obvious text column, take the longest-average column
    if not text_col_indices:
        if not col_values or not any(col_values):
            return raw
        avg_lengths = [
            (i, sum(len(v) for v in vals) / max(len(vals), 1))
            for i, vals in enumerate(col_values)
        ]
        avg_lengths.sort(key=lambda x: x[1], reverse=True)
        text_col_indices = [avg_lengths[0][0]] if avg_lengths[0][1] > 5 else []

    if not text_col_indices:
        return raw  # CSV is purely numeric — nothing to summarize

    # Build sentences from text columns, dedupe rows
    seen: set[str] = set()
    sentences: list[str] = []

    for row in rows:
        parts = []
        for i in text_col_indices:
            if i < len(row):
                cell = (row[i] or "").strip()
                if cell and len(cell) > 3:
                    # Remove trailing punctuation noise, keep one period
                    cell = re.sub(r'[.!?]+$', '', cell)
                    parts.append(cell)

        if not parts:
            continue

        sentence = ". ".join(parts).strip()
        # Normalize for dedup (lowercase, collapse spaces)
        norm = re.sub(r"\s+", " ", sentence.lower())
        if norm in seen:
            continue
        seen.add(norm)

        if not sentence.endswith((".", "!", "?")):
            sentence += "."
        sentences.append(sentence)

    if not sentences:
        return raw

    result = " ".join(sentences)

    # Fallback: if the parsed result is too short, include MORE columns
    # (some CSVs have many short text fields that individually look non-text)
    if len(result.split()) < 40:
        # Take ALL non-numeric columns this time
        all_text_cols = []
        for i, vals in enumerate(col_values):
            non_empty = [v for v in vals if v and v.strip()]
            if not non_empty:
                continue
            numeric = sum(1 for v in non_empty if v.replace(".", "").replace("-", "").replace(",", "").isdigit())
            if numeric / len(non_empty) <= 0.6:
                all_text_cols.append(i)

        if all_text_cols and set(all_text_cols) != set(text_col_indices):
            seen2: set[str] = set()
            broader: list[str] = []
            for row in rows:
                parts = [
                    (row[i] or "").strip()
                    for i in all_text_cols
                    if i < len(row) and (row[i] or "").strip()
                ]
                if not parts:
                    continue
                sentence = ". ".join(parts)
                norm = re.sub(r"\s+", " ", sentence.lower())
                if norm in seen2:
                    continue
                seen2.add(norm)
                if not sentence.endswith((".", "!", "?")):
                    sentence += "."
                broader.append(sentence)
            broader_result = " ".join(broader)
            if len(broader_result.split()) > len(result.split()):
                result = broader_result

    # Final fallback: if still too short, return the raw CSV text
    # (the user will at least get an error explaining the file is too small,
    # rather than silently producing 5-word output)
    if len(result.split()) < 20:        # was 30
        return raw

    return result


# ===========================================================================
# 2. PDF READERS
# ===========================================================================

def _read_with_pdfplumber(data: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        parts = [p.extract_text() or "" for p in pdf.pages]
    return "\n".join(parts).strip()


def _read_with_pypdf2(data: bytes) -> str:
    import PyPDF2
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    return " ".join((page.extract_text() or "") for page in reader.pages).strip()


def _read_with_pdfminer(data: bytes) -> str:
    from pdfminer.high_level import extract_text
    return (extract_text(io.BytesIO(data)) or "").strip()


def read_pdf_bytes(data: bytes) -> str:
    engines = [
        ("pdfplumber", _read_with_pdfplumber),
        ("PyPDF2",     _read_with_pypdf2),
        ("pdfminer",   _read_with_pdfminer),
    ]

    best_text  = ""
    best_score = -1

    for name, fn in engines:
        try:
            text = fn(data)
        except ImportError:
            continue
        except Exception as e:
            print(f"   ⚠️  {name} failed: {e}")
            continue

        if not text:
            continue

        score = len(text.split()) // 4 if _looks_like_garbage(text) else len(text.split())
        if score > best_score:
            best_score = score
            best_text  = text
            if not _looks_like_garbage(text) and score >= 100:
                return text

    if not best_text:
        raise ValueError(
            "Could not extract text from PDF. "
            "Install: pip install pdfplumber PyPDF2 pdfminer.six"
        )

    if _looks_like_garbage(best_text):
        print("   ⚠️  PDF text looks broken (likely scanned/image PDF). "
              "Try OCR first: ocrmypdf input.pdf output.pdf")

    return best_text


def read_pdf_path(path: str) -> str:
    with open(path, "rb") as f:
        return read_pdf_bytes(f.read())


def read_docx_bytes(data: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
        return " ".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        raise ValueError("Install python-docx: pip install python-docx")




def read_xlsx_bytes(data: bytes) -> str:
    try:
        from core.csv_analyzer import analyze_workbook_bytes
        return analyze_workbook_bytes(data)
    except ImportError:
        raise ValueError("Install openpyxl: pip install openpyxl")

def read_file_object(file_obj) -> str:
    filename = file_obj.filename.lower()
    data     = file_obj.read()

    if filename.endswith(".pdf"):
        return read_pdf_bytes(data)
    if filename.endswith(".docx"):
        return read_docx_bytes(data)
    if filename.endswith(".xlsx"):
        return read_xlsx_bytes(data)
    if filename.endswith(".csv"):
        raw = data.decode("utf-8", errors="replace")
        try:
            from core.csv_analyzer import analyze_csv
            return analyze_csv(raw)
        except ImportError:
            return parse_csv_to_text(raw)
    if filename.endswith(".txt"):
        return data.decode("utf-8", errors="replace")

    raise ValueError(f"Unsupported file type: {file_obj.filename}")


# ===========================================================================
# 3. CLEANER
# ===========================================================================

def clean_text(text: str) -> str:
    text = re.sub(r"(?<![.\n])\n(?!\n)", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"\s*\(\s*\)\s*", " ", text)
    text = re.sub(r"\s*\[\s*\]\s*", " ", text)
    text = re.sub(r"([.,!?;:])\1{2,}", r"\1", text)
    # Strip table/citation/page-number noise common in academic PDFs
    text = _strip_pdf_noise(text)
    return text.strip()


# ===========================================================================
# 4. SMART EXTRACTOR
# ===========================================================================

def extract_important(text: str) -> str:
    if _looks_like_garbage(text):
        return text

    sentences = re.split(r'(?<=[.!?])\s+', text)

    HAS_NUMBER  = re.compile(
        r'\b\d[\d,\.]*\s*(%|percent|billion|million|thousand|k\b)?', re.I)
    HAS_DATE    = re.compile(
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|'
        r'january|february|march|april|may|june|july|august|'
        r'september|october|november|december|\d{4})\b', re.I)
    IS_BULLET   = re.compile(r'^\s*[-•*▪◦]\s+.+')
    IS_NUMBERED = re.compile(r'^\s*\d+[.)]\s+.+')
    IS_HEADING  = re.compile(r'^[A-Z][A-Za-z0-9 ,:\-]{3,60}$')

    important = []
    seen      = set()

    for line in text.splitlines():
        line = line.strip()
        if not line or line in seen:
            continue
        if IS_BULLET.match(line) or IS_NUMBERED.match(line) or IS_HEADING.match(line):
            important.append(line)
            seen.add(line)

    for sent in sentences:
        sent = sent.strip()
        if not sent or sent in seen:
            continue
        if HAS_NUMBER.search(sent) or HAS_DATE.search(sent):
            important.append(sent)
            seen.add(sent)

    extracted = " ".join(important) if important else text

    if len(extracted.split()) < max(50, len(text.split()) // 20):
        return text

    return extracted


# ===========================================================================
# 5. CHUNKER
# ===========================================================================

def split_into_chunks(text: str,
                      chunk_size: int = CHUNK_SIZE,
                      overlap: int    = CHUNK_OVERLAP) -> list[str]:
    words  = text.split()
    chunks = []
    start  = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap

    return chunks


# ===========================================================================
# 6. LIBRARY API
# ===========================================================================

def _summarize_chunks_parallel(chunks: list[str], summarizer_fn) -> list[str]:
    if len(chunks) == 1:
        return [summarizer_fn(chunks[0])]
    workers = min(MAX_WORKERS, len(chunks))
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(summarizer_fn, chunks))
    except Exception:
        return [summarizer_fn(c) for c in chunks]


def extract_and_summarize(text: str, summarizer_fn, verbose: bool = False) -> dict:
    total_words = len(text.split())

    if verbose:
        print(f"🔍  Extracting key sentences from {total_words} words …")
    extracted = extract_important(text)
    ext_words = len(extracted.split())

    base_text = extracted
    if len(extracted.split()) < len(text.split()) * 0.6:
        base_text = text
    chunks = split_into_chunks(base_text)
    if verbose:
        print(f"✂️   Split into {len(chunks)} chunk(s) of ~{CHUNK_SIZE} words")

    if verbose and len(chunks) > 1:
        print(f"⚡  Summarizing {len(chunks)} chunks in parallel …")
    chunk_summaries = _summarize_chunks_parallel(chunks, summarizer_fn)

    combined = " ".join(chunk_summaries)
    combined_words = len(combined.split())

    # Detailed mode for large PDFs/documents:
    # keep merged chunk summaries instead of aggressively re-summarizing them.
    if len(chunks) > 10:
        final = combined
    elif len(chunks) > 1 and combined_words > 250:
        if verbose:
            print(f"🔁  Merging chunk summaries ({combined_words} words) …")
        combined_chunks = split_into_chunks(combined)
        if len(combined_chunks) > 1:
            final_parts = _summarize_chunks_parallel(combined_chunks, summarizer_fn)
            final = " ".join(final_parts)
        else:
            final = summarizer_fn(combined)
    else:
        final = combined if len(chunks) > 1 else chunk_summaries[0]

    # Generate key points from the final summary
    try:
        sentences = re.split(r'(?<=[.!?])\s+', final)
        key_points = [s.strip() for s in sentences if len(s.split()) > 8][:10]
        if key_points:
            final = "KEY POINTS:\n" + "\n".join(
                f"• {p}" for p in key_points
            ) + "\n\nDETAILED SUMMARY:\n" + final
    except Exception:
        pass

    return {
        "summary":         final,
        "chunks_count":    len(chunks),
        "original_words":  total_words,
        "extracted_words": ext_words,
        "summary_words":   len(final.split()),
    }


# ===========================================================================
# 7. CLI
# ===========================================================================

def _cli_summarize_chunk(text: str, chunk_num: int, total: int) -> str:
    import requests
    print(f"   ⏳ chunk {chunk_num}/{total}…", end="\r")
    try:
        resp = requests.post(CLI_APP_URL, json={"text": text}, timeout=120)
        data = resp.json()
        if "error" in data:
            return text
        return data.get("summary", text)
    except Exception:
        return text


def _cli_run(text: str, no_summary: bool = False) -> None:
    extracted = extract_important(text)
    if no_summary:
        print(" ".join(extracted.split()[:800]))
        return
    base_text = extracted
    if len(extracted.split()) < len(text.split()) * 0.6:
        base_text = text
    chunks = split_into_chunks(base_text)
    chunk_summaries = [_cli_summarize_chunk(c, i, len(chunks)) for i, c in enumerate(chunks, 1)]
    combined = " ".join(chunk_summaries)
    if len(chunks) > 1 and len(combined.split()) > 250:
        final_chunks = split_into_chunks(combined)
        if len(final_chunks) > 1:
            final = " ".join(_cli_summarize_chunk(c, i, len(final_chunks)) for i, c in enumerate(final_chunks, 1))
        else:
            final = _cli_summarize_chunk(combined, 1, 1)
    else:
        final = combined if len(chunks) > 1 else chunk_summaries[0]
    print("\n" + "=" * 60)
    print("📝  FINAL SUMMARY")
    print("=" * 60)
    print(final)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--text")
    p.add_argument("--no-summary", action="store_true")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    if args.file:
        fl = args.file.lower()
        if fl.endswith(".pdf"):
            raw = read_pdf_path(args.file)
        elif fl.endswith(".csv"):
            with open(args.file, encoding="utf-8", errors="ignore") as f:
                try:
                    from core.csv_analyzer import analyze_csv
                    raw = analyze_csv(f.read())
                except ImportError:
                    raw = parse_csv_to_text(f.read())
        elif fl.endswith(".xlsx"):
            with open(args.file, "rb") as f:
                raw = read_xlsx_bytes(f.read())
        else:
            with open(args.file, encoding="utf-8", errors="ignore") as f:
                raw = f.read()
    else:
        raw = args.text

    cleaned = clean_text(raw)
    _cli_run(cleaned, no_summary=args.no_summary)


if __name__ == "__main__":
    main()

# Excel summary formatting enhancement
EXCEL_SUMMARY_INSTRUCTIONS = '''
For Excel spreadsheets:
- Create a structured report.
- Use headings and subheadings.
- Use bullet points and numbered lists.
- Show important statistics separately.
- Identify trends and key findings.
- Never return one large paragraph.
'''
