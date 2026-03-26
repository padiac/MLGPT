#!/usr/bin/env python3
r"""Normalize GitHub-flavored math markup for Streamlit KaTeX rendering.

Usage:
    python scripts/normalize_github_math.py <file.md>          # print to stdout
    python scripts/normalize_github_math.py <file.md> -o out.md  # write to file

Only touches math delimiters; leaves all other content untouched.

Transformations applied (outside code fences):
  1. `$ ... $`  ->  `$...$`   (trim inner spaces in inline math)
  2. `\( ... \)` -> `$...$`  (alternate inline delimiter)
  3. `\[ ... \]` -> `$$...$$` (alternate display delimiter)
  4. `\_` -> `_` inside math  (GitHub/Jekyll escaped underscore -> real subscript)
"""
import argparse
import re
import sys

_CODE_FENCE = re.compile(r"(```[\s\S]*?```)")

_SPACED_INLINE = re.compile(r"(?<!\$)\$\s+(.+?)\s+\$(?!\$)")
_PAREN_INLINE = re.compile(r"\\\((.+?)\\\)")
_BRACKET_DISPLAY = re.compile(r"\\\[([\s\S]*?)\\\]")

_DISPLAY_MATH = re.compile(r"\$\$([\s\S]*?)\$\$")
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$)((?:[^$\\]|\\.)+?)\$(?!\$)")


def _fix_escaped_underscores(m: re.Match) -> str:
    """Replace \\_ with _ inside a math region."""
    return m.group(0).replace(r"\_", "_")


def normalize(text: str) -> str:
    parts = _CODE_FENCE.split(text)
    for i in range(0, len(parts), 2):
        s = parts[i]
        # Delimiter normalization
        s = _SPACED_INLINE.sub(r"$\1$", s)
        s = _PAREN_INLINE.sub(r"$\1$", s)
        s = _BRACKET_DISPLAY.sub(r"$$\1$$", s)
        # Fix escaped underscores inside math (display first, then inline)
        s = _DISPLAY_MATH.sub(_fix_escaped_underscores, s)
        s = _INLINE_MATH.sub(_fix_escaped_underscores, s)
        parts[i] = s
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Normalize GitHub math for Streamlit KaTeX")
    parser.add_argument("file", help="Input markdown file path")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        text = f.read()

    result = normalize(text)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
