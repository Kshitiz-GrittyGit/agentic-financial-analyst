"""
ingestion.py — Parse 10-K PDFs into hierarchical, breadcrumbed chunks for RAG.

Narrative only. Financial figures come from XBRL via helpers.get_financial_fact,
so table extraction is deliberately out of scope here.

Input:  documents/*.pdf   (produced by filing.py)
Output: output/*.json     (chunks + hierarchy, consumed by embedding.py)
"""

from collections import Counter
from datetime import datetime
from pathlib import Path
import json
import re
import traceback

import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter

BASE_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = BASE_DIR / "documents"
OUTPUT_DIR = BASE_DIR / "output"

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200

HEADING_L1_RATIO = 1.4
HEADING_L2_RATIO = 1.1


_PART_RE = re.compile(r"^PART\s+[IVX]+\b", re.IGNORECASE)
_ITEM_RE = re.compile(r"^Item\s+\d+[A-Z]?\s*[.\u2014-]", re.IGNORECASE)
_TOC_TAIL_RE = re.compile(r"\s\d{1,3}$")
# ---------------------------------------------------------------------------
# 1. Font-size analysis — body text is the most common size; larger is a heading
# ---------------------------------------------------------------------------

def compute_dominant_font_size(pdf, sample_pages=30):
    sizes = []
    for page in pdf.pages[:sample_pages]:
        for c in page.chars:
            if c.get("size"):
                sizes.append(round(c["size"], 1))
    if not sizes:
        return 12.0
    return Counter(sizes).most_common(1)[0][0]


def classify_heading(text, font_size, dominant_size, font_name=None):
    text = text.strip()
    if not text:
        return None

    # Junk filter: checkbox glyphs, digit runs, no letters, single fragments
    if not any(ch.isalpha() for ch in text):
        return None
    if len(text) < 4:
        return None
    digits = sum(ch.isdigit() for ch in text)
    if digits > len(text) * 0.4:
        return None

    # Level 1: the document's real structure
    if _PART_RE.match(text) or _ITEM_RE.match(text):
        if _TOC_TAIL_RE.search(text):      # table-of-contents line, not the section
            return None
        return 1

    if text.endswith((".", ",", ";")):
        return None
    if len(text.split()) > 15:
        return None

    # Level 2: statement titles and sub-headings, uppercase or visually larger
    if text.isupper() and 2 <= len(text.split()) <= 10:
        return 2

    ratio = font_size / dominant_size if dominant_size > 0 and font_size > 0 else 1.0
    is_bold = font_name and "bold" in font_name.lower()
    if ratio >= HEADING_L2_RATIO and 2 <= len(text.split()) <= 12:
        return 2
    if is_bold and 2 <= len(text.split()) <= 10:
        return 2

    return None


def group_chars_to_lines(chars, page_num):
    """Group pdfplumber chars into text lines by y-proximity (~3pt buckets)."""
    if not chars:
        return []

    sorted_chars = sorted(chars, key=lambda c: (round(c["top"] / 3) * 3, c["x0"]))
    lines = []
    current_row = [sorted_chars[0]]
    current_y = round(sorted_chars[0]["top"] / 3) * 3

    for c in sorted_chars[1:]:
        y = round(c["top"] / 3) * 3
        if y == current_y:
            current_row.append(c)
        else:
            _emit_line(current_row, page_num, lines)
            current_row = [c]
            current_y = y

    _emit_line(current_row, page_num, lines)
    return lines


def _emit_line(row_chars, page_num, lines):
    text = " ".join("".join(ch["text"] for ch in row_chars).split())
    if not text:
        return
    sizes = [ch["size"] for ch in row_chars if ch.get("size")]
    fonts = [ch.get("fontname", "") for ch in row_chars if ch.get("fontname")]
    lines.append({
        "text": text,
        "font_size": sum(sizes) / len(sizes) if sizes else 0,
        "font_name": Counter(fonts).most_common(1)[0][0] if fonts else None,
        "top": row_chars[0]["top"],
        "page": page_num,
    })


def merge_lines_to_paragraphs(lines):
    """
    Merge consecutive body lines into sentences/paragraphs.
    Headings stay standalone. Body lines not ending in sentence punctuation are
    PDF line-break continuations and get joined to the next line.
    """
    merged = []
    buffer = []

    def flush():
        if buffer:
            merged.append({
                "text": " ".join(b["text"] for b in buffer),
                "page": buffer[0]["page"],
                "heading_level": None,
            })
            buffer.clear()

    for line in lines:
        if line.get("heading_level") is not None:
            flush()
            merged.append(line)
        else:
            buffer.append(line)
            if line["text"].rstrip().endswith((".", "?", "!", ":", ";")):
                flush()

    flush()
    return merged


# ---------------------------------------------------------------------------
# 2. Boilerplate — repeated header/footer text across pages
# ---------------------------------------------------------------------------

def detect_boilerplate(pdf, top_pt=50, bot_pt=50, min_repeats=3):
    top_texts, bot_texts = [], []

    for page in pdf.pages:
        chars = page.chars
        if not chars:
            top_texts.append("")
            bot_texts.append("")
            continue

        top_chars = sorted(
            [c for c in chars if c["top"] < top_pt],
            key=lambda c: (c["top"], c["x0"]),
        )
        top_texts.append("".join(c["text"] for c in top_chars).strip())

        bot_chars = sorted(
            [c for c in chars if c["top"] > page.height - bot_pt],
            key=lambda c: (c["top"], c["x0"]),
        )
        bot_texts.append("".join(c["text"] for c in bot_chars).strip())

    boilerplate = set()
    for texts in (top_texts, bot_texts):
        for text, count in Counter(texts).items():
            if count >= min_repeats and text:
                boilerplate.add(text)
    return boilerplate


# ---------------------------------------------------------------------------
# 3. Hierarchical document tree
# ---------------------------------------------------------------------------

def build_hierarchical_json(paragraphs):
    root = {"title": "ROOT", "level": 0, "page": None, "content": [], "children": []}
    stack = [root]

    for para in paragraphs:
        text = para["text"]
        page = para["page"]
        level = para.get("heading_level")

        if not text:
            continue

        if level is not None:
            section = {"title": text, "level": level, "page": page,
                       "content": [], "children": []}
            while len(stack) > 1 and stack[-1]["level"] >= level:
                stack.pop()
            stack[-1]["children"].append(section)
            stack.append(section)
        else:
            is_list = (re.match(r"^[*\u2022\-\u2013\u2014]\s", text)
                       or re.match(r"^\(\w+\)\s", text))
            stack[-1]["content"].append({
                "text": text,
                "type": "ListItem" if is_list else "NarrativeText",
                "page": page,
            })

    return root


# ---------------------------------------------------------------------------
# 4. Chunking — breadcrumb carries the section path
# ---------------------------------------------------------------------------


SENTENCE_END = {".", "?", "!", ":"}

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
    length_function=len,
)


def _join_content_items(items):
    if not items:
        return ""
    parts = [items[0]["text"].strip()]
    for i in range(1, len(items)):
        prev = items[i - 1]["text"].strip()
        curr = items[i]["text"].strip()
        if prev and prev[-1] in SENTENCE_END:
            parts.append(curr)
        else:
            parts[-1] = parts[-1] + " " + curr
    return "\n\n".join(parts)


def chunk_section(section, parent_breadcrumb=""):
    breadcrumb = f"{parent_breadcrumb} > {section['title']}".strip(" >")
    chunks = []

    if section["content"]:
        full_text = _join_content_items(section["content"])
        page = section["content"][0]["page"]
        for piece in splitter.split_text(full_text):
            if piece.strip():
                chunks.append({
                    "breadcrumb": breadcrumb,
                    "page": page,
                    "text": piece.strip(),
                })

    for child in section.get("children", []):
        chunks.extend(chunk_section(child, parent_breadcrumb=breadcrumb))

    return chunks


# ---------------------------------------------------------------------------
# 5. Per-document processing
# ---------------------------------------------------------------------------

def process_pdf(pdf_path):
    """Parse one PDF into chunks. Returns (name, chunk_count, status, error)."""
    pdf_path = Path(pdf_path)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            dominant_size = compute_dominant_font_size(pdf)
            boilerplate = detect_boilerplate(pdf)

            all_lines = []
            for page_num, page in enumerate(pdf.pages, start=1):
                lines = group_chars_to_lines(page.chars, page_num)
                for line in lines:
                    if line["text"] in boilerplate:
                        continue
                    line["heading_level"] = classify_heading(
                        line["text"], line["font_size"],
                        dominant_size, line.get("font_name"),
                    )
                    all_lines.append(line)

        paragraphs = merge_lines_to_paragraphs(all_lines)
        doc_json = build_hierarchical_json(paragraphs)
        chunks = chunk_section(doc_json)

        output = {
            "metadata": {
                "source": pdf_path.name,
                "dominant_font_size": dominant_size,
                "boilerplate_count": len(boilerplate),
                "rag_chunks": len(chunks),
            },
            "hierarchy": doc_json,
            "chunks": chunks,
        }

        with open(OUTPUT_DIR / (pdf_path.stem + ".json"), "w") as f:
            json.dump(output, f, indent=2)

        return pdf_path.name, len(chunks), "ok", ""

    except Exception:
        return pdf_path.name, 0, "error", traceback.format_exc()


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    pdf_files = sorted(DOCUMENTS_DIR.glob("*.pdf"))
    print(f"Found {len(pdf_files)} PDFs")

    manifest = []
    ok_count = err_count = 0

    for pdf_path in pdf_files:
        name, chunks, status, error = process_pdf(pdf_path)
        manifest.append({
            "source": name,
            "chunks": chunks,
            "status": status,
            "error": error or None,
            "timestamp": datetime.now().isoformat(),
        })
        if status == "ok":
            ok_count += 1
            print(f"  OK  {name}: {chunks} chunks")
        else:
            err_count += 1
            print(f"  ERR {name}: {error.strip().splitlines()[-1]}")

    with open(OUTPUT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nDone. {ok_count} succeeded, {err_count} failed.")


if __name__ == "__main__":
    main()