#!/usr/bin/env python3
"""
Scan doc/ folder, fetch missing arxiv metadata, and generate INDEX.md.

Usage:
  python scripts/organize_docs.py                  # scan + generate index
  python scripts/organize_docs.py --fetch-meta      # also fetch missing arxiv metadata
  python scripts/organize_docs.py --reorganize      # move files into new directory structure
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "doc"

ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}(?:v\d+)?)")

# ---------- classification rules ----------

# Map arxiv category prefixes to folder names
CATEGORY_MAP = {
    "cs.CL": "nlp-language",
    "cs.LG": "machine-learning",
    "cs.CV": "computer-vision",
    "cs.AI": "artificial-intelligence",
    "cs.IR": "information-retrieval",
    "stat.ML": "machine-learning",
}

# Keywords in title/abstract → suggested subfolder
KEYWORD_RULES = [
    (r"(?i)\b(transformer|attention)\b", "transformer-attention"),
    (r"(?i)\b(diffusion|score.?matching|denois)\b", "diffusion-models"),
    (r"(?i)\b(retrieval.augment|RAG)\b", "rag-retrieval"),
    (r"(?i)\b(in.context.learn|ICL|few.shot)\b", "in-context-learning"),
    (r"(?i)\b(RLHF|reinforcement.*human|alignment)\b", "alignment-rlhf"),
    (r"(?i)\b(fine.?tun|LoRA|adapter|PEFT)\b", "fine-tuning"),
    (r"(?i)\b(pretrain|pre.train|foundation.model)\b", "pretraining"),
    (r"(?i)\b(benchmark|evaluat|leaderboard)\b", "evaluation-benchmark"),
    (r"(?i)\b(prompt|chain.of.thought|CoT)\b", "prompting"),
    (r"(?i)\b(agent|tool.use|function.call)\b", "agents-tools"),
    (r"(?i)\b(quantiz|pruning|distill|compress)\b", "efficiency"),
    (r"(?i)\b(vision|image|video|visual)\b", "vision"),
]


def is_arxiv_id(filename: str) -> str | None:
    """Extract arxiv ID from filename if it looks like one."""
    m = ARXIV_ID_RE.match(filename)
    return m.group(1) if m else None


def load_meta(pdf_path: Path) -> dict | None:
    """Load .meta.json sidecar if it exists."""
    meta_path = pdf_path.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def fetch_arxiv_meta(arxiv_id: str) -> dict | None:
    """Fetch metadata from arxiv API. Reuses logic from arxiv_download.py."""
    try:
        from arxiv_download import fetch_metadata
        return fetch_metadata(arxiv_id, timeout=30.0)
    except ImportError:
        pass

    # Fallback: inline fetch
    import xml.etree.ElementTree as ET
    import requests

    api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
    ua = "Mozilla/5.0 (organize_docs script)"
    r = requests.get(api_url, timeout=30, headers={"User-Agent": ua})
    r.raise_for_status()

    ns = "{http://www.w3.org/2005/Atom}"
    arxiv_ns = "{http://arxiv.org/schemas/atom}"
    root = ET.fromstring(r.text)
    entry = root.find(f"{ns}entry")
    if entry is None:
        return None

    title_el = entry.find(f"{ns}title")
    summary_el = entry.find(f"{ns}summary")
    published_el = entry.find(f"{ns}published")
    authors = [
        a.find(f"{ns}name").text.strip()
        for a in entry.findall(f"{ns}author")
        if a.find(f"{ns}name") is not None and a.find(f"{ns}name").text
    ]
    categories = [c.get("term") for c in entry.findall(f"{ns}category") if c.get("term")]
    primary = entry.find(f"{arxiv_ns}primary_category")

    def _clean(s):
        return re.sub(r"\s+", " ", s).strip() if s else None

    return {
        "arxiv_id": arxiv_id,
        "title": _clean(title_el.text) if title_el is not None else None,
        "authors": authors,
        "abstract": _clean(summary_el.text) if summary_el is not None else None,
        "published": published_el.text.strip() if published_el is not None and published_el.text else None,
        "primary_category": primary.get("term") if primary is not None else None,
        "categories": categories,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def classify_paper(meta: dict) -> str:
    """Suggest a subfolder based on metadata."""
    text = f"{meta.get('title', '')} {meta.get('abstract', '')}"

    # Check keyword rules first (more specific)
    for pattern, folder in KEYWORD_RULES:
        if re.search(pattern, text):
            return folder

    # Fall back to arxiv category
    primary = meta.get("primary_category", "")
    if primary in CATEGORY_MAP:
        return CATEGORY_MAP[primary]
    for cat in meta.get("categories", []):
        if cat in CATEGORY_MAP:
            return CATEGORY_MAP[cat]

    return "other"


def scan_documents(doc_dir: Path) -> list[dict]:
    """Scan all documents and build a list of entries."""
    entries = []

    for pdf in sorted(doc_dir.rglob("*.pdf")):
        rel = pdf.relative_to(doc_dir)
        arxiv_id = is_arxiv_id(pdf.stem)
        meta = load_meta(pdf)

        entry = {
            "path": str(rel),
            "filename": pdf.name,
            "abs_path": str(pdf),
            "arxiv_id": arxiv_id,
            "title": None,
            "authors": [],
            "year": None,
            "category": str(rel.parts[0]) if len(rel.parts) > 1 else "uncategorized",
            "suggested_category": None,
            "has_meta": meta is not None,
        }

        if meta:
            entry["title"] = meta.get("title")
            entry["authors"] = meta.get("authors", [])
            pub = meta.get("published", "")
            if pub:
                entry["year"] = pub[:4]
            entry["suggested_category"] = classify_paper(meta)
        elif arxiv_id:
            # Guess year from arxiv ID (YYMM format)
            yy = arxiv_id[:2]
            century = "20" if int(yy) < 50 else "19"
            entry["year"] = f"{century}{yy}"
        else:
            # Non-arxiv file: use filename as title
            entry["title"] = pdf.stem

        entries.append(entry)

    # Also scan .md files (like cursor docs)
    for md in sorted(doc_dir.rglob("*.md")):
        if md.name == "INDEX.md":
            continue
        rel = md.relative_to(doc_dir)
        entries.append({
            "path": str(rel),
            "filename": md.name,
            "abs_path": str(md),
            "arxiv_id": None,
            "title": md.stem.replace("_", " ").replace("-", " "),
            "authors": [],
            "year": None,
            "category": str(rel.parts[0]) if len(rel.parts) > 1 else "uncategorized",
            "suggested_category": "reference",
            "has_meta": False,
        })

    return entries


def fetch_missing_metadata(entries: list[dict], doc_dir: Path) -> int:
    """Fetch and save metadata for arxiv papers that don't have it."""
    to_fetch = [e for e in entries if e["arxiv_id"] and not e["has_meta"]]
    if not to_fetch:
        print("All arxiv papers already have metadata.")
        return 0

    print(f"Fetching metadata for {len(to_fetch)} papers...")
    fetched = 0

    for i, entry in enumerate(to_fetch):
        if i > 0:
            time.sleep(1.0)  # Be polite to arxiv API

        arxiv_id = entry["arxiv_id"]
        print(f"  [{i+1}/{len(to_fetch)}] {arxiv_id} ...", end=" ", flush=True)

        try:
            meta = fetch_arxiv_meta(arxiv_id)
            if meta and meta.get("title"):
                pdf_path = Path(entry["abs_path"])
                meta_path = pdf_path.with_suffix(".meta.json")
                meta_path.write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                # Update entry in place
                entry["title"] = meta["title"]
                entry["authors"] = meta.get("authors", [])
                pub = meta.get("published", "")
                if pub:
                    entry["year"] = pub[:4]
                entry["has_meta"] = True
                entry["suggested_category"] = classify_paper(meta)
                print(f"OK — {meta['title'][:60]}")
                fetched += 1
            else:
                print("no title returned")
        except Exception as e:
            print(f"failed: {e}")

    print(f"\nFetched {fetched}/{len(to_fetch)} metadata files.")
    return fetched


def generate_index(entries: list[dict], doc_dir: Path) -> Path:
    """Generate INDEX.md with a table of all documents."""
    index_path = doc_dir / "INDEX.md"

    lines = [
        "# Document Index",
        "",
        f"> Auto-generated by `scripts/organize_docs.py` — {len(entries)} documents",
        "",
    ]

    # Group by current category
    from collections import defaultdict
    by_category = defaultdict(list)
    for e in entries:
        by_category[e["category"]].append(e)

    for cat in sorted(by_category.keys()):
        cat_entries = by_category[cat]
        lines.append(f"## {cat} ({len(cat_entries)} files)")
        lines.append("")
        lines.append("| File | Title | Authors | Year | Suggested Category |")
        lines.append("|------|-------|---------|------|--------------------|")

        for e in sorted(cat_entries, key=lambda x: x.get("year") or "9999"):
            title = e["title"] or e["filename"]
            if len(title) > 80:
                title = title[:77] + "..."
            authors = ", ".join(e["authors"][:3])
            if len(e["authors"]) > 3:
                authors += " et al."
            year = e["year"] or "—"
            suggested = e["suggested_category"] or "—"
            fname = e["filename"]
            # Make path relative link
            link = f"[{fname}]({e['path']})"
            lines.append(f"| {link} | {title} | {authors} | {year} | {suggested} |")

        lines.append("")

    # Summary: suggested reorganization
    lines.append("## Suggested Reorganization")
    lines.append("")
    lines.append("If you run `python scripts/organize_docs.py --reorganize`, files will be moved to:")
    lines.append("")

    from collections import Counter
    suggested = Counter(e["suggested_category"] for e in entries if e["suggested_category"])
    for folder, count in sorted(suggested.items()):
        lines.append(f"- `papers/{folder}/` — {count} files")
    lines.append("")

    content = "\n".join(lines)
    index_path.write_text(content, encoding="utf-8")
    print(f"Generated {index_path} ({len(entries)} entries)")
    return index_path


def reorganize(entries: list[dict], doc_dir: Path, dry_run: bool = True) -> None:
    """Move files into the suggested directory structure."""
    textbook_keywords = re.compile(
        r"(?i)(manual|edition|libgen|textbook|教|理论|力学"
        r"|Packt Publishing|Academic Press|Woodhead|Springer)"
    )

    moves = []
    for e in entries:
        if not e["suggested_category"]:
            continue

        old_path = Path(e["abs_path"])
        fname = e["filename"]

        # Decide: papers/ vs textbooks/ vs reference/
        if e["suggested_category"] == "reference":
            new_dir = doc_dir / "reference" / e["category"]
        elif textbook_keywords.search(fname) or textbook_keywords.search(e.get("title") or ""):
            new_dir = doc_dir / "textbooks"
        else:
            new_dir = doc_dir / "papers" / e["suggested_category"]

        new_path = new_dir / fname
        if old_path == new_path:
            continue

        moves.append((old_path, new_path))

        # Also move .meta.json if exists
        old_meta = old_path.with_suffix(".meta.json")
        if old_meta.exists():
            moves.append((old_meta, new_path.with_suffix(".meta.json")))

    if not moves:
        print("Nothing to move.")
        return

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Planned moves ({len(moves)} files):\n")
    for old, new in moves:
        old_rel = old.relative_to(doc_dir)
        new_rel = new.relative_to(doc_dir)
        print(f"  {old_rel}  →  {new_rel}")

    if dry_run:
        print(f"\nThis is a dry run. To actually move files, run:")
        print(f"  python scripts/organize_docs.py --reorganize --confirm")
        return

    for old, new in moves:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
    print(f"\nMoved {len(moves)} files.")


def main():
    parser = argparse.ArgumentParser(description="Organize doc/ folder and generate INDEX.md")
    parser.add_argument("--fetch-meta", action="store_true",
                        help="Fetch missing arxiv metadata from API")
    parser.add_argument("--reorganize", action="store_true",
                        help="Move files into suggested directory structure (dry run by default)")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually move files (use with --reorganize)")
    args = parser.parse_args()

    print(f"Scanning {DOC_DIR} ...")
    entries = scan_documents(DOC_DIR)
    print(f"Found {len(entries)} documents.\n")

    if args.fetch_meta:
        fetch_missing_metadata(entries, DOC_DIR)
        print()

    generate_index(entries, DOC_DIR)

    if args.reorganize:
        reorganize(entries, DOC_DIR, dry_run=not args.confirm)


if __name__ == "__main__":
    main()
