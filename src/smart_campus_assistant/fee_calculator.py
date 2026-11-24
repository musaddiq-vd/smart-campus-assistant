"""Fee totals computed in code from the fee tables, handed to the answer model as a citable block.

The answer model copies numbers well and adds them up badly (it drops a lakh, or adds the hostel
fee twice although the published bill already includes it). So when a question asks for a total
or a cost, the per-semester bill, the yearly cost and the one-time charges for the categories and
hostel option the question names (or, for a signed-in student, their own) are worked out here and
put in front of the retrieved documents as one block labelled "computed fee totals". The prompts
tell the model to copy those totals and cite the block.

Every amount comes from the tables in ``fee_structure.md`` (and the scholarship summary table in
``scholarships.md``). Published totals win over computed sums, so the block never contradicts the
document. The files are parsed once and parsed again only when they change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document

from smart_campus_assistant.config import BRANCHES, COLLEGE_KEY, get_settings
from smart_campus_assistant.student_data import PROGRAMME_SEMESTERS, format_rupees

FEE_FILE = "fee_structure.md"
SCHOLARSHIP_FILE = "scholarships.md"
DOC_TYPE = "computed fee totals"  # shown in the context label, e.g. "(XYZ ... | computed fee totals | fee_structure.md)"

# ---------------------------------------------------------------- which questions get the block

# A sum, a cost or a period total (English and Hinglish). Wider than prompts.TOTAL_QUESTION_RE: a yearly
# or four-year amount is a total even when the question does not say "total".
TOTAL_RE = re.compile(
    r"\b(totals?|including|inclusive|incl|plus|along with|together with|costs?|kharcha|kharch|kul|saath|"
    r"milake|milakar|mila ke|jod\w*|altogether|combined|sum|bill|yearly|annual|annually|per year|a year|"
    r"one year|per annum|ek saal|saal|four[- ]years?|4[- ]years?|full course|whole course|entire course|"
    r"poora|pura|budget|afford)\b",
    flags=re.IGNORECASE,
)
FEE_RE = re.compile(
    r"\b(fees?|costs?|kharcha|kharch|bill|tuition|hostel|mess|deposit|shulk|charges?|paisa|paise|rupees|"
    r"budget|lakh|amount|pay|payable|afford)\b",
    flags=re.IGNORECASE,
)
# Money questions the fee tables do not answer; they get the block only when a bill topic is named too.
_OTHER_MONEY_RE = re.compile(r"\b(late fee|fines?|refunds?|penalty|convenience fee|revaluation|supplementary)\b")
_BILL_TOPIC_RE = re.compile(r"\b(hostel|tuition|bill|yearly|annual|year|saal|costs?|kharcha)\b")

_CATEGORY_RES = (
    ("open", re.compile(r"\b(open|general|gen|unreserved)\b")),
    ("obc", re.compile(r"\b(obc(?:-?ncl)?|ncl|ews)\b")),
    ("sc", re.compile(r"\b(sc|st|scheduled (?:castes?|tribes?))\b")),
)
_DAY_RE = re.compile(
    r"\b(day[- ]?scholars?|without (?:the |a )?hostel|bina hostel|hostel ke bina|no hostel|"
    r"not (?:staying )?in (?:the |a )?hostel|ghar se|local students?)\b"
)
_RESIDENT_RE = re.compile(r"\b(hostels?|hostell?ers?|residents?|boarders?|mess)\b")
_FOUR_YEAR_RE = re.compile(
    r"\b(four[- ]years?|4[- ]years?|4 saal|char saal|full (?:course|programme|program|b\.?tech)|whole (?:course|"
    r"programme|program|b\.?tech)|entire (?:course|programme|program|b\.?tech)|poor[ae] course|pur[ae] course)\b"
)
# Words that ask for the whole bill (academic + hostel), not only the hostel fee.
_WHOLE_BILL_RE = re.compile(r"\b(bill|tuition|academic|first[- ]year|students?|category|caution|deposit|b\.?tech)\b")
_COMPONENT_RE = re.compile(r"\b(tuition|academic|components?|breakdown|break-up|development|lab|examination fee|exam fee)\b")
_SCHOLARSHIP_RE = re.compile(r"\b(scholarships?|chhatravritti|wazifa)\b")
# Words in the question -> a fragment of the scholarship's name in the summary table.
_SCHOLARSHIP_ALIASES = (
    (re.compile(r"\bmerit\b"), "merit"),
    (re.compile(r"\bneed(?:-based| based)?\b|\blow[- ]income\b"), "need"),
    (re.compile(r"\bsc\s*/\s*st\b|\b(?:sc|st)\b"), "sc/st"),
    (re.compile(r"\bstem\b|\bgirls?\b|\bwom[ae]n\b"), "stem"),
    (re.compile(r"\bsports?\b"), "sports"),
)


def wants_fee_totals(question: str) -> bool:
    """True if the question asks for a fee total or cost the fee tables can answer."""
    text = str(question or "").lower()
    if not (TOTAL_RE.search(text) and FEE_RE.search(text)):
        return False
    return not (_OTHER_MONEY_RE.search(text) and not _BILL_TOPIC_RE.search(text))


# ---------------------------------------------------------------- parsing the tables


@dataclass
class FeeTables:
    """The fee tables of one fee_structure.md (amounts in Rs; category keys "open", "obc", "sc")."""

    year_label: str = ""
    labels: dict[str, str] = field(default_factory=dict)  # category key -> label as written
    academic: dict[str, list[tuple[str, int]]] = field(default_factory=dict)  # components, without the total
    academic_total: dict[str, int] = field(default_factory=dict)
    hostel: list[tuple[str, int]] = field(default_factory=list)
    hostel_total: int | None = None
    bills: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)  # (day scholar, resident)
    yearly: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    caution_deposit: int | None = None
    semesters_per_year: int = 2
    same_for_all_branches: bool = False
    scholarships: dict[str, int] = field(default_factory=dict)  # name -> amount per semester
    scholarship_capped_at_tuition: bool = False


def category_key(label: object) -> str | None:
    """"Open / General" -> "open", "OBC-NCL and EWS" / "EWS" -> "obc", "SC / ST" -> "sc" (None if unknown)."""
    text = str(label or "").strip().lower()
    if "obc" in text or "ews" in text:
        return "obc"
    if re.match(r"(sc|st)\b", text):
        return "sc"
    if "open" in text or "general" in text:
        return "open"
    return None


def _amount(cell: str) -> int | None:
    m = re.fullmatch(r"(?:rs\.?\s*)?(\d{1,3}(?:,\d{2,3})+|\d+)", cell.strip(), flags=re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


def _sections(text: str) -> dict[str, str]:
    """Lower-cased "## heading" -> its body (subsections stay inside their section)."""
    return {
        m.group(1).strip().lower(): m.group(2)
        for m in re.finditer(r"^##\s+(.+?)\s*$(.*?)(?=^##\s|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    }


def _table(body: str) -> list[list[str]]:
    """Rows of the first markdown table in ``body`` (header first, separator row left out)."""
    rows: list[list[str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _section(sections: dict[str, str], start: str) -> str:
    return next((body for heading, body in sections.items() if heading.startswith(start)), "")


def _per_category_table(body: str) -> dict[str, tuple[int | None, int | None]]:
    """Rows "Category | day scholar | hostel resident" -> {key: (day, resident)}."""
    out = {}
    for cells in _table(body)[1:]:
        key = category_key(cells[0])
        if key and len(cells) >= 3:
            out[key] = (_amount(cells[1]), _amount(cells[2]))
    return out


def _parse_academic(body: str, tables: FeeTables) -> None:
    rows = _table(body)
    if not rows:
        return
    columns = [(i, category_key(label), label) for i, label in enumerate(rows[0]) if i and category_key(label)]
    for _, key, label in columns:
        tables.labels.setdefault(key, label)
        tables.academic.setdefault(key, [])
    for cells in rows[1:]:
        for i, key, _ in columns:
            amount = _amount(cells[i]) if i < len(cells) else None
            if amount is None:
                continue
            if cells[0].lower().startswith("total"):
                tables.academic_total[key] = amount
            else:
                tables.academic[key].append((cells[0], amount))


def _parse_hostel(body: str, tables: FeeTables) -> None:
    for cells in _table(body)[1:]:
        amount = _amount(cells[-1]) if len(cells) >= 2 else None
        if amount is None:
            continue
        if cells[0].lower().startswith("total"):
            tables.hostel_total = amount
        else:
            tables.hostel.append((cells[0], amount))
    if tables.hostel_total is None and tables.hostel:
        tables.hostel_total = sum(a for _, a in tables.hostel)


def _parse_scholarships(text: str, tables: FeeTables) -> None:
    body = _section(_sections(text), "summary")
    for cells in _table(body)[1:]:
        m = re.search(r"Rs\s*([\d,]+)", cells[1]) if len(cells) >= 2 else None
        if m:
            tables.scholarships[cells[0]] = int(m.group(1).replace(",", ""))
    tables.scholarship_capped_at_tuition = bool(re.search(r"cannot be more than the student's tuition fee", text))


def parse_fee_tables(fee_text: str, scholarship_text: str = "") -> FeeTables | None:
    """All fee tables of the document, or None when it has neither a bill table nor an academic fee table."""
    sections = _sections(fee_text)
    tables = FeeTables()
    title = re.search(r"^#\s+.*?(\d{4}-\d{2})", fee_text, flags=re.MULTILINE)
    tables.year_label = title.group(1) if title else ""
    _parse_academic(_section(sections, "academic fee"), tables)
    _parse_hostel(_section(sections, "hostel and mess fee"), tables)
    tables.bills = _per_category_table(_section(sections, "total semester bill"))
    tables.yearly = _per_category_table(_section(sections, "total yearly cost"))
    for key in list(tables.bills) + list(tables.yearly):
        tables.labels.setdefault(key, {"open": "Open / General", "obc": "OBC-NCL and EWS", "sc": "SC / ST"}[key])
    deposit = re.search(r"caution deposit of Rs ([\d,]+)", fee_text, flags=re.IGNORECASE)
    tables.caution_deposit = int(deposit.group(1).replace(",", "")) if deposit else None
    tables.same_for_all_branches = bool(re.search(r"same for all (?:four |4 )?branches", fee_text, re.IGNORECASE))
    _parse_scholarships(scholarship_text, tables)
    if not (tables.bills or tables.academic_total):
        return None
    tables.labels = {k: tables.labels[k] for k in ("open", "obc", "sc") if k in tables.labels}
    return tables


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


@lru_cache(maxsize=4)
def _load(fee_path: str, fee_mtime: int, scholarship_path: str, scholarship_mtime: int) -> FeeTables | None:
    try:
        fee_text = Path(fee_path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        scholarship_text = Path(scholarship_path).read_text(encoding="utf-8")
    except OSError:
        scholarship_text = ""
    return parse_fee_tables(fee_text, scholarship_text)


def load_fee_tables(fee_file: str | Path | None = None) -> FeeTables | None:
    """Parsed tables of the configured fee_structure.md (cached until the file changes); None if unreadable."""
    fee_path = Path(fee_file) if fee_file else Path(get_settings().data_dir) / FEE_FILE
    scholarship_path = fee_path.parent / SCHOLARSHIP_FILE
    return _load(str(fee_path), _mtime(fee_path), str(scholarship_path), _mtime(scholarship_path))


# ---------------------------------------------------------------- totals


def semester_bill(tables: FeeTables, key: str, resident: bool) -> int | None:
    """Published semester bill, else academic fee (+ hostel fee for residents)."""
    published = tables.bills.get(key, (None, None))[1 if resident else 0]
    if published:
        return published
    academic = tables.academic_total.get(key)
    if academic is None or (resident and tables.hostel_total is None):
        return None
    return academic + (tables.hostel_total if resident else 0)


def yearly_cost(tables: FeeTables, key: str, resident: bool) -> int | None:
    """Published yearly cost, else semesters per year x the semester bill."""
    published = tables.yearly.get(key, (None, None))[1 if resident else 0]
    if published:
        return published
    bill = semester_bill(tables, key, resident)
    return bill * tables.semesters_per_year if bill else None


def record_category(record: dict | None, tables: FeeTables) -> str | None:
    """The student's fee category: the record's own field, else the category whose bill equals the record's total."""
    if not record:
        return None
    key = category_key(record.get("category"))
    if key:
        return key
    try:
        total = int(round(float((record.get("fees") or {}).get("total"))))
    except (TypeError, ValueError):
        return None
    resident = bool(record.get("hostel"))
    matches = [k for k in tables.labels if semester_bill(tables, k, resident) == total]
    return matches[0] if len(matches) == 1 else None


def categories_in(text: str) -> list[str]:
    return [key for key, rx in _CATEGORY_RES if rx.search(text)]


def hostel_options_in(text: str) -> list[bool]:
    """[False] day scholar, [True] hostel resident, both, or [] when the question names neither."""
    day = bool(_DAY_RE.search(text))
    resident = bool(_RESIDENT_RE.search(_DAY_RE.sub(" ", text)))
    return [option for option, named in ((False, day), (True, resident)) if named]


def _scholarships_named(text: str, tables: FeeTables) -> list[tuple[str, int]]:
    if not _SCHOLARSHIP_RE.search(text):
        return []
    fragments = [fragment for rx, fragment in _SCHOLARSHIP_ALIASES if rx.search(text)]
    return [(name, amount) for name, amount in tables.scholarships.items()
            if any(fragment in name.lower() for fragment in fragments)]


def _option_label(tables: FeeTables, key: str, resident: bool) -> str:
    return f"{tables.labels[key]}, {'hostel resident' if resident else 'day scholar'}"


def _bill_line(tables: FeeTables, key: str, resident: bool, four_years: bool) -> str | None:
    rs = format_rupees
    bill, year = semester_bill(tables, key, resident), yearly_cost(tables, key, resident)
    if not bill or not year:
        return None
    academic = tables.academic_total.get(key)
    if resident and academic and tables.hostel_total and academic + tables.hostel_total == bill:
        parts = [f"per semester: academic fee {rs(academic)} + hostel and mess fee {rs(tables.hostel_total)} "
                 f"= {rs(bill)}"]
    else:
        parts = [f"per semester: {rs(bill)}" + ("" if resident else " (no hostel fee)")]
    n = tables.semesters_per_year
    parts.append(f"per year: {n} semesters x {rs(bill)} = {rs(year)}" if year == n * bill else f"per year: {rs(year)}")
    deposit = tables.caution_deposit if resident else None
    if deposit:
        parts.append(f"first year with the one-time refundable hostel caution deposit: {rs(year)} + {rs(deposit)} "
                     f"= {rs(year + deposit)}")
    if four_years:
        years = PROGRAMME_SEMESTERS // n
        programme = f"full {years}-year B.Tech at {tables.year_label} rates: {years} x {rs(year)} = {rs(years * year)}"
        if deposit:
            programme += f", plus the deposit once: {rs(years * year + deposit)}"
        parts.append(programme)
    return f"- {_option_label(tables, key, resident)}: " + "; ".join(parts) + "."


def _scholarship_line(tables: FeeTables, key: str, resident: bool, name: str, amount: int) -> str | None:
    rs = format_rupees
    bill, year = semester_bill(tables, key, resident), yearly_cost(tables, key, resident)
    if not bill or not year:
        return None
    tuition = next((a for label, a in tables.academic.get(key, []) if label.lower().startswith("tuition")), None)
    credit, note = amount, f"{rs(amount)} per semester"
    if tables.scholarship_capped_at_tuition and tuition is not None and amount > tuition:
        credit, note = tuition, f"{rs(amount)} per semester, capped at the {rs(tuition)} tuition fee"
    n = tables.semesters_per_year
    return (
        f"- {_option_label(tables, key, resident)} with the {name} ({note}): {rs(bill)} - {rs(credit)} = "
        f"{rs(bill - credit)} per semester; {rs(year)} - {n} x {rs(credit)} = {rs(year - n * credit)} per year."
    )


def _lower_first(label: str) -> str:
    """"Mess charges (Rs 4,000 ...)" -> "mess charges (Rs 4,000 ...)": reads as part of a sentence."""
    return label[:1].lower() + label[1:]


def _academic_line(tables: FeeTables, key: str) -> str | None:
    parts, total = tables.academic.get(key), tables.academic_total.get(key)
    if not parts or not total:
        return None
    sums = " + ".join(f"{_lower_first(label)} {format_rupees(a)}" for label, a in parts)
    check = "" if sum(a for _, a in parts) == total else " (as published)"
    return f"- {tables.labels[key]} academic fee per semester: {sums} = {format_rupees(total)}{check}."


def _hostel_line(tables: FeeTables) -> str | None:
    if not tables.hostel or not tables.hostel_total:
        return None
    rs, total, n = format_rupees, tables.hostel_total, tables.semesters_per_year
    sums = " + ".join(f"{_lower_first(label)} {rs(a)}" for label, a in tables.hostel)
    return (f"- Hostel and mess fee per semester (same for every category): {sums} = {rs(total)}; "
            f"per year: {n} x {rs(total)} = {rs(n * total)}.")


def _branch_line(question: str, tables: FeeTables) -> str | None:
    from smart_campus_assistant.simple_rag import mentioned_branches  # lazy: simple_rag imports this module

    named = mentioned_branches(question)
    if not named or not tables.same_for_all_branches:
        return None
    return (f"- Fees are the same for all {len(BRANCHES)} branches ({', '.join(BRANCHES)}), so these totals are "
            f"the same for {' and '.join(named)}.")


def fee_totals_text(question: str, record: dict | None = None) -> str:
    """The computed-totals block for a total / cost question ("" when there is nothing to compute).

    Categories and the hostel option come from the question; when it names none, from ``record``
    (the signed-in student's own fee category and hostel status); otherwise every category and both
    options are listed.
    """
    if not wants_fee_totals(question):
        return ""
    tables = load_fee_tables()
    if tables is None or not tables.labels:
        return ""
    text = str(question).lower()
    own_key = record_category(record, tables)
    keys = [k for k in categories_in(text) if k in tables.labels] or ([own_key] if own_key else list(tables.labels))
    options = hostel_options_in(text) or ([bool(record.get("hostel"))] if record else [False, True])
    four_years = bool(_FOUR_YEAR_RE.search(text))

    lines = [_bill_line(tables, k, resident, four_years) for k in keys for resident in options]
    for name, amount in _scholarships_named(text, tables):
        lines += [_scholarship_line(tables, k, resident, name, amount) for k in keys for resident in options]
    if _COMPONENT_RE.search(text):
        lines += [_academic_line(tables, k) for k in keys]
    if _RESIDENT_RE.search(_DAY_RE.sub(" ", text)):
        # "Hostel fee including mess?" is about the hostel fee itself: that line goes first.
        about_hostel_fee = not categories_in(text) and not _WHOLE_BILL_RE.search(text)
        lines.insert(0 if about_hostel_fee else len(lines), _hostel_line(tables))
    lines.append(_branch_line(question, tables))
    lines = [line for line in lines if line]
    if not lines:
        return ""
    scope = ""
    if record and own_key and not categories_in(text):
        scope = f" for your fee category ({_option_label(tables, own_key, bool(record.get('hostel')))}, from your bill)"
    heading = f"Computed fee totals{scope} > worked out from the {tables.year_label} fee tables in {FEE_FILE}"
    return "\n".join([heading, *lines])


def fee_totals_document(question: str, record: dict | None = None) -> Document | None:
    """The computed totals as a Document, so the usual context numbering and citations apply to it."""
    text = fee_totals_text(question, record)
    if not text:
        return None
    return Document(
        page_content=text,
        metadata={"college": COLLEGE_KEY, "doc_type": DOC_TYPE, "source": FEE_FILE, "page": None,
                  "chunk_id": f"{FEE_FILE}:computed"},
    )


def with_fee_totals(question: str, docs: list[Document], record: dict | None = None) -> list[Document]:
    """``docs`` with the computed-totals block first when the question asks for a total (else unchanged)."""
    block = fee_totals_document(question, record)
    return [block, *docs] if block else list(docs)


def is_computed(doc: Document) -> bool:
    return doc.metadata.get("doc_type") == DOC_TYPE
