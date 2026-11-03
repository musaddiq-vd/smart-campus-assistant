# Data sources

All data in this project is **synthetic**. It describes a fictional college, **XYZ College of Engineering** (key `xyz`), with four branches (CSE, ECE, ME, CE). The college, people, student IDs, emails (domain `xyz.edu.example`), phone numbers, companies and amounts are made up. Any similarity to a real institution or person is accidental.

The data is "as of" 2026-11-03 (Odd semester 2026-27, before the end-semester exams).

## College documents (`data/college/*.md`)

Ingested by `python -m smart_campus_assistant.ingest` into the S3 Vectors index `xyz-college-docs`.

| File | doc_type | Content |
|---|---|---|
| about_and_admissions.md | admissions | About the college, intake, eligibility, admission process, reservation, student ID format |
| branches_and_programmes.md | branches | CSE, ECE, ME, CE: intake, HoDs, curriculum, labs, electives, minors |
| fee_structure.md | fees | Academic and hostel fee per semester by category, yearly cost, installments, late fee, payment modes, refunds |
| exam_notifications.md | exams | Mid-sem and end-sem notices, branch-wise end-sem timetable, hall tickets, revaluation, supplementary exams |
| academic_rules.md | rules | Credits, attendance and condonation, grading, SGPA/CGPA, backlogs, branch change |
| hostel_rules.md | hostel | Blocks, hostel fee, allotment, timings, mess, fines |
| library_rules.md | library | Timings, borrowing limits, loan period, fines, lost books |
| scholarships.md | scholarships | Merit, need-based, SC/ST, Girls in STEM, sports scholarships |
| placements_2026.md | placements | Branch-wise placement statistics for the 2026 batch, policy |
| important_dates_and_contacts.md | calendar | Academic calendar 2026-27 and office contacts |

## Student records (`data/students.json`)

20 synthetic students across the four branches and years, read by `student_data.py` for the "personal" route. Each record holds hostel, fees (with transactions and scholarship), library books and fine, attendance, results and upcoming exams. The records are consistent with the fee structure, library rules and the end-sem timetable.

## Old data (no longer used)

The first version of the project used official IIT Delhi, IIT Bombay and NIT Trichy PDFs. That data and its S3 Vectors index (`college-docs`) were removed on 2026-11-03; only the synthetic data above is used now.
