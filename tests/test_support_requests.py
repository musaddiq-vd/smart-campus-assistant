"""Offline tests for support requests (helpdesk tickets): backend rules, HTML helpers and the two pages."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smart_campus_assistant import db  # noqa: E402
from smart_campus_assistant import support_requests as sr  # noqa: E402

A = ("XYZ24CSE001", "aarav@xyz.edu.example", "Aarav Sharma")
B = ("XYZ26ECE009", "diya@xyz.edu.example", "Diya Patel")
BODY = "My fee receipt for the odd semester was not generated."


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "app.db"


def _make(db_file, who=A, category="fees", subject="Fee receipt missing", body=BODY):
    return sr.create_request(*who, category, subject, body, path=db_file)


# ------------------------------------------------------------ create


def test_create_request_happy_path(db_file):
    request = _make(db_file)
    assert request["id"].startswith("R-") and request["id"].endswith("-0001")
    assert request["id"][2:6] == request["created_at"][:4]
    assert request["status"] == "open"
    assert request["student_id"] == "XYZ24CSE001"
    assert request["student_email"] == "aarav@xyz.edu.example"
    assert request["student_name"] == "Aarav Sharma"
    assert request["category"] == "fees"
    assert request["subject"] == "Fee receipt missing"
    assert request["body"] == BODY
    assert request["created_at"] == request["updated_at"]
    assert request["messages"] == []
    assert sr.get_request(request["id"], path=db_file) == request


def test_create_normalises_identity_and_category(db_file):
    request = sr.create_request(" xyz24cse001 ", " Aarav@XYZ.edu.example ", "  Aarav   Sharma ", " FEES ",
                                "Fee receipt missing", BODY, path=db_file)
    assert request["student_id"] == "XYZ24CSE001"
    assert request["student_email"] == "aarav@xyz.edu.example"
    assert request["student_name"] == "Aarav Sharma"
    assert request["category"] == "fees"


def test_ids_are_a_running_sequence_across_students(db_file):
    ids = [_make(db_file)["id"], _make(db_file, who=B)["id"], _make(db_file)["id"]]
    assert [i[-4:] for i in ids] == ["0001", "0002", "0003"]
    assert len(set(ids)) == 3


def test_sequence_continues_after_closing(db_file):
    first = _make(db_file)
    sr.close_own_request(first["id"], A[0], path=db_file)
    assert _make(db_file)["id"].endswith("-0002")


@pytest.mark.parametrize("subject, ok", [
    ("abcd", False), ("abcde", True), ("x" * 120, True), ("x" * 121, False), ("", False), ("   a   ", False),
])
def test_subject_limits(db_file, subject, ok):
    if ok:
        assert _make(db_file, subject=subject)["subject"] == subject
    else:
        with pytest.raises(sr.RequestError, match="Subject must be 5-120"):
            _make(db_file, subject=subject)


@pytest.mark.parametrize("body, ok", [
    ("x" * 9, False), ("x" * 10, True), ("x" * 2000, True), ("x" * 2001, False), (None, False),
])
def test_body_limits(db_file, body, ok):
    if ok:
        assert _make(db_file, body=body)["body"] == body
    else:
        with pytest.raises(sr.RequestError, match="Details must be 10-2000"):
            _make(db_file, body=body)


@pytest.mark.parametrize("category", ["", None, "general", "fees; DROP TABLE support_requests", "<b>fees</b>"])
def test_unknown_category_rejected(db_file, category):
    with pytest.raises(sr.RequestError, match="Unknown category"):
        _make(db_file, category=category)
    assert sr.list_requests(path=db_file) == []


def test_every_category_is_accepted(db_file):
    assert set(sr.CATEGORIES) == {"fees", "hostel", "library", "exams", "certificates", "other"}
    for who, categories in ((A, list(sr.CATEGORIES)[:3]), (B, list(sr.CATEGORIES)[3:])):
        for category in categories:
            assert _make(db_file, who=who, category=category)["category"] == category


def test_missing_student_id_rejected(db_file):
    for sid in ("", None, "   "):
        with pytest.raises(sr.RequestError, match="signed-in student"):
            sr.create_request(sid, "x@y.example", "X", "fees", "Fee receipt missing", BODY, path=db_file)


def test_html_and_control_characters_are_stripped(db_file):
    request = sr.create_request(
        A[0], A[1], "<b>Aarav</b> Sharma", "fees",
        "<script>alert(1)</script>Fee <i>receipt</i>\x00 missing",
        "Line one <img src=x onerror=alert(1)>\r\n\r\n\r\n\r\nLine   two\x07 &lt;b&gt;bold&lt;/b&gt;",
        path=db_file,
    )
    assert request["subject"] == "alert(1)Fee receipt missing"
    assert request["body"] == "Line one\n\nLine two bold"
    assert request["student_name"] == "Aarav Sharma"
    for value in (request["subject"], request["body"], request["student_name"]):
        assert "<" not in value and ">" not in value
    message = sr.add_message(request["id"], "admin", "<u>Office</u>", "<p>Please   visit</p> room 4",
                             path=db_file)["messages"][0]
    assert message["author_name"] == "Office"
    assert message["body"] == "Please visit room 4"


def test_text_that_is_only_markup_fails_the_length_check(db_file):
    with pytest.raises(sr.RequestError, match="Subject"):
        _make(db_file, subject="<div><span></span></div>")
    request = _make(db_file)
    with pytest.raises(sr.RequestError, match="Message must be 1-2000"):
        sr.add_message(request["id"], "admin", "Office", "<br><br>", path=db_file)


def test_sql_metacharacters_are_stored_as_text(db_file):
    subject = "Robert'); DROP TABLE support_requests;--"
    request = _make(db_file, subject=subject)
    assert sr.get_request(request["id"], path=db_file)["subject"] == subject
    assert len(sr.list_requests(path=db_file)) == 1


# ------------------------------------------------------------ the 5-open limit


def test_at_most_five_not_closed_requests_per_student(db_file):
    made = [_make(db_file, subject=f"Request number {n}") for n in range(5)]
    with pytest.raises(sr.RequestError, match="already have 5"):
        _make(db_file)
    assert len(sr.list_requests(student_id=A[0], path=db_file)) == 5
    # Another student is not affected.
    assert _make(db_file, who=B)["student_id"] == B[0]
    # Resolved still counts (it is not closed); closed frees a slot.
    sr.set_status(made[0]["id"], "resolved", path=db_file)
    with pytest.raises(sr.RequestError, match="already have 5"):
        _make(db_file)
    sr.close_own_request(made[0]["id"], A[0], path=db_file)
    assert _make(db_file)["status"] == "open"
    with pytest.raises(sr.RequestError, match="already have 5"):
        _make(db_file)


# ------------------------------------------------------------ list / search / filters


@pytest.fixture
def queue(db_file):
    """Four requests: A fees (in progress), A hostel (open), B library (resolved), B exams (closed)."""
    r1 = _make(db_file, A, "fees", "Fee receipt missing")
    r2 = _make(db_file, A, "hostel", "Hostel room 100% damp_wall")
    r3 = _make(db_file, B, "library", "Library fine is wrong")
    r4 = _make(db_file, B, "exams", "Exam hall ticket not issued")
    sr.add_message(r1["id"], "admin", "Office", "Looking into it.", path=db_file)
    sr.set_status(r3["id"], "resolved", path=db_file)
    sr.set_status(r4["id"], "closed", path=db_file)
    return db_file, [r1["id"], r2["id"], r3["id"], r4["id"]]


def _ids(rows):
    return [r["id"] for r in rows]


def test_list_is_newest_first_and_has_no_messages(queue):
    db_file, ids = queue
    rows = sr.list_requests(path=db_file)
    assert _ids(rows) == list(reversed(ids))
    assert all("messages" not in r for r in rows)


def test_list_for_one_student_only(queue):
    db_file, ids = queue
    assert _ids(sr.list_requests(student_id=A[0], path=db_file)) == [ids[1], ids[0]]
    assert _ids(sr.list_requests(student_id="xyz26ece009", path=db_file)) == [ids[3], ids[2]]
    assert sr.list_requests(student_id="XYZ00XXX000", path=db_file) == []
    # An empty student id means "nobody", never "everybody".
    assert sr.list_requests(student_id="", path=db_file) == []


def test_filter_by_status_and_category(queue):
    db_file, ids = queue
    assert _ids(sr.list_requests(status="open", path=db_file)) == [ids[1]]
    assert _ids(sr.list_requests(status="in_progress", path=db_file)) == [ids[0]]
    assert _ids(sr.list_requests(status="resolved", path=db_file)) == [ids[2]]
    assert _ids(sr.list_requests(status="closed", path=db_file)) == [ids[3]]
    assert _ids(sr.list_requests(category="library", path=db_file)) == [ids[2]]
    assert sr.list_requests(category="certificates", path=db_file) == []
    assert sr.list_requests(status="open", category="fees", path=db_file) == []
    assert _ids(sr.list_requests(student_id=B[0], status="closed", category="exams", path=db_file)) == [ids[3]]
    with pytest.raises(sr.RequestError, match="Unknown status"):
        sr.list_requests(status="pending", path=db_file)
    with pytest.raises(sr.RequestError, match="Unknown category"):
        sr.list_requests(category="canteen", path=db_file)


def test_search_subject_student_and_id(queue):
    db_file, ids = queue
    assert _ids(sr.list_requests(search="RECEIPT", path=db_file)) == [ids[0]]
    assert _ids(sr.list_requests(search="diya", path=db_file)) == [ids[3], ids[2]]
    assert _ids(sr.list_requests(search="xyz24cse", path=db_file)) == [ids[1], ids[0]]
    assert _ids(sr.list_requests(search="aarav@xyz", path=db_file)) == [ids[1], ids[0]]
    assert _ids(sr.list_requests(search=ids[2], path=db_file)) == [ids[2]]
    assert sr.list_requests(search="no such thing", path=db_file) == []
    assert len(sr.list_requests(search="   ", path=db_file)) == 4
    # Search never widens a student's own list.
    assert sr.list_requests(student_id=A[0], search="library", path=db_file) == []


def test_search_treats_like_wildcards_as_text(queue):
    db_file, ids = queue
    assert _ids(sr.list_requests(search="100%", path=db_file)) == [ids[1]]
    assert _ids(sr.list_requests(search="damp_wall", path=db_file)) == [ids[1]]
    assert _ids(sr.list_requests(search="%", path=db_file)) == [ids[1]]  # a literal "%", not "match everything"
    assert _ids(sr.list_requests(search="_", path=db_file)) == [ids[1]]
    assert sr.list_requests(search="damp wall", path=db_file) == []
    assert sr.list_requests(search="' OR 1=1 --", path=db_file) == []


def test_counts(queue, tmp_path):
    db_file, _ = queue
    assert sr.counts(path=db_file) == {"open": 1, "in_progress": 1, "resolved": 1, "closed": 1, "total": 4}
    assert sr.counts(path=tmp_path / "empty.db") == {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0,
                                                     "total": 0}


def test_reads_work_on_a_fresh_database(tmp_path):
    fresh = tmp_path / "fresh.db"
    assert sr.list_requests(path=fresh) == []
    assert sr.get_request("R-2026-0001", path=fresh) is None


# ------------------------------------------------------------ isolation


def test_student_cannot_read_another_students_request(db_file):
    mine = _make(db_file)
    assert sr.get_request(mine["id"], student_id=A[0], path=db_file)["id"] == mine["id"]
    assert sr.get_request(mine["id"], student_id="xyz24cse001", path=db_file)["id"] == mine["id"]
    assert sr.get_request(mine["id"], student_id=B[0], path=db_file) is None
    # An empty id is "a student with no id", not "an admin".
    assert sr.get_request(mine["id"], student_id="", path=db_file) is None
    assert sr.list_requests(student_id=B[0], path=db_file) == []


def test_student_cannot_reply_on_another_students_request(db_file):
    mine = _make(db_file)
    with pytest.raises(sr.RequestError, match="was not found"):
        sr.add_message(mine["id"], "student", B[2], "Let me in.", student_id=B[0], path=db_file)
    with pytest.raises(sr.RequestError, match="signed-in student"):
        sr.add_message(mine["id"], "student", B[2], "No id given.", path=db_file)
    with pytest.raises(sr.RequestError, match="signed-in student"):
        sr.add_message(mine["id"], "student", B[2], "Blank id.", student_id=" ", path=db_file)
    assert sr.get_request(mine["id"], path=db_file)["messages"] == []


def test_student_cannot_close_another_students_request(db_file):
    mine = _make(db_file)
    with pytest.raises(sr.RequestError, match="was not found"):
        sr.close_own_request(mine["id"], B[0], path=db_file)
    with pytest.raises(sr.RequestError, match="signed-in student"):
        sr.close_own_request(mine["id"], "", path=db_file)
    assert sr.get_request(mine["id"], path=db_file)["status"] == "open"


def test_foreign_and_unknown_ids_give_the_same_error(db_file):
    mine = _make(db_file)
    with pytest.raises(sr.RequestError) as foreign:
        sr.close_own_request(mine["id"], B[0], path=db_file)
    with pytest.raises(sr.RequestError) as unknown:
        sr.close_own_request("R-2026-9999", B[0], path=db_file)
    assert str(foreign.value).replace(mine["id"], "X") == str(unknown.value).replace("R-2026-9999", "X")


# ------------------------------------------------------------ replies and status transitions


def test_thread_order_and_authors(db_file):
    request = _make(db_file)
    sr.add_message(request["id"], "admin", "XYZ Admin Office", "Which receipt number?", path=db_file)
    sr.add_message(request["id"], "student", A[2], "Receipt R-17.", student_id=A[0], path=db_file)
    updated = sr.add_message(request["id"], "admin", "", "Fixed, please check.", path=db_file)
    messages = updated["messages"]
    assert [m["author_role"] for m in messages] == ["admin", "student", "admin"]
    assert [m["body"] for m in messages] == ["Which receipt number?", "Receipt R-17.", "Fixed, please check."]
    assert [m["author_name"] for m in messages] == ["XYZ Admin Office", "Aarav Sharma", "Admin office"]
    assert all(m["request_id"] == request["id"] and m["created_at"] for m in messages)
    assert [m["id"] for m in messages] == sorted(m["id"] for m in messages)
    assert sr.get_request(request["id"], student_id=A[0], path=db_file)["messages"] == messages


def test_admin_reply_moves_open_to_in_progress(db_file):
    request = _make(db_file)
    assert sr.add_message(request["id"], "admin", "Office", "On it.", path=db_file)["status"] == "in_progress"
    # A second reply, and a student reply, leave "in progress" alone.
    assert sr.add_message(request["id"], "admin", "Office", "Still on it.", path=db_file)["status"] == "in_progress"
    assert sr.add_message(request["id"], "student", A[2], "Thanks.", student_id=A[0],
                          path=db_file)["status"] == "in_progress"


def test_student_reply_keeps_open_request_open(db_file):
    request = _make(db_file)
    assert sr.add_message(request["id"], "student", A[2], "One more detail.", student_id=A[0],
                          path=db_file)["status"] == "open"


def test_student_reply_reopens_a_resolved_request(db_file):
    request = _make(db_file)
    sr.set_status(request["id"], "resolved", path=db_file)
    # An admin note on a resolved request does not reopen it.
    assert sr.add_message(request["id"], "admin", "Office", "Receipt emailed.", path=db_file)["status"] == "resolved"
    assert sr.add_message(request["id"], "student", A[2], "Still not received.", student_id=A[0],
                          path=db_file)["status"] == "open"


def test_closed_request_rejects_replies_from_everyone(db_file):
    request = _make(db_file)
    sr.close_own_request(request["id"], A[0], path=db_file)
    with pytest.raises(sr.RequestError, match="is closed"):
        sr.add_message(request["id"], "student", A[2], "One more thing.", student_id=A[0], path=db_file)
    with pytest.raises(sr.RequestError, match="is closed"):
        sr.add_message(request["id"], "admin", "Office", "Late reply.", path=db_file)
    assert sr.get_request(request["id"], path=db_file)["messages"] == []
    # An admin can reopen it, then replies work again.
    sr.set_status(request["id"], "open", path=db_file)
    assert len(sr.add_message(request["id"], "admin", "Office", "Reopened.", path=db_file)["messages"]) == 1


@pytest.mark.parametrize("body, ok", [("", False), ("   ", False), ("k", True), ("x" * 2000, True), ("x" * 2001, False)])
def test_message_limits(db_file, body, ok):
    request = _make(db_file)
    if ok:
        assert sr.add_message(request["id"], "admin", "Office", body, path=db_file)["messages"][0]["body"] == body
    else:
        with pytest.raises(sr.RequestError, match="Message must be 1-2000"):
            sr.add_message(request["id"], "admin", "Office", body, path=db_file)
        assert sr.get_request(request["id"], path=db_file)["status"] == "open"


@pytest.mark.parametrize("role", ["", None, "teacher", "ADMINISTRATOR", "root"])
def test_unknown_author_role_rejected(db_file, role):
    request = _make(db_file)
    with pytest.raises(sr.RequestError, match="Unknown author role"):
        sr.add_message(request["id"], role, "Someone", "Hello there.", path=db_file)


def test_set_status_every_transition_and_unknown_status(db_file):
    request = _make(db_file)
    for status in ("in_progress", "resolved", "closed", "open", "resolved", "resolved"):
        assert sr.set_status(request["id"], status, path=db_file)["status"] == status
    assert sr.set_status(request["id"], " CLOSED ", path=db_file)["status"] == "closed"
    for bad in ("", None, "done", "deleted"):
        with pytest.raises(sr.RequestError, match="Unknown status"):
            sr.set_status(request["id"], bad, path=db_file)
    assert sr.get_request(request["id"], path=db_file)["status"] == "closed"


def test_close_own_request_is_idempotent(db_file):
    request = _make(db_file)
    assert sr.close_own_request(request["id"], "xyz24cse001", path=db_file)["status"] == "closed"
    assert sr.close_own_request(request["id"], A[0], path=db_file)["status"] == "closed"


def test_updated_at_changes_with_activity(db_file, monkeypatch):
    db.init_db(db_file)  # the first connect stamps its own rows; do that before freezing the clock
    stamps = iter(f"2026-11-03T10:0{n}:00+00:00" for n in range(9))
    monkeypatch.setattr(db, "now_iso", lambda: next(stamps))
    request = _make(db_file)
    assert request["id"] == "R-2026-0001"
    assert request["created_at"] == request["updated_at"] == "2026-11-03T10:00:00+00:00"
    replied = sr.add_message(request["id"], "admin", "Office", "On it.", path=db_file)
    assert replied["created_at"] == "2026-11-03T10:00:00+00:00"
    assert replied["updated_at"] == replied["messages"][0]["created_at"] == "2026-11-03T10:01:00+00:00"
    assert sr.set_status(request["id"], "resolved", path=db_file)["updated_at"] > replied["updated_at"]


# ------------------------------------------------------------ unknown ids


@pytest.mark.parametrize("request_id", ["R-2026-9999", "", None, "nope", "R-2026-0001' OR '1'='1"])
def test_unknown_ids(db_file, request_id):
    _make(db_file)
    assert sr.get_request(request_id, path=db_file) is None
    with pytest.raises(sr.RequestError, match="was not found"):
        sr.add_message(request_id, "admin", "Office", "Hello there.", path=db_file)
    with pytest.raises(sr.RequestError, match="was not found"):
        sr.set_status(request_id, "resolved", path=db_file)
    with pytest.raises(sr.RequestError, match="was not found"):
        sr.close_own_request(request_id, A[0], path=db_file)


def test_request_id_lookup_ignores_case_and_spaces(db_file):
    request = _make(db_file)
    assert sr.get_request(f"  {request['id'].lower()} ", path=db_file)["id"] == request["id"]


def test_error_messages_do_not_echo_markup(db_file):
    with pytest.raises(sr.RequestError) as err:
        sr.set_status("<script>alert(1)</script>", "resolved", path=db_file)
    assert "<" not in str(err.value)
    with pytest.raises(sr.RequestError) as err:
        _make(db_file, category="<img src=x>")
    assert "<" not in str(err.value)


def test_request_error_is_a_value_error():
    assert issubclass(sr.RequestError, ValueError)


def test_data_survives_other_tables_and_reconnects(db_file):
    request = _make(db_file)
    db.init_db(db_file)  # the app's own schema setup must not disturb our tables
    with db.reading(db_file) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"support_requests", "support_request_messages", "users"} <= tables
    assert sr.get_request(request["id"], path=db_file)["subject"] == "Fee receipt missing"


# ------------------------------------------------------------ HTML helpers


def test_status_chip_html():
    assert sr.status_chip_html("in_progress") == (
        '<span class="sca-chip req-in_progress"><span class="dot"></span>In progress</span>'
    )
    assert "req-open" in sr.status_chip_html('"><script>')
    assert "<script>" not in sr.status_chip_html('"><script>')


def test_nice_time():
    assert sr.nice_time("2026-11-03T10:15:00+00:00") == "03 Nov 2026, 10:15 UTC"
    assert sr.nice_time(None) == ""
    assert sr.nice_time("soon") == "soon"


def test_request_card_html_escapes_and_shows_student_only_for_admins():
    request = {
        "id": "R-2026-0007", "student_id": "XYZ24CSE001", "student_email": "a@xyz.example",
        "student_name": "A <b>Sharma</b>", "category": "hostel", "subject": "<script>alert(1)</script>",
        "status": "resolved", "created_at": "2026-11-03T10:15:00+00:00", "updated_at": "2026-11-04T09:00:00+00:00",
    }
    card = sr.request_card_html(request)
    assert "<script>" not in card and "&lt;script&gt;alert(1)&lt;/script&gt;" in card
    assert 'class="sca-req resolved"' in card and "Resolved" in card
    assert "R-2026-0007 · Hostel · raised 03 Nov 2026, 10:15 UTC · updated 04 Nov 2026, 09:00 UTC" in card
    assert "XYZ24CSE001" not in card and "a@xyz.example" not in card
    admin_card = sr.request_card_html(request, show_student=True)
    assert "A &lt;b&gt;Sharma&lt;/b&gt; · XYZ24CSE001 · a@xyz.example" in admin_card
    assert "<b>Sharma" not in admin_card
    # Unexpected values fall back instead of leaking into class names.
    odd = sr.request_card_html({"status": '"><i>', "category": '"><i>', "subject": "Odd"})
    assert 'class="sca-req open"' in odd and "<i>" not in odd


def test_thread_html_orders_escapes_and_marks_own_messages():
    request = {
        "student_name": "Aarav", "body": "First <b>line</b>\nsecond", "created_at": "2026-11-03T10:15:00+00:00",
        "messages": [
            {"author_role": "admin", "author_name": "Office", "body": "<img src=x>", "created_at": "2026-11-03T11:00:00+00:00"},
            {"author_role": "student", "author_name": "", "body": "Thanks", "created_at": "2026-11-03T12:00:00+00:00"},
            {"author_role": "hacker", "author_name": "Mallory", "body": "Hi", "created_at": None},
        ],
    }
    as_student = sr.thread_html(request, viewer_role="student")
    assert "<b>line" not in as_student and "<img" not in as_student
    assert "First &lt;b&gt;line&lt;/b&gt;\nsecond" in as_student
    assert as_student.index("First") < as_student.index("&lt;img") < as_student.index("Thanks")
    assert as_student.count('class="sca-msg student mine"') == 3  # original + reply + unknown role shown as student
    assert as_student.count('class="sca-msg admin"') == 1
    as_admin = sr.thread_html(request, viewer_role="admin")
    assert as_admin.count('class="sca-msg admin mine"') == 1
    assert as_admin.count('class="sca-msg student"') == 3
    assert sr.thread_html({}).count("sca-msg") == 1


# ------------------------------------------------------------ pages (Streamlit AppTest, temp database)

STUDENT_A = {"email": A[1], "role": "student", "name": A[2], "student_id": A[0]}
STUDENT_B = {"email": B[1], "role": "student", "name": B[2], "student_id": B[0]}
ADMIN = {"email": "admin@xyz.edu.example", "role": "admin", "name": "XYZ Admin Office", "student_id": None}


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """Point the default database (what the pages use) at a temp file."""
    target = tmp_path / "pages.db"
    monkeypatch.setattr(db, "db_path", lambda path=None: Path(path) if path else target)
    return target


def _page(name, user):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "ui" / name), default_timeout=30)
    if user is not None:
        at.session_state["user"] = dict(user)
    return at.run()


def _click(at, label):
    button = next(b for b in at.button if b.label == label)
    return button.click().run()


def _markdown(at):
    return "\n".join(str(m.value) for m in at.markdown)


def _alerts(at):
    return [str(e.value) for kind in ("error", "warning", "success", "info") for e in getattr(at, kind)]


@pytest.mark.parametrize("page, user", [
    ("student_requests.py", None), ("student_requests.py", ADMIN),
    ("admin_requests.py", None), ("admin_requests.py", STUDENT_A),
])
def test_pages_refuse_the_wrong_role(app_db, page, user):
    _make(app_db, subject="Secret fee question")
    at = _page(page, user)
    assert not at.exception
    assert any("do not have access" in str(e.value) for e in at.error)
    assert "Secret fee question" not in _markdown(at)
    assert not at.text_area and not at.button


def test_student_page_raise_open_reply_close(app_db):
    _make(app_db, who=B, subject="Diya private request")
    at = _page("student_requests.py", STUDENT_A)
    assert not at.exception
    assert "No requests yet" in _markdown(at) and "Diya private request" not in _markdown(at)

    # Invalid input: nothing saved, the typed text stays in the form.
    at.text_input(key="req_new_subject").set_value("Hey")
    at.text_area(key="req_new_body").set_value(BODY)
    at = _click(at, "Send request")
    assert not at.exception
    assert any("Subject must be 5-120" in text for text in _alerts(at))
    assert at.text_area(key="req_new_body").value == BODY
    assert sr.list_requests(student_id=A[0], path=app_db) == []

    at.selectbox(key="req_new_category").set_value("hostel")
    at.text_input(key="req_new_subject").set_value("Room <b>fan</b> broken")
    at = _click(at, "Send request")
    assert not at.exception
    (saved,) = sr.list_requests(student_id=A[0], path=app_db)
    assert saved["subject"] == "Room fan broken" and saved["category"] == "hostel" and saved["student_email"] == A[1]
    assert at.text_input(key="req_new_subject").value == ""
    assert "Room fan broken" in _markdown(at) and "Diya private request" not in _markdown(at)

    at = at.button(key=f"req_open_{saved['id']}").click().run()
    assert not at.exception
    assert "sca-thread" in _markdown(at)
    sr.add_message(saved["id"], "admin", "Office", "Electrician booked.", path=app_db)
    at.text_area(key=f"req_reply_{saved['id']}").set_value("Thank <i>you</i>")
    at = _click(at, "Send reply")
    assert not at.exception
    assert [m["body"] for m in sr.get_request(saved["id"], path=app_db)["messages"]] == ["Electrician booked.", "Thank you"]
    assert "Electrician booked." in _markdown(at)
    assert at.text_area(key=f"req_reply_{saved['id']}").value == ""

    # Closing needs a confirm step; Cancel keeps it open.
    at = at.button(key=f"req_close_{saved['id']}").click().run()
    assert sr.get_request(saved["id"], path=app_db)["status"] == "in_progress"
    at = _click(at, "Cancel")
    assert sr.get_request(saved["id"], path=app_db)["status"] == "in_progress"
    at = at.button(key=f"req_close_{saved['id']}").click().run()
    at = _click(at, "Yes, close")
    assert not at.exception
    assert sr.get_request(saved["id"], path=app_db)["status"] == "closed"
    assert not at.text_area  # no reply box on a closed request


def test_student_page_cannot_open_someone_elses_request(app_db):
    other = _make(app_db, who=B, subject="Diya private request")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "ui" / "student_requests.py"), default_timeout=30)
    at.session_state["user"] = dict(STUDENT_A)
    at.session_state["req_open_id"] = other["id"]  # forged selection
    at.run()
    assert not at.exception
    assert "Diya private request" not in _markdown(at) and BODY not in _markdown(at)
    assert any("not available" in text for text in _alerts(at))
    assert "req_open_id" not in at.session_state


def test_student_page_hides_the_form_at_the_limit(app_db):
    for n in range(sr.MAX_ACTIVE_PER_STUDENT):
        _make(app_db, subject=f"Request number {n}")
    at = _page("student_requests.py", STUDENT_A)
    assert not at.exception
    assert not at.text_input and any("limit is 5" in text for text in _alerts(at))


def test_admin_page_queue_filters_reply_and_status(app_db):
    first = _make(app_db, A, "fees", "Fee receipt missing")
    second = _make(app_db, B, "library", "Library <fine> is wrong")
    at = _page("admin_requests.py", ADMIN)
    assert not at.exception
    page = _markdown(at)
    assert "Fee receipt missing" in page and "Library  is wrong" not in page and "Diya Patel" in page
    assert "sca-kpis" in page

    at.selectbox(key="req_f_category").set_value("library")
    at = _click(at, "Apply filters")
    assert second["subject"] in _markdown(at) and "Fee receipt missing" not in _markdown(at)
    at.text_input(key="req_f_search").set_value("nobody")
    at = _click(at, "Apply filters")
    assert "No requests here" in _markdown(at)
    at = _click(at, "Reset")
    assert not at.exception
    assert "Fee receipt missing" in _markdown(at) and at.text_input(key="req_f_search").value == ""

    at = at.button(key=f"req_admin_open_{first['id']}").click().run()
    assert not at.exception
    assert A[1] in _markdown(at) and BODY in _markdown(at)
    at.text_area(key=f"req_admin_reply_{first['id']}").set_value("We are checking the <b>ledger</b>.")
    at = _click(at, "Send reply")
    assert not at.exception
    after = sr.get_request(first["id"], path=app_db)
    assert after["status"] == "in_progress"
    assert after["messages"][0]["body"] == "We are checking the ledger."
    assert after["messages"][0]["author_name"] == "XYZ Admin Office"

    at = _click(at, "Mark resolved")
    assert sr.get_request(first["id"], path=app_db)["status"] == "resolved"

    # Closing needs a confirm step.
    at = at.button(key=f"req_admin_close_{first['id']}").click().run()
    assert sr.get_request(first["id"], path=app_db)["status"] == "resolved"
    at = _click(at, "Yes, close")
    assert not at.exception
    assert sr.get_request(first["id"], path=app_db)["status"] == "closed"
    assert not at.text_area
    at = _click(at, "Reopen")
    assert sr.get_request(first["id"], path=app_db)["status"] == "open"
    assert sr.get_request(second["id"], path=app_db)["status"] == "open"  # the other request was never touched
