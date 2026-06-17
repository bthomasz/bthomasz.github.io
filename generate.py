#!/usr/bin/env python3
"""
generate.py - Add a new publication from a PDF (+ optional video) to publications.yaml,
extract a thumbnail, optionally create a project page, and rebuild the site.

Usage:
    python generate.py path/to/paper.pdf \
        --video "https://youtu.be/XXXXXXXX" \
        --venue "Proc. ACM SIGGRAPH 2026" \
        --year 2026 \
        --project

    python generate.py path/to/paper.pdf --video local_video.mp4 --project

    python generate.py path/to/paper.pdf --teaser teaser.jpg --project

Workflow:
    1. Extracts title and abstract from the PDF's first page
       (best-effort heuristic - REVIEW THE RESULT, edit publications.yaml if needed).
    2. Generates a BibTeX stub.
    3. Extracts the first embedded image as a thumbnail (saved to
       assets/images/publications/<id>.png).
    4. If --video points to a local file, copies it to assets/video/.
    5. Appends a new entry to data/publications.yaml.
    6. Runs build.py to regenerate index.html, publications.html, and
       (if --project) the new project page.

Requires:
    pip install pymupdf pyyaml jinja2
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image as _PILImage
import io as _io
import yaml

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "publications.yaml"
THUMB_DIR = ROOT / "assets" / "images" / "publications"
VIDEO_DIR = ROOT / "assets" / "video"
PDF_DIR = ROOT / "assets" / "pdf"


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    return text[:60]


def extract_metadata(pdf_path):
    """Best-effort extraction of title and abstract from page 1.

    This is a heuristic: it looks at the largest-font text on the first page
    for the title, and the abstract is found by searching for the word
    'Abstract'. ALWAYS REVIEW the result and edit publications.yaml if wrong.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]

    # --- Title: largest font span(s) on the first page ---
    blocks = page.get_text("dict")["blocks"]
    spans = []
    for block in blocks:
        for line in block.get("lines", []):
            for span in line["spans"]:
                spans.append(span)

    if spans:
        max_size = max(s["size"] for s in spans)
        title_spans = [s["text"] for s in spans if s["size"] >= max_size - 0.5]
        title = " ".join(title_spans).strip()
        title = re.sub(r"\s+", " ", title)
    else:
        title = "Untitled"

    # --- Abstract: text following the word "Abstract" ---
    full_text = page.get_text()
    abstract = ""
    match = re.search(r"abstract[\s:.-]*\n?(.*?)(?:\n\n|\n[0-9]\.?\s|Introduction)",
                       full_text, re.IGNORECASE | re.DOTALL)
    if match:
        abstract = re.sub(r"\s+", " ", match.group(1)).strip()

    doc.close()
    return {
        "title": title,
        "abstract": abstract,
    }


def _extract_largest_image(doc, pages):
    """Return (img_bytes, smask_bytes) for the largest embedded image across the given page indices."""
    best = None  # (area, img_bytes, smask_xref)
    for page_num in pages:
        for img in doc[page_num].get_images(full=True):
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue
            area = base_image.get("width", 0) * base_image.get("height", 0)
            if area < 10_000:
                continue
            if best is None or area > best[0]:
                best = (area, base_image["image"], base_image.get("smask", 0))
    if best is None:
        return None, None
    _, img_bytes, smask_xref = best
    smask_bytes = None
    if smask_xref:
        try:
            smask_bytes = doc.extract_image(smask_xref)["image"]
        except Exception:
            pass
    return img_bytes, smask_bytes


def extract_thumbnail(pdf_path, out_path):
    """Largest embedded image across pages 1-4, composited onto white. Falls back to rendering page 1."""
    doc = fitz.open(pdf_path)
    n_pages = min(4, len(doc))
    img_bytes, smask_bytes = _extract_largest_image(doc, range(n_pages))
    if img_bytes is None:
        pix = doc[0].get_pixmap(dpi=150)
        doc.close()
        actual = out_path.with_suffix(".jpg")
        _save_resized(pix.tobytes("png"), actual)
        return actual
    doc.close()
    actual = out_path.with_suffix(".jpg")
    _save_resized(img_bytes, actual, smask_bytes=smask_bytes)
    return actual


def extract_teaser(pdf_path, out_path):
    """Crop the teaser figure from page 1: the band between author names and the figure caption / abstract.

    Strategy:
    - header_end: bottom of the last WIDE text block (>40% of page width) in the top 40% of the page.
      Using width filters out narrow figure-label text ("Fig. 1A", "Linear", etc.) that sits inside
      the teaser region but is part of the figure, not the header.
    - teaser_bottom: top of the first wide text block below header_end that looks like a figure
      caption ("Fig. 1", "Figure 1") or an abstract heading ("Abstract"). Falls back to 55% page height.
    - If the resulting crop is thinner than 10% of the page (no teaser on page 1, e.g. CGF/arXiv
      two-column format), falls back to rendering the middle 20%–75% band of the page.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    page_h = page.rect.height
    page_w = page.rect.width

    blocks = page.get_text("blocks")
    text_blocks = [(b[0], b[1], b[2], b[3], b[4]) for b in blocks if b[6] == 0]

    MIN_WIDE = page_w * 0.40   # block must span at least 40% of page width

    # --- header_end: bottom of the last wide non-caption block in the top 40% of page ---
    # Exclude figure captions ("Fig. 1", "Figure 1") which are wide but belong to the teaser.
    header_end_y = 0
    for x0, y0, x1, y1, text in text_blocks:
        if y0 < page_h * 0.40 and (x1 - x0) >= MIN_WIDE:
            if not re.match(r'^Fig(ure)?\.?\s*\d', text[:30].strip(), re.IGNORECASE):
                header_end_y = max(header_end_y, y1)
    if header_end_y == 0:
        header_end_y = page_h * 0.25   # nothing wide found (unusual)

    # --- teaser_bottom: first wide block below header that is a caption or abstract heading ---
    teaser_bottom_y = None
    for x0, y0, x1, y1, text in sorted(text_blocks, key=lambda b: b[1]):
        if y0 <= header_end_y:
            continue
        if (x1 - x0) < MIN_WIDE:
            continue
        snippet = text[:50].strip()
        if re.match(r'^Fig(ure)?\.?\s*\d', snippet, re.IGNORECASE) or \
           re.match(r'^Abstract', snippet, re.IGNORECASE):
            teaser_bottom_y = y0
            break
    # Fallback: first wide block below header regardless of content
    if teaser_bottom_y is None:
        for x0, y0, x1, y1, _ in sorted(text_blocks, key=lambda b: b[1]):
            if y0 > header_end_y and (x1 - x0) >= MIN_WIDE:
                teaser_bottom_y = y0
                break
    if teaser_bottom_y is None:
        teaser_bottom_y = page_h * 0.55

    teaser_height = teaser_bottom_y - header_end_y

    if teaser_height < page_h * 0.10:
        # No dedicated teaser slot on page 1 — render middle band
        clip = fitz.Rect(0, page_h * 0.20, page_w, page_h * 0.75)
    else:
        margin = page_h * 0.005
        clip = fitz.Rect(0, header_end_y + margin, page_w, teaser_bottom_y - margin)

    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, colorspace=fitz.csRGB)
    doc.close()

    actual = out_path.with_suffix(".jpg")
    _save_resized(pix.tobytes("png"), actual, max_width=1200, quality=88)
    return actual


def _save_resized(img_bytes, out_path, max_width=800, quality=85, smask_bytes=None):
    """Resize to max_width and save as JPEG, compositing onto white if a soft mask is given."""
    img = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
    if smask_bytes:
        try:
            alpha = _PILImage.open(_io.BytesIO(smask_bytes)).convert("L")
            bg = _PILImage.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=alpha)
            img = bg
        except Exception:
            pass
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), _PILImage.LANCZOS)
    img.save(str(out_path), "JPEG", quality=quality, optimize=True)


def make_bibtex(entry):
    first_author_last = entry["authors"][0].split()[-1]
    cite_key = f"{first_author_last}{entry['year']}"
    authors_bib = " and ".join(entry["authors"])
    return (
        f"@article{{{cite_key},\n"
        f"  title   = {{{entry['title']}}},\n"
        f"  author  = {{{authors_bib}}},\n"
        f"  journal = {{{entry['venue']}}},\n"
        f"  year    = {{{entry['year']}}}\n"
        f"}}"
    )


def main():
    parser = argparse.ArgumentParser(description="Add a new publication and rebuild the site.")
    parser.add_argument("pdf", type=Path, help="Path to the paper PDF")
    parser.add_argument("--id", help="Slug/id for this entry (default: derived from title)")
    parser.add_argument("--title", help="Override extracted title")
    parser.add_argument("--authors", help="Comma-separated author list (overrides extraction)")
    parser.add_argument("--venue", required=True, help="Venue string, e.g. 'Proc. ACM SIGGRAPH 2026'")
    parser.add_argument("--year", type=int, required=True, help="Publication year")
    parser.add_argument("--video", help="Video URL (YouTube/Vimeo) or path to a local .mp4")
    parser.add_argument("--teaser", type=Path,
                         help="Path to a teaser/hero image for the project page "
                              "(used instead of the auto-extracted thumbnail)")
    parser.add_argument("--code", help="Code repository URL")
    parser.add_argument("--project", action="store_true",
                         help="Generate a standalone project page for this paper")
    parser.add_argument("--no-build", action="store_true",
                         help="Skip running build.py at the end")
    args = parser.parse_args()

    if not args.pdf.exists():
        sys.exit(f"PDF not found: {args.pdf}")

    print(f"Extracting metadata from {args.pdf} ...")
    meta = extract_metadata(args.pdf)

    title = args.title or meta["title"]
    abstract = meta["abstract"]

    if args.authors:
        authors = [a.strip() for a in args.authors.split(",")]
    else:
        authors = ["UNKNOWN AUTHOR - edit publications.yaml"]
        print("WARNING: no --authors given; author extraction from PDF is unreliable. "
              "Edit publications.yaml afterwards.")

    entry_id = args.id or slugify(title)

    print(f"  Title:   {title}")
    print(f"  Authors: {', '.join(authors)}")
    print(f"  Abstract: {abstract[:120]}{'...' if len(abstract) > 120 else ''}")
    print(f"  ID:      {entry_id}")

    # --- thumbnail ---
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb_base = THUMB_DIR / entry_id
    thumb_path = extract_thumbnail(args.pdf, thumb_base)
    thumb_rel = f"assets/images/publications/{thumb_path.name}"
    print(f"  Thumbnail: {thumb_rel}")

    # --- teaser image ---
    teaser_rel = None
    if args.teaser:
        if not args.teaser.exists():
            sys.exit(f"Teaser image not found: {args.teaser}")
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        teaser_dest = THUMB_DIR / f"{entry_id}-teaser{args.teaser.suffix.lower()}"
        shutil.copy(args.teaser, teaser_dest)
        teaser_rel = f"assets/images/publications/{teaser_dest.name}"
        print(f"  Teaser: {teaser_rel}")
    elif args.project:
        # Auto-extract page-1 teaser figure for project pages
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        teaser_path = extract_teaser(args.pdf, THUMB_DIR / f"{entry_id}-teaser")
        teaser_rel = f"assets/images/publications/{teaser_path.name}"
        print(f"  Teaser (auto from page 1): {teaser_rel}")

    # --- copy PDF into assets/pdf/ ---
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_dest = PDF_DIR / args.pdf.name
    shutil.copy(args.pdf, pdf_dest)
    pdf_rel = f"assets/pdf/{args.pdf.name}"

    # --- video ---
    video_link = None
    if args.video:
        video_path = Path(args.video)
        if video_path.exists() and video_path.suffix.lower() == ".mp4":
            VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            video_dest = VIDEO_DIR / video_path.name
            shutil.copy(video_path, video_dest)
            video_link = f"assets/video/{video_path.name}"
            print(f"  Video copied to: {video_link}")
        else:
            video_link = args.video  # assume URL
            print(f"  Video URL: {video_link}")

    # --- build entry ---
    entry = {
        "id": entry_id,
        "title": title,
        "authors": authors,
        "venue": args.venue,
        "year": args.year,
        "thumbnail": thumb_rel,
        "links": {"pdf": pdf_rel},
        "project": bool(args.project),
        "highlight": False,
    }
    if video_link:
        entry["links"]["video"] = video_link
    if teaser_rel:
        entry["teaser"] = teaser_rel
    if args.code:
        entry["links"]["code"] = args.code
    if abstract:
        entry["abstract"] = abstract
    entry["bibtex"] = make_bibtex(entry)

    # --- append to publications.yaml ---
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        pubs = yaml.safe_load(f) or []

    pubs.insert(0, entry)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        yaml.dump(pubs, f, sort_keys=False, allow_unicode=True, width=1000)

    print(f"\nAppended entry '{entry_id}' to {DATA_FILE}")
    print("Review the entry (especially authors/abstract/bibtex) before pushing.")

    # --- rebuild ---
    if not args.no_build:
        print("\nRunning build.py ...")
        subprocess.run([sys.executable, str(ROOT / "build.py")], check=True)


if __name__ == "__main__":
    main()
