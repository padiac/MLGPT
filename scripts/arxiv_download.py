#!/usr/bin/env python3
"""
Download arXiv papers (PDF + optional metadata) to a local directory.

Usage:
  python scripts/arxiv_download.py 2301.07041
  python scripts/arxiv_download.py https://arxiv.org/abs/2301.07041v2
  python scripts/arxiv_download.py 2301.07041 2310.05866 -o doc/Diffusion/
  python scripts/arxiv_download.py 2301.07041 --meta          # also save .meta.json
  python scripts/arxiv_download.py 2301.07041 --source        # download source tarball instead
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

import requests

_ARXIV_ID_RE = re.compile(
    r"(?:^|/)(?:abs|pdf|e-print)/(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)"
    r"|^(?P<bare>\d{4}\.\d{4,5}(?:v\d+)?)$"
)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def parse_arxiv_id(raw: str) -> str | None:
    """Extract an arXiv ID (e.g. '2301.07041v2') from a URL or bare ID string."""
    raw = raw.strip().rstrip("/")
    m = _ARXIV_ID_RE.search(raw)
    if not m:
        return None
    return m.group("id") or m.group("bare")


def fetch_metadata(arxiv_id: str, timeout: float) -> dict | None:
    """Query the arXiv Atom API and return structured metadata."""
    api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    r = requests.get(api_url, timeout=timeout, headers={"User-Agent": _UA})
    r.raise_for_status()

    root = ET.fromstring(r.text)
    entry = root.find(f"{_ATOM_NS}entry")
    if entry is None:
        return None

    title_el = entry.find(f"{_ATOM_NS}title")
    summary_el = entry.find(f"{_ATOM_NS}summary")
    published_el = entry.find(f"{_ATOM_NS}published")
    updated_el = entry.find(f"{_ATOM_NS}updated")

    authors = []
    for author_el in entry.findall(f"{_ATOM_NS}author"):
        name_el = author_el.find(f"{_ATOM_NS}name")
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    categories = []
    for cat_el in entry.findall(f"{_ATOM_NS}category"):
        term = cat_el.get("term")
        if term:
            categories.append(term)

    primary = entry.find(f"{_ARXIV_NS}primary_category")

    return {
        "arxiv_id": arxiv_id,
        "title": _norm_ws(title_el.text) if title_el is not None and title_el.text else None,
        "authors": authors,
        "abstract": _norm_ws(summary_el.text) if summary_el is not None and summary_el.text else None,
        "published": published_el.text.strip() if published_el is not None and published_el.text else None,
        "updated": updated_el.text.strip() if updated_el is not None and updated_el.text else None,
        "primary_category": primary.get("term") if primary is not None else None,
        "categories": categories,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def download_pdf(arxiv_id: str, dest: Path, timeout: float) -> Path:
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return _download(url, dest / f"{arxiv_id}.pdf", timeout)


def download_source(arxiv_id: str, dest: Path, timeout: float) -> Path:
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    return _download(url, dest / f"{arxiv_id}.tar.gz", timeout)


def _download(url: str, out: Path, timeout: float) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": _UA}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  downloading {out.name} ... {pct}%", end="", flush=True)
        if total:
            print()
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Download arXiv papers (PDF and optional metadata)."
    )
    p.add_argument(
        "ids",
        nargs="+",
        metavar="ID_OR_URL",
        help="arXiv ID (e.g. 2301.07041, 2301.07041v2) or URL",
    )
    p.add_argument(
        "-o", "--output-dir",
        default="doc",
        help="Destination directory (default: doc/)",
    )
    p.add_argument(
        "--meta",
        action="store_true",
        help="Also save a .meta.json with title, authors, abstract, etc.",
    )
    p.add_argument(
        "--source",
        action="store_true",
        help="Download the LaTeX source tarball instead of the PDF",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (default: 60)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds to wait between downloads when fetching multiple papers (default: 1)",
    )
    args = p.parse_args()

    dest = Path(args.output_dir)
    dest.mkdir(parents=True, exist_ok=True)

    ids: list[str] = []
    for raw in args.ids:
        aid = parse_arxiv_id(raw)
        if aid is None:
            print(f"Error: cannot parse arXiv ID from '{raw}'", file=sys.stderr)
            return 1
        ids.append(aid)

    errors = 0
    for i, arxiv_id in enumerate(ids):
        if i > 0 and args.delay > 0:
            time.sleep(args.delay)

        print(f"[{i+1}/{len(ids)}] {arxiv_id}")

        if args.meta:
            try:
                meta = fetch_metadata(arxiv_id, args.timeout)
                if meta and meta.get("title"):
                    meta_path = dest / f"{arxiv_id}.meta.json"
                    meta_path.write_text(
                        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    print(f"  metadata → {meta_path}")
                    print(f"  title: {meta['title']}")
                else:
                    print("  warning: metadata returned no title (ID may not exist)", file=sys.stderr)
            except Exception as e:
                print(f"  warning: metadata fetch failed: {e}", file=sys.stderr)

        try:
            if args.source:
                out = download_source(arxiv_id, dest, args.timeout)
            else:
                out = download_pdf(arxiv_id, dest, args.timeout)
            print(f"  saved → {out.resolve()}")
        except requests.HTTPError as e:
            print(f"  error: download failed ({e})", file=sys.stderr)
            errors += 1
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            errors += 1

    if errors:
        print(f"\n{errors}/{len(ids)} download(s) failed.", file=sys.stderr)
        return 1

    print(f"\nDone. {len(ids)} paper(s) saved to {dest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
