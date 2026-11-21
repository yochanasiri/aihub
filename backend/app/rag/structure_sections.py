"""Document-agnostic heading/list/table preservation and source section expansion.

This is a deterministic *reader* of original uploaded-page text, not a summarizer.
It does not attempt to create a company-specific schema or invent missing rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Only topic aliases are needed for user shorthand; source headings can be arbitrary.
ALIASES = {
    "jd": "job description",
    "job responsibilities": "job description",
    "role description": "job description",
    "hiring stages": "selection process",
    "requirements": "eligibility",
}
STOPWORDS = set("what is are the a an of for from at in on to me tell give show list entire full complete all details please about i want its their company document information points section under explain do does".split())
BULLET = re.compile(r"^\s*(?:[•●▪◦*\-–]|\(?\d{1,2}[.)])\s+", re.I)
NUMBERED_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|(?:\d+(?:\.\d+){0,3})[.)]?\s+)(.+)$")
LABEL_HEAD = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /&()\-]{2,85}):\s*$")
TABLE_LABEL = re.compile(r"^\s*Table columns\s*:", re.I)
SAME_LINE_FIELD = re.compile(r"(?<!\w)(?:Stipend|Salary|CTC|Eligibility(?: Criteria)?|Job Description|Selection Process|Skills Required|Role|Company|Open For|Number of Selects)\s*:", re.I)


@dataclass(frozen=True)
class Segment:
    page: int
    text: str


@dataclass(frozen=True)
class Section:
    heading: str
    pieces: tuple[Segment, ...]
    context: str


def norm(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def heading_of(raw: str) -> str | None:
    """Detect standalone visual headings conservatively; never treat bullet prose as headings."""
    line = raw.strip()
    if not line or len(line) > 96 or BULLET.match(line) or TABLE_LABEL.match(line):
        return None
    match = NUMBERED_HEADING.match(line)
    if match:
        line = match.group(1).strip()
    colon = LABEL_HEAD.fullmatch(line)
    if colon:
        return colon.group(1).strip()
    if line.endswith(('.', ';', ',', '?', '!')) or '|' in line or len(line.split()) > 9:
        return None
    # General heading heuristics for text/Markdown and PDF extractions.
    if raw.lstrip().startswith('#') and len(line) >= 3:
        return line.lstrip('#').strip()
    if raw.lstrip()[:1].isdigit() and match and len(line.split()) >= 2:
        return line
    if 2 <= len(line.split()) <= 7 and (line.isupper() or line.istitle()):
        return line
    return None


def requested_heading(question: str) -> str | None:
    q = question.casefold().strip()
    for alias, canonical in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        q = re.sub(r"\b"+re.escape(alias)+r"\b", canonical, q)
    # Open-ended prompts are not automatically interpreted as whole-section asks.
    if not re.search(r"\b(?:section|jd|job description|eligibility|requirements|selection process|syllabus|policy|rules|responsibilities|criteria|procedure|all|complete|full|list|every|points|table)\b", q):
        return None
    # Explicit 'under heading X' is generic for any heading.
    m = re.search(r"(?:under|in|from)\s+(?:the\s+)?(?:heading|section)\s+[\"']?(.+?)[\"']?(?:\?|$)", q)
    if m:
        return m.group(1).strip(" ?.'\"")
    if 'job description' in q:
        return 'job description'
    for candidate in ('eligibility criteria', 'eligibility', 'selection process', 'syllabus',
                      'requirements', 'responsibilities', 'skills required'):
        if re.search(r"\b" + re.escape(candidate) + r"\b", q):
            return ALIASES.get(candidate, candidate)
    # For 'full fee structure', 'all hostel rules', etc., retain content words.
    bits = [w for w in re.findall(r"[a-z0-9]+", q) if w not in STOPWORDS]
    return ' '.join(bits[:5]) if bits and re.search(r"\b(all|full|complete|entire|list|every|points|table)\b", q) else None


def score_heading(wanted: str, heading: str) -> float:
    w, h = set(norm(wanted).split()), set(norm(heading).split())
    if not w or not h:
        return 0
    if norm(wanted) == norm(heading):
        return 10
    if w.issubset(h):
        return 5 + len(w) / max(len(h), 1)
    if h.issubset(w) and len(h) >= 2:
        return 3
    return 2 * len(w & h) / len(w | h) if len(w & h) >= 2 else 0


def find_sections(pages: list[dict]) -> list[Section]:
    """Build contiguous sections across page boundaries, retaining original line breaks."""
    records: list[tuple[int, str]] = []
    for page in sorted(pages, key=lambda p: p['page']):
        for line in page.get('text', '').splitlines(keepends=True):
            records.append((page['page'], line))
        # Preserve a gap between pages even if the original page lacks a final newline.
        records.append((page['page'], '\n'))
    heads = [(i, heading_of(line)) for i, (_, line) in enumerate(records)]
    heads = [(i, heading) for i, heading in heads if heading]
    result = []
    for n, (start, heading) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(records)
        context = ''.join(line for _, line in records[max(0, start-18):start])[-1500:]
        pieces = []
        for page, line in records[start:end]:
            if pieces and pieces[-1].page == page:
                last = pieces[-1]
                pieces[-1] = Segment(page, last.text + line)
            else:
                pieces.append(Segment(page, line))
        if pieces:
            result.append(Section(heading, tuple(pieces), context))
    return result


def match_section(pages: list[dict], question: str, title: str = '', entity: str | None = None) -> Section | None:
    wanted = requested_heading(question)
    if not wanted:
        return None
    if wanted == 'job description':
        from .role_sections import job_description
        selected = job_description(pages, question, title, entity)
        if selected:
            heading, pieces = selected
            return Section(heading, tuple(Segment(page, content) for page, content in pieces), '')
        # Do not fall back to the first unrelated role/company section in a
        # multi-company source, particularly when the user selected a role.
        if entity:
            return None
    options = []
    for sec in find_sections(pages):
        score = score_heading(wanted, sec.heading)
        if score < 1.25:
            continue
        text = ''.join(piece.text for piece in sec.pieces)
        if not text.strip() or len(text) > 20000 or len(sec.pieces) > 6:
            continue  # Refuse to claim an oversized/ambiguous section is complete.
        if entity:
            from .focused import entity_matches
            # Company must be tied to the document title or nearby header/record,
            # not simply mentioned somewhere else in a large PDF.
            scoped = title + '\n' + sec.context[-900:] + '\n' + text[:160]
            if not entity_matches(entity, scoped):
                continue
        options.append((score, len(text), sec))
    return max(options, key=lambda item: (item[0], item[1]))[2] if options else None
