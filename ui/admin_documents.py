"""Page "Documents" (admin only): upload extra documents the assistant can answer from, and remove them.

Uploads are searchable as soon as they are saved: offline keyword search re-reads the uploads
folder, and in live mode the chunks are also added to the vector index (a failure there is shown
as a warning; keyword search still finds the document). The built-in college documents are listed
read-only: they ship with the app and cannot be deleted here.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    confirm_prompt,
    empty_state,
    flash,
    hero,
    is_demo_mode,
    local_stamp,
    require_role,
    section_header,
    show_flashes,
)
from ui.theme import CONTENT_WIDTH, inject_css  # noqa: E402

inject_css(max_width=CONTENT_WIDTH)
user = require_role("admin")

try:
    from smart_campus_assistant import documents as docs_mod
except Exception as exc:  # noqa: BLE001
    st.error(f"The documents module could not be loaded: {type(exc).__name__}.")
    st.stop()

# Demo mode has no vector index: offline search is all there is, and it picks uploads up by itself.
LIVE = not bool(st.session_state["demo_mode"] if "demo_mode" in st.session_state else is_demo_mode())
MAX_MB = docs_mod.MAX_UPLOAD_BYTES // (1024 * 1024)

st.session_state.setdefault("doc_upload_round", 0)


def _flash(kind: str, text: str) -> None:
    flash("doc_flash", kind, text)  # the shared pattern: one inline message after the action


def _uploader_key() -> str:
    """A new key after each upload empties the file picker."""
    return f"doc_upload_file_{st.session_state['doc_upload_round']}"


def document_card_html(info: dict) -> str:
    """One uploaded document as a card (every value escaped)."""
    sections = int(info.get("sections") or 0)
    meta = " · ".join([
        str(info.get("name", "")),
        str(info.get("kind", "")),
        str(info.get("size_label", "")),
        f"{sections} section{'s' if sections != 1 else ''}",
        f"uploaded {local_stamp(info.get('uploaded_at', ''))}",
    ])
    return (
        f'<div class="sca-notice"><div class="head"><span>📄</span>'
        f'<span class="title">{escape(str(info.get("title") or info.get("name") or ""))}</span></div>'
        f'<div class="meta">{escape(meta)}</div></div>'
    )


# ---------- callbacks ----------

def upload() -> None:
    """Button callback: validate, store and index the chosen file, then empty the picker."""
    file = st.session_state.get(_uploader_key())
    if file is None:
        _flash("warning", "Choose a file first.")
        return
    try:
        info, indexed, message = docs_mod.publish_document(file.name, file.getvalue(), live=LIVE)
    except docs_mod.DocumentError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not save the document: {type(exc).__name__}.")
        return
    _flash("success" if indexed else "warning", f"Uploaded {info['name']}. {message}")
    st.session_state["doc_upload_round"] += 1


def ask_delete(name: str) -> None:
    st.session_state["doc_confirm"] = name


def cancel_delete() -> None:
    st.session_state.pop("doc_confirm", None)


def confirm_delete(name: str) -> None:
    st.session_state.pop("doc_confirm", None)
    try:
        deleted, unindexed, message = docs_mod.remove_document(name, live=LIVE)
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not delete {name}: {type(exc).__name__}.")
        return
    if not deleted:
        _flash("warning", message)
    else:
        _flash("success" if unindexed else "warning", f"Deleted {name}. {message}")


# ---------- page ----------

hero("Admin · documents", "Documents",
     "Upload extra rules, circulars or policies. The assistant can answer from them right away "
     "and cites the document and section.")
show_flashes("doc_flash")

types = docs_mod.allowed_types()
type_text = ", ".join(f".{t}" for t in types)
section_header("Upload a document",
               f"{type_text} up to {MAX_MB} MB. Headings split the text into sections; HTML is removed.")
st.file_uploader("Document", type=types, key=_uploader_key(), max_upload_size=MAX_MB,
                 help="Markdown headings (#, ##, ###) become the sections shown in citations.")
if not docs_mod.pdf_supported():
    st.caption("PDF upload is off: install the pypdf package to turn it on.")
st.button("Upload and index", type="primary", icon=":material/upload_file:", on_click=upload, key="doc_upload")

try:
    uploaded = docs_mod.list_documents()
except Exception as exc:  # noqa: BLE001
    uploaded = []
    st.warning(f"The uploaded documents could not be listed: {type(exc).__name__}.", icon=":material/warning:")

count = len(uploaded)
section_header("Uploaded documents", f"{count} document{'s' if count != 1 else ''}, newest first")
if not uploaded:
    empty_state("📄", "No uploaded documents yet", "Upload a file above and the assistant can answer from it.",
                compact=True)
pending = st.session_state.get("doc_confirm")
for info in uploaded:
    name = info["name"]
    card, action = st.columns([6, 1], vertical_alignment="center")
    with card:
        st.markdown(f'<div class="sca-notices">{document_card_html(info)}</div>', unsafe_allow_html=True)
    with action:
        st.button("Delete", key=f"doc_del_{name}", icon=":material/delete:", on_click=ask_delete, args=(name,),
                  disabled=pending == name)
    if pending == name:
        confirm_prompt(f"Delete {name}? The assistant will stop using it. This cannot be undone.",
                       yes_label="Yes, delete", yes_key=f"doc_yes_{name}", on_yes=confirm_delete, yes_args=(name,),
                       no_key=f"doc_no_{name}", on_no=cancel_delete)

with st.expander("Built-in college documents (read-only)", icon=":material/lock:"):
    st.caption("These ship with the app. Edit them in data/college and re-run the ingest; they cannot be deleted here.")
    try:
        builtin = docs_mod.college_documents()
    except Exception as exc:  # noqa: BLE001
        builtin = []
        st.warning(f"The college documents could not be read: {type(exc).__name__}.", icon=":material/warning:")
    if builtin:
        st.dataframe(
            [{"Document": d["name"], "Sections": d["sections"], "Chunks": d["chunks"]} for d in builtin],
            hide_index=True, width="stretch",
        )
