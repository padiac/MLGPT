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


def load_skills(dirs: list[str] | str = "") -> str:
    """Read `.cursor/skills/**/SKILL.md` from each knowledge directory.

    Injects skill instructions into the prompt so the CLI agent follows them
    even when its cwd is a common parent that doesn't contain the skills.
    """
    if isinstance(dirs, str):
        dirs = [dirs] if dirs else []
    parts: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        skills_dir = Path(d) / ".cursor" / "skills"
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
