#!/usr/bin/env python3
"""
Download a web page and save as Markdown (main article text when possible).

Usage:
  python scripts/url_to_md.py https://example.com/article
  python scripts/url_to_md.py https://example.com -o my_note.md
  python scripts/url_to_md.py https://example.com -o notes/   # writes URL-based name under notes/
  python scripts/url_to_md.py https://spa.example.com/docs -o out.md --render   # JS-heavy pages (needs: playwright install chromium)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import trafilatura

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _http_lang_prefs(lang: str) -> tuple[str, str]:
    """Return (Accept-Language header, Playwright locale) for consistent server/UI language."""
    key = (lang or "en").strip().lower().replace("_", "-")
    if key in ("zh", "zh-cn", "cn"):
        return "zh-CN,zh;q=0.9,en;q=0.8", "zh-CN"
    if key in ("en", "en-us", "en-gb"):
        return "en-US,en;q=0.9", "en-US"
    # passthrough e.g. ja, fr-FR
    if "-" in key:
        return f"{key},{key.split('-')[0]};q=0.9", key
    return f"{key};q=0.9", key


def _safe_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "page").replace(":", "_")
    path = (parsed.path or "/").strip("/").replace("/", "_") or "index"
    if len(path) > 80:
        path = path[:80]
    for c in '<>:"/\\|?*':
        path = path.replace(c, "_")
    return f"{host}_{path}.md"


def resolve_output_path(url: str, output: str | None) -> Path:
    """File path, or directory (existing / trailing slash) → URL-based .md inside."""
    if not output:
        return Path(_safe_filename_from_url(url))
    raw = output.strip()
    if raw.endswith(("/", "\\")):
        return Path(raw) / _safe_filename_from_url(url)
    out = Path(raw)
    if out.exists() and out.is_dir():
        return out / _safe_filename_from_url(url)
    if out.suffix == "":
        out = out.with_suffix(".md")
    return out


def fetch_html_playwright(
    url: str,
    timeout_s: float,
    verify_ssl: bool,
    wait_after_ms: int,
    accept_language: str,
    locale: str,
) -> str:
    """Render page in headless Chromium (for SPAs / client-rendered docs)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from e

    timeout_ms = max(int(timeout_s * 1000), 5000)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=_CHROME_UA,
                viewport={"width": 1280, "height": 720},
                locale=locale,
                extra_http_headers={"Accept-Language": accept_language},
                ignore_https_errors=not verify_ssl,
            )
            page = context.new_page()
            page.goto(url, wait_until="load", timeout=timeout_ms)
            try:
                page.evaluate(
                    "() => { window.scrollTo(0, document.body.scrollHeight); }"
                )
                time.sleep(0.4)
                page.evaluate("() => { window.scrollTo(0, 0); }")
            except Exception:
                pass
            if wait_after_ms > 0:
                time.sleep(wait_after_ms / 1000.0)
            return page.content()
        finally:
            browser.close()


def fetch_html(
    url: str,
    timeout: float,
    verify_ssl: bool,
    accept_language: str,
) -> tuple[str, str | None]:
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": accept_language,
    }
    r = requests.get(url, headers=headers, timeout=timeout, verify=verify_ssl)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text, r.headers.get("Content-Type")


def html_to_markdown(html: str, url: str) -> str | None:
    return trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
    )


def fallback_html_to_md(html: str) -> str:
    try:
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return h.handle(html)
    except ImportError:
        # Minimal fallback: strip tags crudely
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_document(
    url: str,
    body_md: str,
    used_fallback: bool,
    fetch_mode: str,
    lang_pref: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    extract_label = "html2text (full page)" if used_fallback else "trafilatura"
    lines = [
        "---",
        f"source: {url}",
        f"fetched: {now}",
        f"fetch: {fetch_mode}",
        f"lang: {lang_pref}",
        f"extractor: {extract_label}",
        "---",
        "",
        body_md.strip(),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Download a URL and write Markdown.")
    p.add_argument("url", help="Page URL")
    p.add_argument(
        "-o",
        "--output",
        help="Output .md file, or a directory / path ending with / (filename from URL)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout seconds (default: 30)",
    )
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (use only if SSL fails locally)",
    )
    p.add_argument(
        "--full-page",
        action="store_true",
        help="Skip trafilatura; convert full HTML with html2text (needs html2text)",
    )
    p.add_argument(
        "--render",
        action="store_true",
        help="Load page in headless Chromium so JS-rendered content is present (pip install playwright; playwright install chromium)",
    )
    p.add_argument(
        "--wait-after-ms",
        type=int,
        default=2500,
        metavar="MS",
        help="After DOM ready, wait this many ms for SPA paint (only with --render; default: 2500)",
    )
    p.add_argument(
        "--lang",
        default="en",
        metavar="CODE",
        help="Preferred page language: en (default) or zh — sent as Accept-Language + browser locale (fixes EN site vs ZH download mismatch)",
    )
    args = p.parse_args()

    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        print("Error: URL must start with http:// or https://", file=sys.stderr)
        return 1

    accept_language, pw_locale = _http_lang_prefs(args.lang)

    fetch_mode = "playwright" if args.render else "requests"
    try:
        if args.render:
            try:
                html = fetch_html_playwright(
                    url,
                    args.timeout,
                    verify_ssl=not args.insecure,
                    wait_after_ms=max(0, args.wait_after_ms),
                    accept_language=accept_language,
                    locale=pw_locale,
                )
            except RuntimeError as e:
                print(str(e), file=sys.stderr)
                return 1
            except Exception as e:
                print(f"Playwright failed: {e}", file=sys.stderr)
                return 1
        else:
            html, _ctype = fetch_html(
                url,
                args.timeout,
                verify_ssl=not args.insecure,
                accept_language=accept_language,
            )
    except requests.RequestException as e:
        print(f"Download failed: {e}", file=sys.stderr)
        return 1

    used_fallback = False
    if args.full_page:
        body = fallback_html_to_md(html)
        used_fallback = True
    else:
        body = html_to_markdown(html, url)
        if not body or not body.strip():
            body = fallback_html_to_md(html)
            used_fallback = True

    if not (body or "").strip():
        if fetch_mode == "requests":
            print(
                "Warning: extracted body is empty. This URL is likely a JS SPA — "
                "re-run with --render (install: pip install playwright && playwright install chromium).",
                file=sys.stderr,
            )
        else:
            print(
                "Warning: body is still empty after --render. The site may block automation "
                "or need a longer --wait-after-ms.",
                file=sys.stderr,
            )

    out_path = resolve_output_path(url, args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        build_document(url, body, used_fallback, fetch_mode, args.lang),
        encoding="utf-8",
    )
    print(out_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
