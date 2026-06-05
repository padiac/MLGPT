"""Prompt building for MLGPT — working directory and doc/ hints for the agent."""
import os
import re
from pathlib import Path

_USAGE_EXAMPLE_RE = re.compile(
    r"(?:add|save|put|store|append)\b.*\b(?:usage\s*example|how\s*to\s*use|example)",
    re.IGNORECASE,
)


def auto_title(question: str) -> str:
    title = question.strip().split("\n")[0]
    return (title[:47] + "...") if len(title) > 50 else (title or "New Chat")


def compute_common_cwd(dirs: list[str]) -> str:
    """Return the deepest common parent of *dirs* (for Cursor CLI --cwd).

    Falls back to the first directory if commonpath fails (e.g. different drives on Windows).
    """
    valid = [d for d in dirs if d and os.path.isdir(d)]
    if not valid:
        return ""
    if len(valid) == 1:
        return valid[0]
    try:
        return os.path.commonpath(valid)
    except ValueError:
        return valid[0]


def enrich_prompt(question: str, dirs: list[str] | str = "") -> str:
    """Scope Q&A to one or more knowledge directories."""
    if isinstance(dirs, str):
        dirs = [dirs] if dirs else []
    dirs = [d for d in dirs if d]
    if not dirs:
        return question

    if len(dirs) == 1:
        header = (
            f"Your working directory is `{dirs[0]}`. "
            "This folder is the **only** knowledge base: answer using PDFs, Markdown, and other files **here**. "
            "Do not treat the parent repository, application source code, or paths outside this directory as material to quote or reason from. "
            "If the question cannot be answered from these files, say so.\n\n"
            "Relative paths are relative to this directory.\n\n"
        )
    else:
        listing = "\n".join(f"  - `{d}`" for d in dirs)
        header = (
            "Your knowledge base consists of the following directories:\n"
            f"{listing}\n\n"
            "Answer using PDFs, Markdown, and other files **only** from these directories. "
            "Do not treat the parent repository, application source code, or paths outside these directories as material to quote or reason from. "
            "If the question cannot be answered from these files, say so.\n\n"
            "Use absolute paths or paths relative to your working directory when referencing files.\n\n"
        )
    return header + question


_MLGPT_ROOT = Path(__file__).resolve().parent
# Subdirectories under each search root that may hold SKILL.md files.
# `.cursor/skills/` is Cursor's convention; `.claude/skills/` is Claude Code's.
# We inline ALL of them into the prompt so skills work identically regardless
# of which backend (Cursor / Claude) is active — backend-native auto-discovery
# differs, and Cursor's `.cursor/skills/` is invisible to Claude Code.
_SKILL_SUBDIRS = (
    (".cursor", "skills"),
    (".claude", "skills"),
)


def load_skills(dirs: list[str] | str = "") -> str:
    """Read every SKILL.md under the MLGPT project root AND each knowledge dir.

    Searches `.cursor/skills/**/SKILL.md` and `.claude/skills/**/SKILL.md` so
    project-level skills (like show-note) apply to both backends regardless of
    where the agent's cwd ends up.
    """
    if isinstance(dirs, str):
        dirs = [dirs] if dirs else []
    # MLGPT root is always searched (it owns project-level skills); knowledge
    # dirs are also searched (for user-authored per-corpus skills).
    roots = [_MLGPT_ROOT, *[Path(d) for d in dirs if d]]
    parts: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for subdir in _SKILL_SUBDIRS:
            skills_dir = root.joinpath(*subdir)
            if not skills_dir.is_dir():
                continue
            for f in sorted(skills_dir.glob("**/SKILL.md")):
                norm = str(f.resolve())
                if norm in seen:
                    continue
                seen.add(norm)
                try:
                    text = f.read_text(encoding="utf-8").strip()
                    if text:
                        parts.append(text)
                except OSError:
                    continue
    return "\n\n".join(parts)


def is_add_usage_example(question: str) -> bool:
    """Return True if the user is asking to add the current conversation as a usage example."""
    return bool(_USAGE_EXAMPLE_RE.search(question))


def format_conversation_as_example(messages: list[dict]) -> str:
    """Serialize conversation messages to JSON for storage. Preserves all markers."""
    import json
    cleaned = []
    for msg in messages:
        content = msg.get("content", "").strip()
        if not content:
            continue
        cleaned.append({"role": msg["role"], "content": content})
    return json.dumps(cleaned, ensure_ascii=False)
