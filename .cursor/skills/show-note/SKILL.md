---
name: show-note
description: "Normalize and display markdown notes. ALWAYS use when you are about to output the full or near-full content of any .md file from the knowledge directories — regardless of how the user phrased the request."
disable-model-invocation: true
---

# Show Note (with math normalization)

## When to Use

Use this skill **whenever you are about to display the full (or substantially full) content of a `.md` file** from the knowledge directories. This includes but is not limited to:

- User gives an explicit path: "show me `notes/foo.md`"
- User says "展示全文", "显示文档", "把整篇给我看", "display the document"
- User references a previously found file: "看一下第一篇", "看看那个PCA的", "read the first one", "open it"
- User says "show me", "let me see", "看一下", "读一下", "打开"
- **Any situation where you would otherwise use `cat` or `Read` to dump an entire `.md` file as your response**

Do **NOT** use this skill when:
- Answering Q&A or summarizing (you only quote short excerpts)
- The file is not `.md` (e.g. PDF — those don't need this normalization)

## Why

The notes are authored for GitHub Pages, which uses math syntax that Streamlit's KaTeX cannot render correctly (e.g. `$ x $` with inner spaces, `\_` for subscripts). The normalization script fixes these without changing other content.

## Instructions

1. Identify the `.md` file path. It may be relative to one of the knowledge directories.

2. Run the normalization script:

   ```bash
   python scripts/normalize_github_math.py "<path-to-file>"
   ```

   The script prints normalized markdown to stdout. It does **not** modify the original file.

3. Return the script's stdout **directly as your response text** — raw markdown, NOT inside a code block or code fence. The output IS markdown meant to be rendered. Do not wrap it in ``` or any other delimiter.

## Critical Rule

**NEVER** wrap the output in a code fence. The whole point is that the frontend renders the markdown + math. A code fence defeats this entirely.

- **Correct:** paste the normalized markdown directly as your reply text.
- **Wrong:** wrapping it in ```markdown ... ``` or any code block.

## Important

- **Never modify the original file.** The script is read-only.
- Only apply to `.md` files from knowledge directories.
- If the file doesn't exist, tell the user and ask for clarification.
