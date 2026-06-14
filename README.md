# thomaszewski.github.io

Personal research website, statically generated from `data/publications.yaml`.

## Structure

```
.
├── index.html              <- generated, do not edit by hand
├── publications.html       <- generated, do not edit by hand
├── projects/<id>/index.html<- generated per-paper project pages
├── data/publications.yaml  <- SOURCE OF TRUTH for all publication data
├── templates/              <- Jinja2 HTML templates
├── assets/
│   ├── css/style.css
│   ├── js/carousel.js
│   ├── images/             <- profile photo, publication thumbnails, carousel images
│   ├── pdf/                <- locally-hosted PDFs
│   └── video/              <- locally-hosted videos (if not using YouTube links)
├── build.py                 <- renders all pages from publications.yaml
├── generate.py              <- adds a new publication from a PDF (+ video)
├── requirements.txt
└── CNAME                     <- custom domain (thomaszewski.com)
```

## One-time setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Adding a new publication (the automated way)

```bash
python generate.py path/to/paper.pdf \
    --authors "A. Author, B. Author, B. Thomaszewski" \
    --venue "Proc. ACM SIGGRAPH 2026" \
    --year 2026 \
    --video "https://youtu.be/XXXXXXXX" \
    --project
```

This will:
1. Extract a title + abstract from the PDF (best-effort — review!).
2. Extract a thumbnail image and copy the PDF into `assets/`.
3. Append an entry to `data/publications.yaml`.
4. Regenerate `index.html`, `publications.html`, and (with `--project`)
   `projects/<id>/index.html`.

**Always review the new entry in `data/publications.yaml`** — title/author
extraction from PDFs is heuristic and frequently needs small corrections
(e.g. line breaks in the title, author name formatting).

Then:

```bash
git add -A
git commit -m "Add <paper title>"
git push
```

GitHub Pages will redeploy automatically.

## Editing publications by hand

Open `data/publications.yaml`, add/edit/remove entries, then run:

```bash
python build.py
```

to regenerate all pages.

## Marking a paper as a homepage highlight

Set `highlight: true` and provide a wide `carousel_image` (around
1200x360px) in the entry. It will appear in the homepage carousel.

## Marking a paper for a standalone project page

Set `project: true`. Optionally add `abstract` and `bibtex` fields for
richer project pages. A page will be generated at
`projects/<id>/index.html` linking the PDF, video (embedded if YouTube
or local .mp4), abstract, and BibTeX.

## Custom domain

The `CNAME` file points GitHub Pages at `thomaszewski.com`. Make sure
your DNS settings point to GitHub Pages per
https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site
