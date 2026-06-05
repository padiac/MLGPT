---
name: show-note
description: "Display a .md note from the knowledge directories to the user. Use whenever the user wants to view a markdown file's content — phrasings include 'show me X.md', '展示全文', '看一下', '把这篇给我看', 'open it', etc."
disable-model-invocation: true
---

# Show Note

When the user wants to see a `.md` file from the knowledge directories, do **exactly this and nothing else**:

1. Locate the absolute path of the requested `.md` file (use Glob if needed).
2. Run **one** Bash command (no other flags, no `-o`, no `Read` afterwards):

   ```
   python E:/Repo/MLGPT/scripts/show_file.py "<absolute_path_to.md>"
   ```

3. End your turn **immediately** after the script exits 0. No further text.

The script normalizes the file's math syntax for the frontend and the app renders it inline. Do **not** paste the file content into your response — the app handles display. A long re-typed response is wasted work; the user already sees the file from step 2.

If the file path is ambiguous (multiple matches), pick the most specific match silently — do not ask, just run `show_file.py` on the best guess.

Use this only for `.md` files in the knowledge directories. For other file types or quick excerpts, answer normally.
