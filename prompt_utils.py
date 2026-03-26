"""Prompt building for MLGPT — working directory and doc/ hints for the agent."""
import os
import re

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
