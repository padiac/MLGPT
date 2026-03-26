# Memory (conversation context)

## Problem

Sending the full chat history on every turn wastes tokens and grows without bound.

## Approach

Two layers plus the current user message:

1. **Rolling summary** — Older turns are compressed into `summary` (stored on `conversations`).
2. **Recent turns** — The last few user/assistant messages are kept verbatim (after light filtering).

The structured prompt looks conceptually like:

```
<current request>          ← this turn (with cwd / doc hint from prompt_utils)

<note>…reuse prior summary…</note>   ← only when there is prior history

<conversation_summary>
…compressed older dialogue…
</conversation_summary>

<recent_conversation>
User: …
Assistant: …
</recent_conversation>
```

## Parameters

- `RECENT_TURN_COUNT` — how many **exchanges** to keep raw (default 3 → 6 messages).
- Summary length is capped (`MAX_SUMMARY_CHARS`); long assistant replies in the summary are truncated intelligently.

## Database

- `conversations.summary` — rolling summary text.
- `conversations.summary_msg_count` — how many messages were accounted for when building the summary (incremental compression).
- `conversations.diagnostic_state` — legacy column; **unused** (always empty). Kept for compatibility with existing DB files.

## Code

- `memory.filter_content` — strips UI markers and very long log/code blobs from text fed into summary/recent blocks.
- `memory.build_prompt` — assembles the blocks above.
- `app.py` — after each reply, calls `db.update_memory` with the new summary and an empty diagnostic state string.
