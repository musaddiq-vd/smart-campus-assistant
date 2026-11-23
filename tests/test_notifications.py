"""Offline tests for student email notifications: outbox, dedupe, fee reminder rules and delivery."""

from __future__ import annotations

import json
from datetime import date

import pytest

from smart_campus_assistant import db, mailer, notifications, student_data
from smart_campus_assistant import support_requests as sr
from tests.test_personal import STUDENTS  # XYZ24CSE001 (CSE, Rs 35,000 due 2026-11-30), XYZ23ECE007 (ECE, overdue)

CSE, ECE = "XYZ24CSE001", "XYZ23ECE007"


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "students.json"
    path.write_text(json.dumps({"college": "XYZ", "as_of": "2026-11-03", "students": STUDENTS}), encoding="utf-8")
    monkeypatch.setattr(student_data, "_default_path", lambda: path)
    monkeypatch.delenv("SCA_EMAIL_NOTIFICATIONS", raising=False)
    student_data.clear_cache()
    file = tmp_path / "app.db"
    db.init_db(file)
    yield file
    student_data.clear_cache()


def add_account(db_file, student_id, email):
    """An account row without the slow password hashing (the hash is never checked here)."""
    with db.transaction(db_file) as conn:
        conn.execute(
            "INSERT INTO users (email, role, name, student_id, password_hash, created_at)"
            " VALUES (?, 'student', '', ?, 'x', ?)", (email, student_id, db.now_iso()))


class FakeSession:
    """Stand-in for mailer.smtp_session: records emails; addresses starting with "fail" are refused."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.sessions = 0

    def __call__(self):
        from contextlib import contextmanager

        @contextmanager
        def session():
            self.sessions += 1
            yield lambda to, subject, body: self.sent.append((to, subject, body)) or not to.startswith("fail")

        return session()


@pytest.fixture
def smtp(monkeypatch):
    """Pretend SMTP is configured and record every email instead of sending it."""
    fake = FakeSession()
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    monkeypatch.setattr(mailer, "smtp_session", fake)
    return fake


def record(student_id):
    return student_data.get_student(student_id)


# ---------------------------------------------------------------- outbox


def test_queue_dedupes_and_ignores_bad_addresses(db_file):
    assert notifications.queue("new_notice", "A@X.example", "s", "b", "k1", path=db_file)
    assert not notifications.queue("new_notice", "a@x.example", "s", "b", "k1", path=db_file)  # same key
    assert not notifications.queue("new_notice", "", "s", "b", "k2", path=db_file)
    assert not notifications.queue("new_notice", "not-an-email", "s", "b", "k3", path=db_file)
    rows = notifications.recent(path=db_file)
    assert len(rows) == 1 and rows[0]["recipient"] == "a@x.example" and rows[0]["status"] == "pending"
    with pytest.raises(ValueError):
        notifications.queue("spam", "a@x.example", "s", "b", "k4", path=db_file)


def test_switch_off_queues_nothing(db_file, monkeypatch):
    monkeypatch.setenv("SCA_EMAIL_NOTIFICATIONS", "0")
    assert not notifications.queue("new_notice", "a@x.example", "s", "b", "k1", path=db_file)
    assert notifications.counts(db_file)["total"] == 0


def test_without_smtp_emails_are_skipped_not_sent(db_file, monkeypatch, smtp):
    monkeypatch.setattr(mailer, "email_configured", lambda: False)
    notifications.queue("new_notice", "a@x.example", "s", "b", "k1", path=db_file)
    assert notifications.deliver_pending(db_file) == {"sent": 0, "skipped": 1, "retry": 0, "failed": 0}
    assert smtp.sent == []
    assert notifications.recent(path=db_file)[0]["status"] == "skipped"
    # Turning SMTP on later does not send old emails.
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    assert notifications.deliver_pending(db_file)["sent"] == 0 and smtp.sent == []


def test_delivery_uses_one_connection_and_sends_once(db_file, smtp):
    for i in range(3):
        notifications.queue("new_notice", f"s{i}@x.example", "s", "b", f"k{i}", path=db_file)
    assert notifications.deliver_pending(db_file)["sent"] == 3
    assert smtp.sessions == 1 and len(smtp.sent) == 3
    assert notifications.deliver_pending(db_file)["sent"] == 0 and len(smtp.sent) == 3
    assert all(r["sent_at"] for r in notifications.recent(path=db_file))


def test_failed_email_is_retried_then_given_up(db_file, smtp):
    notifications.queue("new_notice", "fail@x.example", "s", "b", "k1", path=db_file)
    t0 = 1_000_000.0
    assert notifications.deliver_pending(db_file, now=t0) == {"sent": 0, "skipped": 0, "retry": 1, "failed": 0}
    row = notifications.recent(path=db_file)[0]
    assert (row["status"], row["attempts"]) == ("pending", 1) and "Attempt 1 failed" in row["error"]
    # Not before its retry time (5 minutes), then once more (30 minutes), then it gives up.
    assert notifications.due_count(db_file, now=t0 + 60) == 0
    assert notifications.deliver_pending(db_file, now=t0 + 60)["retry"] == 0
    assert notifications.deliver_pending(db_file, now=t0 + 301)["retry"] == 1
    assert notifications.deliver_pending(db_file, now=t0 + 400)["retry"] == 0
    assert notifications.deliver_pending(db_file, now=t0 + 301 + 1801)["failed"] == 1
    row = notifications.recent(path=db_file)[0]
    assert (row["status"], row["attempts"]) == ("failed", 3) and len(smtp.sent) == 3
    assert notifications.due_count(db_file, now=t0 + 99_999) == 0


def test_retry_succeeds_on_a_later_attempt(db_file, smtp, monkeypatch):
    notifications.queue("new_notice", "fail@x.example", "s", "b", "k1", path=db_file)
    t0 = 1_000_000.0
    notifications.deliver_pending(db_file, now=t0)
    monkeypatch.setattr(mailer, "smtp_session", lambda: _always_ok_session(smtp.sent))
    assert notifications.deliver_pending(db_file, now=t0 + 301)["sent"] == 1
    assert notifications.recent(path=db_file)[0]["status"] == "sent"


def _always_ok_session(log):
    from contextlib import contextmanager

    @contextmanager
    def session():
        yield lambda to, subject, body: log.append((to, subject, body)) or True

    return session()


def test_email_stuck_in_sending_is_picked_up_again(db_file, smtp):
    notifications.queue("new_notice", "a@x.example", "s", "b", "k1", path=db_file)
    t0 = 1_000_000.0
    notifications._claim(db_file, 10, t0)  # the server stopped after claiming it
    assert notifications.recent(path=db_file)[0]["status"] == "sending"
    assert notifications.deliver_pending(db_file, now=t0 + 60)["sent"] == 0  # may still be sending
    assert notifications.deliver_pending(db_file, now=t0 + notifications.STALE_SENDING_SECONDS + 1)["sent"] == 1
    row = notifications.recent(path=db_file)[0]
    assert (row["status"], row["attempts"]) == ("sent", 2)


def test_email_stuck_too_often_is_failed(db_file, smtp):
    notifications.queue("new_notice", "a@x.example", "s", "b", "k1", path=db_file)
    t = 1_000_000.0
    for _ in range(notifications.MAX_ATTEMPTS):
        notifications._claim(db_file, 10, t)
        t += notifications.STALE_SENDING_SECONDS + 1
    assert notifications.deliver_pending(db_file, now=t)["sent"] == 0
    row = notifications.recent(path=db_file)[0]
    assert row["status"] == "failed" and "interrupted" in row["error"] and smtp.sent == []


def test_old_outbox_table_gets_the_new_columns(db_file, smtp):
    with db.transaction(db_file) as conn:
        conn.execute("CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
                     " dedupe_key TEXT NOT NULL UNIQUE, student_id TEXT NOT NULL DEFAULT '', recipient TEXT NOT NULL,"
                     " subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',"
                     " error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, sent_at TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO notifications (kind, dedupe_key, recipient, subject, body, created_at)"
                     " VALUES ('new_notice', 'old', 'a@x.example', 's', 'b', '2026-11-01T00:00:00+00:00')")
    assert notifications.deliver_pending(db_file)["sent"] == 1
    assert notifications.recent(path=db_file)[0]["attempts"] == 1


def test_queue_many_is_one_batch(db_file):
    emails = [{"kind": "new_notice", "recipient": f"s{i}@x.example", "subject": "s", "body": "b",
               "dedupe_key": f"k{i % 3}"} for i in range(5)]
    emails.append({"kind": "new_notice", "recipient": "nope", "subject": "s", "body": "b", "dedupe_key": "k9"})
    assert notifications.queue_many(emails, db_file) == 3  # k0, k1, k2; duplicates and bad address left out
    assert notifications.queue_many(emails, db_file) == 0
    assert notifications.queue_many([], db_file) == 0


def test_background_delivery(db_file, smtp):
    notifications.queue("new_notice", "a@x.example", "s", "b", "k1", path=db_file)
    notifications.deliver_in_background(db_file).join(timeout=10)
    assert [to for to, _, _ in smtp.sent] == ["a@x.example"]


# ---------------------------------------------------------------- request replies


def test_admin_reply_emails_the_owner_once(db_file):
    add_account(db_file, CSE, "aarav.login@xyz.example")
    request = sr.create_request(CSE, "old@xyz.example", "Aarav", "fees", "Fee receipt missing",
                                "My receipt was not generated.", path=db_file)
    assert not notifications.notify_request_reply(request, db_file)  # no admin reply yet
    request = sr.add_message(request["id"], "admin", "Office", "We fixed it, please check.", path=db_file)
    assert notifications.notify_request_reply(request, db_file)
    assert not notifications.notify_request_reply(request, db_file)  # same reply: not again
    row = notifications.recent(path=db_file)[0]
    assert row["recipient"] == "aarav.login@xyz.example"  # the account email wins over the request copy
    assert row["kind"] == "request_reply" and request["id"] in row["subject"]
    with db.reading(db_file) as conn:
        body = conn.execute("SELECT body FROM notifications").fetchone()["body"]
    assert "We fixed it, please check." in body and "in progress" in body
    # A second reply is a new email.
    request = sr.add_message(request["id"], "admin", "Office", "Anything else?", path=db_file)
    assert notifications.notify_request_reply(request, db_file)


def test_reply_falls_back_to_request_email_without_account(db_file):
    request = sr.create_request(ECE, "kabir@xyz.example", "Kabir", "hostel", "Room fan broken",
                                "The fan in my room does not work.", path=db_file)
    request = sr.add_message(request["id"], "admin", "Office", "Sent an electrician.", path=db_file)
    assert notifications.notify_request_reply(request, db_file)
    assert notifications.recent(path=db_file)[0]["recipient"] == "kabir@xyz.example"


# ---------------------------------------------------------------- notices


def test_notice_goes_only_to_its_branches_and_accounts(db_file):
    add_account(db_file, CSE, "cse@xyz.example")
    add_account(db_file, ECE, "ece@xyz.example")
    notice = {"id": "N-2026-0009", "title": "Lab closed", "body": "The CSE lab is closed on Friday.",
              "branches": ["CSE"], "published_at": "2026-11-03"}
    assert notifications.notify_new_notice(notice, db_file) == 1
    assert notifications.notify_new_notice(notice, db_file) == 0  # not twice
    assert [r["recipient"] for r in notifications.recent(path=db_file)] == ["cse@xyz.example"]
    everyone = {**notice, "id": "N-2026-0010", "branches": ["ALL"]}
    assert notifications.notify_new_notice(everyone, db_file) == 2


def test_notice_without_accounts_emails_nobody(db_file):
    notice = {"id": "N-1", "title": "t", "body": "b", "branches": ["ALL"], "published_at": "2026-11-03"}
    assert notifications.notify_new_notice(notice, db_file) == 0


# ---------------------------------------------------------------- fee reminders


@pytest.mark.parametrize("today, expected", [
    (date(2026, 11, 3), None),  # 27 days left: too early
    (date(2026, 11, 23), "due-soon"),  # 7 days left
    (date(2026, 11, 30), "due-soon"),  # due today
    (date(2026, 12, 1), "overdue"),
])
def test_fee_reminder_stages(db_file, today, expected):
    reminder = notifications.fee_reminder(record(CSE), today)
    assert (reminder[0].split(":")[0] if reminder else None) == expected


def test_overdue_reminder_has_amounts_computed_in_code(db_file):
    stage, subject, body = notifications.fee_reminder(record(ECE), date(2026, 11, 3))
    # Rs 55,000 due on 2026-10-31: 3 days late, Rs 100 per day.
    assert stage == "overdue:Odd 2026-27:w0"
    assert "3 days overdue" in body and "Rs 55,000" in body and "Rs 300" in body and "Rs 55,300" in body
    assert subject == "Fee overdue: Rs 55,300 payable now"


def test_nothing_due_means_no_reminder(db_file):
    paid = {**record(CSE), "fees": {**record(CSE)["fees"], "due": 0}}
    assert notifications.fee_reminder(paid, date(2026, 11, 29)) is None
    assert notifications.fee_reminder({"fees": {"due": "x"}}, date(2026, 11, 29)) is None


def test_fee_reminders_once_per_stage_and_week(db_file):
    add_account(db_file, CSE, "cse@xyz.example")
    add_account(db_file, ECE, "ece@xyz.example")
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 3)) == 1  # only ECE is overdue
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 4)) == 0  # same week
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 7)) == 1  # a week later
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 24)) == 2  # CSE due soon + ECE week 3
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 25)) == 0  # CSE: one due-soon email


def test_fee_reminders_skip_students_without_account(db_file):
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 3)) == 0


def test_cli_runs_with_and_without_date(db_file, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(notifications, "queue_fee_reminders", lambda today=None: seen.append(today) or 2)
    monkeypatch.setattr(notifications, "deliver_pending",
                        lambda: {"sent": 0, "skipped": 2, "retry": 0, "failed": 0})
    assert notifications.main(["fee-reminders"]) == 0
    out = capsys.readouterr().out
    assert "Queued 2 fee reminder(s) as of each student record's date" in out and "skipped 2" in out
    assert notifications.main(["fee-reminders", "--date", "2026-12-01"]) == 0
    assert seen == [None, date(2026, 12, 1)]
    assert "as of 2026-12-01" in capsys.readouterr().out


def test_cli_says_when_notifications_are_off(db_file, monkeypatch, capsys):
    monkeypatch.setenv("SCA_EMAIL_NOTIFICATIONS", "0")
    assert notifications.main(["fee-reminders"]) == 0
    assert "turned off" in capsys.readouterr().out


def test_fee_reminders_off_queue_nothing(db_file, monkeypatch):
    add_account(db_file, ECE, "ece@xyz.example")
    monkeypatch.setenv("SCA_EMAIL_NOTIFICATIONS", "0")
    assert notifications.queue_fee_reminders(db_file, today=date(2026, 11, 3)) == 0


def test_reminder_date_matches_the_records(db_file):
    assert notifications.reminder_date(record(CSE)) == date(2026, 11, 3)  # the file's as_of, like the dashboard
    assert notifications.reminder_date({}) == date.today()
    # Without an explicit date each record's own date is used.
    add_account(db_file, ECE, "ece@xyz.example")
    assert notifications.queue_fee_reminders(db_file) == 1
    assert "3 days overdue" in notifications.fee_reminder(record(ECE))[2]
