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


def extract_thumbnail(pdf_path, out_path):
    """Save the first embedded image on page 1 as the thumbnail.

    Returns the actual path written (with correct extension), or the
    rendered-page fallback path.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    images = page.get_images(full=True)

    if not images:
        # fall back: render the first page itself
        out_path_with_ext = out_path.with_suffix(".png")
        pix = page.get_pixmap(dpi=150)
        pix.save(str(out_path_with_ext))
        doc.close()
        return out_path_with_ext

    xref = images[0][0]
    base_image = doc.extract_image(xref)
    img_bytes = base_image["image"]
    ext = base_image["ext"]
    out_path_with_ext = out_path.with_suffix(f".{ext}")
    out_path_with_ext.write_bytes(img_bytes)
    doc.close()
    return out_path_with_ext


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
