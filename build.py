#!/usr/bin/env python3
"""
build.py - Render the static site from data/publications.yaml

Usage:
    python build.py

Outputs:
    index.html
    publications.html
    projects/<id>/index.html   (for each entry with project: true)

Run this after editing publications.yaml by hand, or after generate.py
has appended a new entry.
"""

import datetime
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "publications.yaml"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_ROOT = ROOT

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]+)"
)


def authors_joined(authors):
    return ", ".join(authors)


def authors_short(authors):
    """First author + et al. for carousel captions."""
    if len(authors) <= 2:
        return " and ".join(authors)
    return f"{authors[0]} et al."


def video_embed_html(video_url):
    """Return an <iframe> or <video> snippet for a project page, or None."""
    if not video_url:
        return None

    yt_match = YOUTUBE_RE.search(video_url)
    if yt_match:
        vid_id = yt_match.group(1)
        return (
            f'<iframe src="https://www.youtube.com/embed/{vid_id}" '
            f'title="Project video" allowfullscreen></iframe>'
        )

    if video_url.lower().endswith(".mp4"):
        return f'<video controls src="{video_url}"></video>'

    # Unknown / external project-style video link (e.g. a hosted page) -
    # don't try to embed, let the project page fall back to the thumbnail.
    return None


def load_publications():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []

    pubs = []
    for entry in raw:
        entry.setdefault("links", {})
        entry.setdefault("project", False)
        entry.setdefault("highlight", False)
        entry["authors_joined"] = authors_joined(entry["authors"])
        entry["authors_short"] = authors_short(entry["authors"])
        entry["video_embed"] = video_embed_html(entry["links"].get("video"))
        pubs.append(entry)

    # Sort newest first
    pubs.sort(key=lambda p: p["year"], reverse=True)
    return pubs


def group_by_year(pubs):
    grouped = {}
    for pub in pubs:
        grouped.setdefault(pub["year"], []).append(pub)
    # Return list of (year, pubs) sorted descending by year
    return sorted(grouped.items(), key=lambda kv: kv[0], reverse=True)


def build():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    pubs = load_publications()
    current_year = datetime.datetime.now().year

    # ---- index.html ----
    highlights = [p for p in pubs if p.get("highlight") and p["links"].get("video") or p.get("highlight")]
    highlights = [p for p in pubs if p.get("highlight")]
    recent = pubs[:8]

    index_tpl = env.get_template("index_template.html")
    index_html = index_tpl.render(
        root="",
        highlights=highlights,
        recent=recent,
        year=current_year,
    )
    (OUTPUT_ROOT / "index.html").write_text(index_html, encoding="utf-8")
    print("Wrote index.html")

    # ---- publications.html ----
    pubs_by_year = group_by_year(pubs)
    pubs_tpl = env.get_template("publications_template.html")
    pubs_html = pubs_tpl.render(
        root="",
        pubs_by_year=pubs_by_year,
        year=current_year,
    )
    (OUTPUT_ROOT / "publications.html").write_text(pubs_html, encoding="utf-8")
    print("Wrote publications.html")

    # ---- project pages ----
    project_tpl = env.get_template("project_template.html")
    for pub in pubs:
        if not pub.get("project"):
            continue
        out_dir = OUTPUT_ROOT / "projects" / pub["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        page_html = project_tpl.render(pub=pub, year=current_year)
        (out_dir / "index.html").write_text(page_html, encoding="utf-8")
        print(f"Wrote projects/{pub['id']}/index.html")

    print(f"\nDone. {len(pubs)} publications, "
          f"{sum(1 for p in pubs if p.get('project'))} project pages, "
          f"{len(highlights)} carousel highlights.")


if __name__ == "__main__":
    try:
        build()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Make sure {DATA_FILE} exists.", file=sys.stderr)
        sys.exit(1)
