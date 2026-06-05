"""
MLGPT — Chat-style Q&A powered by the Cursor Agent CLI.

UI layout (ChatGPT-like):
  Left sidebar  — conversation list per IP, new-chat button, settings
  Right main    — current conversation messages with streaming responses

Knowledge directories:
  - Set env MLGPT_CWD (pipe-separated for multiple dirs), or default to `doc/` if present, else app root.
  - Multiple knowledge directories are supported; the Cursor CLI cwd is set to their common parent.
"""
import html
import json
import os
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

import streamlit as st

import backends
import db
import knowledge
import memory
import prompt_utils
import media_utils
from ui_styles import SIDEBAR_AND_MAIN_CSS

# Default knowledge directories: MLGPT_CWD (pipe-separated), else `doc/` if present, else app root
def _default_dirs() -> list[str]:
    env = os.environ.get("MLGPT_CWD", "")
    if env:
        return [d.strip() for d in env.split("|") if d.strip() and Path(d.strip()).exists()]
    _doc = ROOT / "doc"
    return [str(_doc)] if _doc.is_dir() else [str(ROOT)]

DEFAULT_DIRS: list[str] = _default_dirs()

DEFAULT_BACKEND = backends.DEFAULT_BACKEND
DEFAULT_MODEL = backends.DEFAULT_MODELS[DEFAULT_BACKEND]
# Older app versions defaulted to Opus; DB still has that string — migrate to current default.
LEGACY_DEFAULT_MODELS = ("claude-4.6-opus-high-thinking",)
DEFAULT_MODE = "agent"

db.init_db()


def get_client_ip() -> str:
    """Get the real client IP via the Tornado websocket request object.

    st.context.ip_address is not available in Streamlit <=1.44, so we
    reach into the runtime to read request.remote_ip from the websocket
    handler instead.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        from streamlit.runtime import get_instance
        from streamlit.web.server.browser_websocket_handler import BrowserWebSocketHandler

        ctx = get_script_run_ctx()
        if ctx is not None:
            client = get_instance().get_client(ctx.session_id)
            if isinstance(client, BrowserWebSocketHandler):
                ip = client.request.remote_ip
                if ip and ip not in ("::1",):
                    return ip
                if ip == "::1":
                    return "127.0.0.1"
    except Exception:
        pass
    return "127.0.0.1"


# ── Clean up interrupted streaming (e.g. page refresh during stream) ───────────
# With sync streaming we rarely hit this; handles stale state from refresh

if "_streaming_proc" in st.session_state:
    proc = st.session_state.pop("_streaming_proc")
    backends.kill_process(proc)

    partial = st.session_state.pop("_partial_response", "")
    cid = st.session_state.pop("_streaming_conv_id", None)
    if partial and cid:
        db.add_message(cid, "assistant", partial + "\n\n*(generation stopped)*")

    title_prompt = st.session_state.pop("_streaming_auto_title_prompt", None)
    if title_prompt and cid:
        user_msgs = [m for m in db.get_messages(cid) if m["role"] == "user"]
        if len(user_msgs) == 1:
            db.update_title(cid, prompt_utils.auto_title(title_prompt))


# ── Page config & CSS ────────────────────────────────────────────────────────

st.set_page_config(page_title="MLGPT", page_icon="📚", layout="wide", initial_sidebar_state="expanded")
st.markdown(SIDEBAR_AND_MAIN_CSS, unsafe_allow_html=True)

# ── Session state defaults ───────────────────────────────────────────────────

if "current_conv" not in st.session_state:
    st.session_state.current_conv = None
if "viewing_example" not in st.session_state:
    st.session_state.viewing_example = None

# ── Share link: ?conv=xxx&msg=yyy → read-only shared view ───────────────────
params = st.query_params
_share_mode = False
if "conv" in params and "msg" in params:
    _share_conv = params["conv"]
    _share_conv_info = db.get_conversation(_share_conv)
    try:
        _share_msg_id = int(params["msg"])
    except ValueError:
        _share_msg_id = None
    if _share_conv_info and _share_msg_id:
        _share_mode = True
elif "conv" in params:
    _share_conv = params["conv"]
    if db.get_conversation(_share_conv):
        st.session_state.current_conv = _share_conv

client_ip = get_client_ip()

# Load settings from DB (persists across page refresh); fallback to defaults
if "settings" not in st.session_state:
    defaults = {
        "backend": DEFAULT_BACKEND,
        "model": DEFAULT_MODEL,
        "mode": DEFAULT_MODE,
        "cwd": list(DEFAULT_DIRS),
    }
    saved = db.get_user_settings(client_ip)
    if saved:
        b = saved.get("backend") or defaults["backend"]
        if b not in backends.BACKENDS:
            b = defaults["backend"]
        merged = {
            "backend": b,
            "model": saved.get("model") or backends.DEFAULT_MODELS.get(b, DEFAULT_MODEL),
            "mode": saved.get("mode") or defaults["mode"],
            "cwd": saved.get("cwd") or defaults["cwd"],
        }
        if merged["model"] in LEGACY_DEFAULT_MODELS:
            merged["model"] = backends.DEFAULT_MODELS.get(b, DEFAULT_MODEL)
            db.save_user_settings(client_ip, merged)
        st.session_state.settings = merged
    else:
        st.session_state.settings = dict(defaults)

# Share mode: render full conversation up to shared message, no sidebar, no chat input
if _share_mode:
    _share_messages = db.get_messages_up_to(_share_conv, _share_msg_id)
    if _share_messages:
        for msg in _share_messages:
            with st.chat_message(msg["role"]):
                media_utils.render_message(msg["content"])
    st.stop()

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 📚 MLGPT")

    if st.button("＋  New Chat", key="btn_new_chat", use_container_width=True):
        st.session_state.current_conv = None
        st.session_state.viewing_example = None
        st.rerun()

    st.divider()

    conversations = db.get_conversations(client_ip)

    for conv in conversations:
        is_active = st.session_state.current_conv == conv["id"]
        col_title, col_del = st.columns([5, 1])
        with col_title:
            label = ("▸ " if is_active else "") + conv["title"]
            if st.button(
                label,
                key=f"c_{conv['id']}",
                use_container_width=True,
                help=media_utils.relative_time(conv["updated_at"]),
            ):
                st.session_state.current_conv = conv["id"]
                st.session_state.viewing_example = None
                st.rerun()
        with col_del:
            if st.button("×", key=f"d_{conv['id']}"):
                db.delete_conversation(conv["id"])
                if is_active:
                    st.session_state.current_conv = None
                st.rerun()

    if conversations:
        st.divider()

    _usage_examples = db.get_usage_examples()
    if _usage_examples:
        st.markdown('<p style="font-size:0.8em;color:#888;margin:0 0 4px 2px;">📝 Usage Examples</p>', unsafe_allow_html=True)
        for _ue in _usage_examples:
            _ue_active = st.session_state.viewing_example == _ue["id"]
            col_ue_title, col_ue_del = st.columns([5, 1])
            with col_ue_title:
                _ue_label = ("▸ " if _ue_active else "") + _ue["title"]
                if st.button(
                    _ue_label,
                    key=f"ue_{_ue['id']}",
                    use_container_width=True,
                ):
                    st.session_state.viewing_example = _ue["id"]
                    st.session_state.current_conv = None
                    st.rerun()
            with col_ue_del:
                if st.button("×", key=f"del_ue_{_ue['id']}"):
                    db.delete_usage_example(_ue["id"])
                    if _ue_active:
                        st.session_state.viewing_example = None
                    st.rerun()
        st.divider()

    with st.expander("📖  How to Use"):
        st.markdown("""
**MLGPT** answers from files under your **Knowledge Directories** (PDFs, Markdown, notes, blogs, etc.). It does **not** use the rest of the repository as a knowledge base.

You can add **multiple directories** — e.g. a `doc/` folder with papers AND a blog folder with Markdown posts.

---

#### Quick start — example prompts

**Summarize or compare papers**:
> `Summarize the main ideas in Diffusion/2006.11239v2.pdf`

> `Compare score-based diffusion vs DDPM using the PDFs here`

**Retrieval-style**:
> `What do these notes say about classifier-free guidance?`

---

#### Save & Share

- **💾 Save** — click the save button on any assistant answer to generate a summarized knowledge document (saved to `liked_answers/`).
- **🔗 Share** — click the link button to copy a shareable URL (read-only view up to that answer).

---

#### Tips

- Set **Knowledge Directories** — one path per line. Default tries `…/MLGPT/doc` if it exists.
- Multiple dirs are supported: the agent sees all of them as its knowledge base.
- **Memory**: Earlier turns are summarized so long chats stay usable; the assistant should reuse prior file reads when relevant.
- **Settings** persist per client IP.

---

#### Add your own example

In any conversation, type **"add this to usage example"** to save the thread here for others to see.
""")

    with st.expander("⚙  Settings"):
        # ── Backend selector ──────────────────────────────────────────────
        _backend_options = backends.list_backends()
        _backend_ids = [bid for bid, _ in _backend_options]
        _backend_labels = [label for _, label in _backend_options]
        _cur_backend = st.session_state.settings.get("backend", DEFAULT_BACKEND)
        if _cur_backend not in _backend_ids:
            _cur_backend = DEFAULT_BACKEND
        _b_idx = _backend_ids.index(_cur_backend)
        _b_sel = st.selectbox("Backend", _backend_labels, index=_b_idx, help="Which CLI to use as the LLM backend.")
        _new_backend = _backend_ids[_backend_labels.index(_b_sel)]
        if _new_backend != _cur_backend:
            # Backend changed — clear model option cache and migrate model id
            # to the new backend's default if the old one isn't valid there.
            st.session_state.pop("_model_options", None)
            st.session_state.pop("_model_options_backend", None)
            st.session_state.settings["backend"] = _new_backend
            new_models = backends.get_available_models(_new_backend)
            new_ids = {mid for mid, _ in new_models}
            if st.session_state.settings.get("model") not in new_ids:
                st.session_state.settings["model"] = backends.DEFAULT_MODELS.get(_new_backend, DEFAULT_MODEL)
            db.save_user_settings(client_ip, st.session_state.settings)
            st.rerun()

        # ── Model selector (backend-aware) ────────────────────────────────
        if (
            "_model_options" not in st.session_state
            or st.session_state.get("_model_options_backend") != _new_backend
        ):
            pairs = backends.get_available_models(_new_backend)
            if pairs:
                st.session_state._model_options = pairs
            else:
                fallback_model = backends.DEFAULT_MODELS.get(_new_backend, DEFAULT_MODEL)
                st.session_state._model_options = [(fallback_model, fallback_model)]
            st.session_state._model_options_backend = _new_backend

        _model_ids = [mid for mid, _ in st.session_state._model_options]
        _model_labels = [f"{display}  ({mid})" for mid, display in st.session_state._model_options]
        _cur_model = st.session_state.settings["model"]
        _model_idx = _model_ids.index(_cur_model) if _cur_model in _model_ids else 0
        _sel = st.selectbox("Model", _model_labels, index=_model_idx)
        st.session_state.settings["model"] = _model_ids[_model_labels.index(_sel)]
        st.session_state.settings["mode"] = st.selectbox(
            "Mode",
            ["agent", "ask", "plan"],
            index=["agent", "ask", "plan"].index(
                st.session_state.settings["mode"]
            ),
        )
        st.markdown("**Knowledge directories**")
        st.caption(
            "Each folder is part of the knowledge base. The agent cwd is the common parent of these paths."
        )

        _cur_dirs: list[str] = st.session_state.settings.get("cwd", [])
        if isinstance(_cur_dirs, str):
            _cur_dirs = [_cur_dirs] if _cur_dirs else []
        if "_kw_dirs_n" not in st.session_state:
            _n0 = max(len(_cur_dirs), 1)
            st.session_state._kw_dirs_n = _n0
            for _i in range(_n0):
                st.session_state[f"kw_dir_{_i}"] = _cur_dirs[_i] if _i < len(_cur_dirs) else ""

        for _i in range(st.session_state._kw_dirs_n):
            st.text_input(
                f"Directory {_i + 1}",
                key=f"kw_dir_{_i}",
                placeholder=r"e.g. C:\Users\you\MLGPT\doc",
            )

        _add_col, _rem_col = st.columns(2)
        with _add_col:
            if st.button("＋ Add directory", key="kw_dirs_add", use_container_width=True):
                _idx = st.session_state._kw_dirs_n
                st.session_state._kw_dirs_n += 1
                st.session_state[f"kw_dir_{_idx}"] = ""
                st.rerun()
        with _rem_col:
            if st.session_state._kw_dirs_n > 1 and st.button(
                "Remove last", key="kw_dirs_remove", use_container_width=True
            ):
                _last = st.session_state._kw_dirs_n - 1
                st.session_state._kw_dirs_n -= 1
                if f"kw_dir_{_last}" in st.session_state:
                    del st.session_state[f"kw_dir_{_last}"]
                st.rerun()

        st.session_state.settings["cwd"] = [
            st.session_state.get(f"kw_dir_{_i}", "").strip()
            for _i in range(st.session_state._kw_dirs_n)
            if st.session_state.get(f"kw_dir_{_i}", "").strip()
        ]
        # Persist settings to DB so they survive page refresh
        db.save_user_settings(client_ip, st.session_state.settings)

# ── Main area — load conversation ────────────────────────────────────────────

# ── Usage example view (read-only) ───────────────────────────────────────────
if st.session_state.viewing_example:
    _all_examples = {e["id"]: e for e in db.get_usage_examples()}
    _sel_example = _all_examples.get(st.session_state.viewing_example)
    if _sel_example:
        st.markdown(f"### 📝 {_sel_example['title']}")
        st.divider()
        try:
            _example_msgs = json.loads(_sel_example["content"])
        except (json.JSONDecodeError, TypeError):
            _example_msgs = None
        if _example_msgs and isinstance(_example_msgs, list):
            for _emsg in _example_msgs:
                with st.chat_message(_emsg.get("role", "user")):
                    media_utils.render_message(_emsg.get("content", ""))
        else:
            st.markdown(_sel_example["content"], unsafe_allow_html=False)
        st.stop()
    else:
        st.session_state.viewing_example = None

conv_id = st.session_state.current_conv
conv_info: dict | None = None

if conv_id:
    conv_info = db.get_conversation(conv_id)
    if not conv_info:
        st.session_state.current_conv = None
        st.rerun()
    messages = db.get_messages(conv_id)
else:
    messages = []

# Welcome screen when no conversation selected
if not conv_id:
    safe_ip = html.escape(client_ip)
    st.markdown(
        f'<div class="welcome-card">'
        f'<p class="greeting">Hello User,   <span class="ip">{safe_ip}</span></p>'
        f'<p class="sub">Ask about materials in your knowledge base folder (papers, notes).<br>'
        f'Start a new conversation or pick one from the sidebar.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

# Render existing messages with per-answer save
# Use fragment to auto-refresh save status when summarization is in progress
_cwd_dirs = st.session_state.settings.get("cwd", [])
if isinstance(_cwd_dirs, str):
    _cwd_dirs = [_cwd_dirs] if _cwd_dirs else []
cwd = prompt_utils.compute_common_cwd(_cwd_dirs) if _cwd_dirs else ""
_has_pending = False
if conv_id:
    _liked = db.get_liked_entries_for_conversation(conv_id)
    _has_pending = any(e.get("status") in ("pending", "summarizing") for e in _liked.values())


@st.fragment(run_every=timedelta(seconds=2) if _has_pending else None)
def _messages_with_likes():
    liked = db.get_liked_entries_for_conversation(conv_id) if conv_id else {}
    for msg in messages:
        with st.chat_message(msg["role"]):
            media_utils.render_message(msg["content"])
            if conv_id and msg["role"] == "assistant" and "id" in msg:
                mid = msg["id"]
                entry = liked.get(mid)
                with st.container(key=f"action_btns_{mid}"):
                    btn_col1, btn_col2 = st.columns(2)
                    with btn_col1:
                        if not entry:
                            if st.button("💾", key=f"like_{mid}", help="Save to knowledge base", type="secondary"):
                                ok, m = knowledge.start_summarization(
                                    conv_id, mid, cwd,
                                    backend=st.session_state.settings.get("backend", DEFAULT_BACKEND),
                                )
                                st.toast(m)
                                st.rerun()
                        elif entry["status"] in ("pending", "summarizing"):
                            if st.button("⏳", key=f"cancel_{mid}", help="Saving… click to cancel"):
                                ok, m = knowledge.cancel_or_unlike(conv_id, mid)
                                st.toast(m)
                                st.rerun()
                        else:
                            if st.button("✓", key=f"unlike_{mid}", help="Saved · click to remove", type="secondary"):
                                ok, m = knowledge.cancel_or_unlike(conv_id, mid)
                                st.toast(m)
                                st.rerun()
                    with btn_col2:
                        if st.button("🔗", key=f"share_{mid}", help="Copy share link", type="secondary"):
                            st.session_state["_copy_share"] = f"__DYNAMIC__?conv={conv_id}&msg={mid}"
                            st.rerun()


_messages_with_likes()

_share_path = st.session_state.pop("_copy_share", None)
if _share_path:
    _qs = _share_path.split("?", 1)[1] if "?" in _share_path else ""
    st.components.v1.html(
        f"""<script>
        (function(){{
            var qs = "?{_qs}";
            var pdoc = window.parent.document;
            var origin = "";
            try {{ origin = window.parent.location.origin; }} catch(e) {{
                origin = window.location.origin;
            }}
            var url = origin + "/" + qs;

            var ok = false;
            var ta = pdoc.createElement("textarea");
            ta.value = url;
            ta.style.position = "fixed";
            ta.style.left = "-9999px";
            pdoc.body.appendChild(ta);
            ta.select();
            try {{ ok = pdoc.execCommand("copy"); }} catch(e) {{}}
            pdoc.body.removeChild(ta);
            if (!ok && window.parent.navigator.clipboard && window.parent.navigator.clipboard.writeText) {{
                window.parent.navigator.clipboard.writeText(url).then(function(){{ ok = true; }}).catch(function(){{}});
            }}

            var toast = pdoc.createElement("div");
            toast.textContent = ok ? "Link copied!" : "Copy failed – select manually and press Ctrl+C";
            toast.style.cssText = "position:fixed;top:16px;right:20px;padding:8px 16px;background:#262730;color:#fafafa;border-radius:6px;font-size:13px;z-index:999999;font-family:sans-serif;box-shadow:0 2px 12px rgba(0,0,0,0.3);";
            pdoc.body.appendChild(toast);
            setTimeout(function(){{ toast.remove(); }}, 1500);
        }})();
        </script>""",
        height=0,
    )

# ── Chat input & streaming response ─────────────────────────────────────────

if prompt := st.chat_input("Ask anything…"):
    settings = st.session_state.settings

    # Create conversation on first message
    if not conv_id:
        conv_id = db.create_conversation(client_ip, prompt_utils.auto_title(prompt))
        st.session_state.current_conv = conv_id
        conv_info = db.get_conversation(conv_id)

    # ── "Add to usage example" interception ──────────────────────────────
    if prompt_utils.is_add_usage_example(prompt) and conv_id and messages:
        conv_title = (conv_info or {}).get("title", "Usage Example")
        example_content = prompt_utils.format_conversation_as_example(messages)
        db.add_usage_example(
            title=conv_title,
            content=example_content,
            source_conv_id=conv_id,
            created_by_ip=client_ip,
        )
        db.add_message(conv_id, "user", prompt)
        confirm_msg = f"✅ This conversation has been added to **How to Use** as an example: **{conv_title}**"
        db.add_message(conv_id, "assistant", confirm_msg)
        st.toast("Added to usage examples!")
        st.rerun()

    # Persist & show the user message
    db.add_message(conv_id, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    _dirs: list[str] = settings.get("cwd", [])
    if isinstance(_dirs, str):
        _dirs = [_dirs] if _dirs else []
    _common_cwd = prompt_utils.compute_common_cwd(_dirs)

    enriched = prompt_utils.enrich_prompt(prompt, _dirs)
    skills_text = prompt_utils.load_skills(_dirs)
    if skills_text:
        enriched = enriched + "\n\n" + skills_text
    cli_session = conv_info.get("cli_session_id") if conv_info else None

    existing_summary, _state_json, summary_msg_count = db.get_memory(conv_id)

    if messages:
        enriched, updated_summary, new_summary_msg_count = memory.build_prompt(
            current_question=enriched,
            all_messages=messages,
            existing_summary=existing_summary,
            is_continuity_query=len(messages) > 0,
            summary_msg_count=summary_msg_count,
        )
    else:
        updated_summary = existing_summary
        new_summary_msg_count = summary_msg_count

    request_start_time = time.time()

    # Start the CLI process (cwd = common parent of all knowledge dirs)
    process, proc_err = backends.create_process(
        backend=settings.get("backend", DEFAULT_BACKEND),
        prompt=enriched,
        cwd=_common_cwd or None,
        model=settings.get("model") or None,
        mode=settings.get("mode", "agent"),
        resume_session=cli_session,
    )

    if proc_err:
        with st.chat_message("assistant"):
            st.markdown(f"**Error:** {proc_err}")
            db.add_message(conv_id, "assistant", f"**Error:** {proc_err}")
        st.rerun()

    # Stream in main thread (sync)
    st.session_state._streaming_proc = process
    st.session_state._streaming_conv_id = conv_id
    st.session_state._partial_response = ""
    st.session_state._streaming_auto_title_prompt = prompt

    with st.chat_message("assistant"):
        response_area = st.empty()
        tool_area = st.empty()
        stop_area = st.empty()
        full_response = ""
        show_file_paths: list[str] = []

        stop_area.button("⏹ Stop", key="stop_gen", type="secondary")

        for evt_type, payload in backends.iter_events(process):
            if evt_type == "text":
                full_response += payload
                st.session_state._partial_response = full_response
                response_area.markdown(full_response + "▌")

            elif evt_type == "text_replace":
                full_response = payload
                st.session_state._partial_response = full_response
                response_area.markdown(full_response + "▌")

            elif evt_type == "show_file":
                show_file_paths.append(payload)

            elif evt_type == "tool":
                tool_area.markdown(
                    f'<p class="tool-ind">🔧 {payload}</p>',
                    unsafe_allow_html=True,
                )

            elif evt_type == "session_id":
                db.update_cli_session(conv_id, payload)

            elif evt_type == "error" and not full_response:
                full_response = f"**Error:** {payload}"

            elif evt_type == "done":
                tool_area.empty()
                stop_area.empty()
                response_area.markdown(full_response or "_No response received._")

        # show_file events: render raw content directly (deduplicate paths)
        _seen_show: set[str] = set()
        deduped_show_paths: list[str] = []
        for p in show_file_paths:
            norm = os.path.normpath(os.path.join(_common_cwd, p)) if (not os.path.isabs(p) and _common_cwd) else os.path.normpath(p)
            if norm not in _seen_show:
                _seen_show.add(norm)
                deduped_show_paths.append(p)

        if deduped_show_paths:
            rendered_paths: list[str] = []
            for rel_path in deduped_show_paths:
                abs_path = os.path.normpath(os.path.join(_common_cwd, rel_path)) if (not os.path.isabs(rel_path) and _common_cwd) else rel_path
                if not os.path.isfile(abs_path):
                    continue
                rendered_paths.append(abs_path)
                try:
                    raw = Path(abs_path).read_text(encoding="utf-8", errors="ignore")
                    name = os.path.basename(abs_path)
                    is_config = media_utils._is_config_file(abs_path)
                    is_md = abs_path.lower().endswith(".md")
                    if is_md:
                        # show-note path: render the file INLINE as markdown
                        # so headings and math render. Prefer the script-
                        # normalized sibling <input>.shown.md if scripts/
                        # show_file.py produced it (math/underscore fixes).
                        shown = abs_path[:-3] + ".shown.md"
                        if os.path.isfile(shown):
                            try:
                                raw = Path(shown).read_text(encoding="utf-8", errors="ignore")
                                # Render the script's polished version, not the original.
                                rendered_paths[-1] = shown
                            except OSError:
                                pass
                        st.markdown(raw, unsafe_allow_html=False)
                    elif abs_path.lower().endswith(".json"):
                        with st.expander(f"📄 {name}", expanded=not is_config):
                            st.json(json.loads(raw))
                    else:
                        with st.expander(f"📄 {name}", expanded=not is_config):
                            st.code(raw[:50000], language=media_utils.lang_for_file(name))
                except (json.JSONDecodeError, OSError):
                    pass
            if rendered_paths:
                full_response = media_utils.attach_files(full_response, rendered_paths)

        plotly_cache, plotly_fig, plotly_html_path = media_utils.try_interactive_plot(_common_cwd, full_response)
        if plotly_fig:
            st.plotly_chart(plotly_fig, use_container_width=True, key=f"plotly_{int(time.time()*1000)}")
            full_response = media_utils.attach_plotly(full_response, plotly_cache)
        elif plotly_html_path:
            html_content = Path(plotly_html_path).read_text(encoding="utf-8", errors="ignore")
            st.components.v1.html(html_content, height=1200, scrolling=False)
            full_response = media_utils.attach_plotly_html(full_response, plotly_html_path)
        else:
            all_new_images: list[str] = []
            for _d in (_dirs if _dirs else [_common_cwd] if _common_cwd else []):
                all_new_images.extend(media_utils.find_new_images(_d, request_start_time, full_response))
            seen_img: set[str] = set()
            new_images: list[str] = []
            for ip in all_new_images:
                nip = os.path.normpath(ip)
                if nip not in seen_img:
                    seen_img.add(nip)
                    new_images.append(ip)
            for img_path in new_images:
                st.image(img_path, caption=os.path.basename(img_path))
            if new_images:
                full_response = media_utils.attach_images(full_response, new_images)

    # Clear streaming state
    st.session_state.pop("_streaming_proc", None)
    st.session_state.pop("_streaming_conv_id", None)
    st.session_state.pop("_partial_response", None)
    st.session_state.pop("_streaming_auto_title_prompt", None)

    if full_response:
        db.add_message(conv_id, "assistant", full_response)

    # +2: user message and assistant message we just added (build_prompt used pre-add messages)
    db.update_memory(conv_id, updated_summary, "", new_summary_msg_count + 2)

    user_msgs = [m for m in db.get_messages(conv_id) if m["role"] == "user"]
    if len(user_msgs) == 1:
        db.update_title(conv_id, prompt_utils.auto_title(prompt))

    st.rerun()
