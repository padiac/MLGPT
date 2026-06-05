"""show_file — render-side hook used by the show-note skill.

Usage (from a Bash tool call):
    python E:/Repo/MLGPT/scripts/show_file.py <abs_path_to.md>

What it does:
  1. Normalize the .md file's math syntax for Streamlit KaTeX.
  2. Write the normalized result to <input>.shown.md next to the original.
  3. Print one line ('OK <shown_path>') and exit 0.

The MLGPT wrapper (claude_cli.py / cursor_cli.py) detects 'show_file.py'
in the Bash command and emits a show_file event to the UI; app.py then
inline-renders the .shown.md so the model doesn't have to retype the
file content token-by-token.

The script itself is the ONLY work the model has to trigger. After it
exits the model should end its turn — no further text needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Reuse the existing normalizer rather than duplicating its logic.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import normalize_github_math as norm  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: show_file.py <path.md>", file=sys.stderr)
        return 2

    src = Path(argv[1]).expanduser()
    if not src.is_file():
        print(f"not a file: {src}", file=sys.stderr)
        return 1

    if src.suffix.lower() != ".md":
        # For non-markdown we don't normalize — just acknowledge.
        print(f"OK {src}")
        return 0

    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"read error: {e}", file=sys.stderr)
        return 1

    normalized = norm.normalize(text) if hasattr(norm, "normalize") else _fallback_normalize(text)
    out = src.with_suffix(".shown.md")
    out.write_text(normalized, encoding="utf-8")
    print(f"OK {out}")
    return 0


def _fallback_normalize(text: str) -> str:
    """If normalize_github_math doesn't expose a `normalize(text)` entrypoint,
    pipe through it as a subprocess (slower path).
    """
    import subprocess
    result = subprocess.run(
        [sys.executable, str(_HERE / "normalize_github_math.py"), "-"],
        input=text, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stdout if result.returncode == 0 else text


if __name__ == "__main__":
    sys.exit(main(sys.argv))
