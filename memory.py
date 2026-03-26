"""Conversation memory: rolling summary + recent turns (compact prompts for long chats)."""
from __future__ import annotations

import re

# ── Content filters ──────────────────────────────────────────────────────────

_MARKER_RE = re.compile(r"<!-- (?:PLOTLY_CHART|ATTACHED_IMAGES):.*?-->", re.DOTALL)
_LOG_LINE_BLOCK_RE = re.compile(
    r"(?:^|\n)(?:\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.*?\n){5,}",
    re.DOTALL,
)
_LONG_CODE_RE = re.compile(r"```[^\n]*\n.{2000,}?```", re.DOTALL)

RECENT_TURN_COUNT = 3
MAX_SUMMARY_CHARS = 5000
MAX_RECENT_MSG_CHARS = 3000


def filter_content(content: str) -> str:
    """Strip UI markers, long timestamp runs, and oversized code blocks."""
    text = _MARKER_RE.sub("", content)
    if content.count("\n") >= 5:
        text = _LOG_LINE_BLOCK_RE.sub("\n[long line block omitted — see conversation_summary]\n", text)
    if len(text) >= 2500:
        text = _LONG_CODE_RE.sub("```\n[large code block omitted]\n```", text)
    return text.strip()


def compress_message(role: str, content: str, max_chars: int = 400) -> str:
    """Compress a single message for the rolling summary."""
    filtered = filter_content(content)
    if len(filtered) <= max_chars:
        return f"{role}: {filtered}"

    if role == "Assistant":
        paragraphs = [p.strip() for p in filtered.split("\n\n") if p.strip()]
        if len(paragraphs) >= 2:
            compressed = paragraphs[0] + "\n...\n" + paragraphs[-1]
            if len(compressed) <= max_chars:
                return f"{role}: {compressed}"
        return f"{role}: {filtered[:max_chars]}…"
    else:
        return f"{role}: {filtered[:max_chars]}…"


def build_summary(
    existing_summary: str,
    turns_to_compress: list[dict],
) -> str:
    """Compress evicted turns into the rolling summary."""
    new_parts = []
    for msg in turns_to_compress:
        role = "User" if msg["role"] == "user" else "Assistant"
        new_parts.append(compress_message(role, msg["content"]))
    new_block = "\n".join(new_parts)

    if existing_summary:
        combined = existing_summary + "\n" + new_block
    else:
        combined = new_block

    if len(combined) > MAX_SUMMARY_CHARS:
        combined = "…" + combined[-(MAX_SUMMARY_CHARS - 1):]

    return combined


def build_prompt(
    current_question: str,
    all_messages: list[dict],
    existing_summary: str,
    is_continuity_query: bool,
    summary_msg_count: int = 0,
) -> tuple[str, str, int]:
    """Assemble prompt from rolling summary + recent turns.

    Returns (prompt_text, updated_summary, new_summary_msg_count).
    """

    recent_count = RECENT_TURN_COUNT * 2
    total = len(all_messages)
    if total > recent_count:
        older = all_messages[:-recent_count]
        recent = all_messages[-recent_count:]
        prev_older_count = max(0, summary_msg_count - recent_count)
        newly_evicted = older[prev_older_count:]
        if newly_evicted:
            updated_summary = build_summary(existing_summary, newly_evicted)
        else:
            updated_summary = existing_summary or ""
        new_summary_msg_count = total
    else:
        recent = list(all_messages)
        updated_summary = existing_summary or ""
        new_summary_msg_count = summary_msg_count

    blocks: list[str] = [current_question]

    if is_continuity_query:
        blocks.append(
            "<note>Use conversation_summary to avoid repeating work already done "
            "(e.g. files already read in the knowledge folder). Reuse prior answers unless the user "
            "asks for a fresh pass.</note>"
        )

    if updated_summary:
        blocks.append(
            f"<conversation_summary>\n{updated_summary}\n</conversation_summary>"
        )

    if recent:
        recent_lines: list[str] = []
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = filter_content(msg["content"])
            if len(content) > MAX_RECENT_MSG_CHARS:
                half = MAX_RECENT_MSG_CHARS // 2
                content = content[:half] + "\n[...]\n" + content[-half:]
            recent_lines.append(f"{role}: {content}")
        recent_block = "\n\n".join(recent_lines)
        blocks.append(
            f"<recent_conversation>\n{recent_block}\n</recent_conversation>"
        )

    return "\n\n".join(blocks), updated_summary, new_summary_msg_count
