"""Page "Notices" (admin only): publish notices for students and make them searchable by the assistant."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.components import (  # noqa: E402
    alert,
    confirm_prompt,
    email_note,
    empty_state,
    flash,
    get_branches,
    hero,
    notice_card_html,
    notify_students,
    require_role,
    section_header,
    show_flashes,
)
from ui.theme import CONTENT_WIDTH, inject_css  # noqa: E402

inject_css(max_width=CONTENT_WIDTH)
# Only a signed-in admin may use this page (app.py never registers it for students).
user = require_role("admin")

try:
    from smart_campus_assistant import notices as notices_mod
except Exception as exc:  # noqa: BLE001
    alert("error", f"The notices module could not be loaded: {type(exc).__name__}.")
    st.stop()

CATEGORIES = notices_mod.CATEGORIES
BRANCH_OPTIONS = ["ALL", *get_branches().keys()]
FORM_KEYS = {"notice_title": "", "notice_body": "", "notice_category": "general", "notice_branches": ["ALL"]}
FLASH_KEY = "notice_flash"

for key, default in FORM_KEYS.items():
    st.session_state.setdefault(key, default)


def _flash(kind: str, text: str) -> None:
    flash(FLASH_KEY, kind, text)


def publish() -> None:
    """Button callback: validate + save + index, then clear the form."""
    try:
        notice, indexed, message = notices_mod.publish_notice(
            st.session_state["notice_title"],
            st.session_state["notice_body"],
            st.session_state["notice_category"],
            st.session_state["notice_branches"],
            user.get("name") or user.get("email") or "Admin",
        )
    except notices_mod.NoticeError as exc:
        _flash("error", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not save the notice: {type(exc).__name__}.")
        return
    emailed = notify_students("notify_new_notice", notice)
    if emailed:
        message = (message + email_note(f"Notice to {emailed} student{'s' if emailed != 1 else ''}")).strip()
    if indexed:
        _flash("success", f"Published {notice['id']}. {message}")
    else:
        _flash("warning", f"Published {notice['id']}, but it is not searchable yet. {message}")
    for key, default in FORM_KEYS.items():
        st.session_state[key] = list(default) if isinstance(default, list) else default


def ask_delete(notice_id: str) -> None:
    st.session_state["notice_confirm"] = notice_id


def cancel_delete() -> None:
    st.session_state.pop("notice_confirm", None)


def confirm_delete(notice_id: str) -> None:
    st.session_state.pop("notice_confirm", None)
    try:
        deleted, unindexed, message = notices_mod.remove_notice(notice_id)
    except Exception as exc:  # noqa: BLE001
        _flash("error", f"Could not delete {notice_id}: {type(exc).__name__}.")
        return
    if not deleted:
        _flash("warning", message)
    elif unindexed:
        _flash("success", f"Deleted {notice_id}. {message}")
    else:
        _flash("warning", f"Deleted {notice_id}. {message}")


hero("Admin · notices", "Notices",
     "Publish a notice: students of the chosen branches see it on their dashboard, "
     "and the assistant can answer questions about it right away.")

# The outcome of the last action: one inline message (no toast on top of it).
show_flashes(FLASH_KEY)

left, right = st.columns([1, 1], gap="large")
with left:
    section_header(
        "New notice",
        f"Title {notices_mod.TITLE_MIN}-{notices_mod.TITLE_MAX} characters, text "
        f"{notices_mod.BODY_MIN}-{notices_mod.BODY_MAX} characters. HTML is removed.",
    )
    st.text_input("Title", key="notice_title", max_chars=notices_mod.TITLE_MAX,
                  placeholder="e.g. Mid-sem exam rescheduled")
    c1, c2 = st.columns(2)
    with c1:
        st.selectbox("Category", list(CATEGORIES), key="notice_category", format_func=lambda c: CATEGORIES[c])
    with c2:
        st.multiselect("Branches", BRANCH_OPTIONS, key="notice_branches",
                       help="ALL = every branch. Otherwise pick one or more branches.")
    st.text_area("Notice text", key="notice_body", height=180, max_chars=notices_mod.BODY_MAX,
                 placeholder="What changed, for whom, and the new dates.")
    st.button("Publish notice", type="primary", icon=":material/campaign:", on_click=publish, key="notice_publish")

with right:
    section_header("Preview", "How students will see it")
    title = notices_mod.clean_input(st.session_state["notice_title"])
    body = notices_mod.clean_input(st.session_state["notice_body"], multiline=True)
    if title or body:
        try:
            branches = notices_mod.normalize_branches(st.session_state["notice_branches"])
        except notices_mod.NoticeError:
            branches = []
        preview = {"id": "Draft", "title": title or "(no title)", "body": body,
                   "category": st.session_state["notice_category"], "branches": branches,
                   "published_at": None}
        st.markdown(f'<div class="sca-notices">{notice_card_html(preview)}</div>', unsafe_allow_html=True)
    else:
        empty_state("👀", "Nothing to preview yet", "Start typing a title or text to see the notice card.",
                    compact=True)

published = notices_mod.list_notices()
section_header("Published notices", f"{len(published)} notice{'s' if len(published) != 1 else ''}, newest first")
if not published:
    empty_state("📢", "No notices yet", "Published notices appear here and on students' dashboards.")
pending = st.session_state.get("notice_confirm")
for notice in published:
    nid = notice["id"]
    # The action sits right under its own card (as on the request pages): in a narrow column beside it
    # the label was cut to "D...". The small gap keeps card and button visibly together.
    with st.container(gap="xsmall"):
        st.markdown(f'<div class="sca-notices tight">{notice_card_html(notice)}</div>', unsafe_allow_html=True)
        st.button("Delete", key=f"notice_del_{nid}", icon=":material/delete:", on_click=ask_delete, args=(nid,),
                  disabled=pending == nid)
    if pending == nid:
        confirm_prompt(
            f"Delete {nid} \"{notice.get('title', '')}\"? Students will no longer see it and "
            "the assistant will stop using it.",
            yes_label="Yes, delete", yes_key=f"notice_yes_{nid}", on_yes=confirm_delete, yes_args=(nid,),
            no_key=f"notice_no_{nid}", on_no=cancel_delete,
        )
