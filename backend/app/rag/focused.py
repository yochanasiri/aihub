"""Conservative, schema-free extraction for atomic fields and complete document sections.

All returned quotes are copied from the retrieved source (or its verified original
PDF page), never manufactured. Ambiguous cases fall through to the normal RAG path.
"""
import re
from dataclasses import dataclass

@dataclass
class VerifiedQuote:
    source_id: str
    quote: str
    answer: str = ""

def normalize(text):
    return re.sub(r"\s+", " ", text).strip()

# Generic document-label vocabulary: no company names or company-specific schema.
LABELS = {
    "stipend": ("stipend", "internship stipend"),
    "salary": ("salary", "compensation", "ctc", "package"),
    "eligibility": ("eligibility", "eligibility criteria", "requirements"),
    "job description": ("job description", "job responsibilities", "responsibilities", "jd"),
    "selection process": ("selection process", "recruitment process", "hiring process"),
}
STOP_LABELS = sorted({x for group in LABELS.values() for x in group} |
                     {"role", "open for", "number of selects", "number of selects", "number of selections",
                      "job location", "location", "company", "company name", "skills required",
                      "qualifications", "application deadline", "last date", "about company"},
                     key=len, reverse=True)
LABEL_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(x) for x in STOP_LABELS) + r")\s*:\s*", re.I)
BULLET_RE = re.compile(r"(?:^|\n)\s*(?:[•●▪◦*]|[-–]|\d+[.)])\s+", re.M)
AMOUNT_RE = re.compile(r"(?:₹|Rs\.?|INR|USD|\$)?\s*\d[\d,]*(?:\.\d+)?\s*(?:per\s+(?:month|year|annum)|/\s*(?:month|year)|p\.?a\.?|lpa|lakhs?|monthly|annually)?", re.I)
QUESTION_HEAD = re.compile(r"\b(?:what|is|are|the|tell|me|give|show|complete|full|all|entire|details|detail|for|of|in|at|about|please|company|internship|intern|offered|offering|by|does|provide|provides|and|its|their)\b", re.I)


def intent(question: str) -> str | None:
    q = question.casefold()
    if re.search(r"\b(stipend|internship pay)\b", q):return "stipend"
    if re.search(r"\b(salary|compensation|ctc|package)\b", q):return "salary"
    if re.search(r"\b(jd|job description|job responsibilities|responsibilities)\b", q):return "job description"
    if re.search(r"\b(eligibility|eligible|qualifications)\b", q):return "eligibility"
    if re.search(r"\b(selection process|hiring process|recruitment process)\b", q):return "selection process"
    return None


def entity(question: str) -> str | None:
    """Extract an explicit organization span only for clear 'of/for/from X' phrasing."""
    q = re.sub(r"[?!.]+$", "", question.strip())
    match = re.search(r"\b(?:of|for|from|at|by)\s+(.+)$", q, re.I)
    if not match:return None
    value = match.group(1).strip()
    value = re.split(r"\b(?:in\s+20\d\d|for\s+(?:internship|placement)|at\s+iit|during\s+placement)\b", value, maxsplit=1, flags=re.I)[0].strip()
    if not value or len(value) > 70 or len(value.split()) > 8:return None
    if value.casefold() in {"iit indore", "interns", "students", "the institute", "internship", "placements"}:return None
    return value


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", s.casefold()))


def entity_matches(name: str, text: str) -> bool:
    # Require the whole requested organization phrase, not just shared generic words.
    name = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    hay = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", hay):
        return True
    # In a handbook, a question may include both the company and role:
    # "Deloitte Business Analyst". Check the source's *company heading*
    # and role independently instead of requiring that exact concatenation.
    from .role_sections import company_headers, possible_roles
    lines = text.splitlines()
    for i, company in company_headers(lines):
        company_norm = ' '.join(re.findall(r'[a-z0-9]+', company.casefold()))
        if name == company_norm or name.startswith(company_norm + ' '):
            role_tail = name[len(company_norm):].strip()
            if not role_tail:
                return True
            roles = possible_roles(lines, next((j for j in range(i,len(lines)) if re.match(r"\s*Job Description\s*:", lines[j], re.I)), i+1))
            if any(' '.join(re.findall(r'[a-z0-9]+', r.casefold())) == role_tail for r in roles):
                return True
    return False


def evidence_matches(question, source):
    name = entity(question)
    if not name:return True
    # A page may only hold the second half of the record. The stored document text
    # establishes ownership; never use this to mix unrelated source documents.
    md = source.node.node.metadata
    context = "\n".join(str(x or "") for x in (
        source.source.get("title"), md.get("_document_text", ""), source.text))
    return entity_matches(name, context)


def source_text(source):
    md = source.node.node.metadata
    return md.get("_source_page_text") or source.text


def fields(text):
    """Generic label boundaries on both wrapped and flattened extracted PDF text."""
    matches = list(LABEL_RE.finditer(text))
    # Some PDF headings appear alone on a line without a colon.
    for m in re.finditer(r"(?m)^\s*(" + "|".join(re.escape(x) for x in STOP_LABELS) + r")\s*$", text, re.I):
        if not any(existing.start() == m.start() for existing in matches):
            matches.append(m)
    matches.sort(key=lambda m: m.start())
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        yield m.group(1).strip().casefold(), text[m.end():end].strip(), m.start(), end


def structured_quotes(question, evidence):
    """Yield every source-preserved part of a matched section with page citations."""
    chosen = [e for e in evidence if e.node.node.metadata.get('_structured_section')]
    if not chosen:
        return []
    # The retriever resolves and validates which document the section belongs to.
    doc_id = chosen[0].source.get('document_id')
    chosen = [e for e in chosen if e.source.get('document_id') == doc_id]
    chosen.sort(key=lambda e: e.source.get('page') or 0)
    result = []
    for item in chosen:
        section = item.node.node.metadata['_structured_section'].strip()
        if section:
            # Keep line breaks, bullets, and table separators in the displayed answer.
            result.append(VerifiedQuote(item.source_id, section, section))
    return result


def _field_belongs_to_entity(name: str, text: str, start: int, title: str) -> bool:
    """For multi-company documents, bind a value to its nearest company header."""
    from .role_sections import company_headers
    lines = text.splitlines(keepends=True)
    headers = company_headers([line.rstrip('\r\n') for line in lines])
    if headers:
        offsets = []
        pos = 0
        for line in lines:
            offsets.append(pos)
            pos += len(line)
        preceding = [(idx, company) for idx, company in headers if offsets[idx] <= start]
        if preceding:
            company = preceding[-1][1]
            return norm_company(name) == norm_company(company) or norm_company(name).startswith(norm_company(company) + ' ')
        return False
    # On an individually uploaded file or a one-line placement record, require
    # the actual organization name in the file title or the same local record.
    return (entity_matches(name, title) or
            entity_matches(name, text[max(0,start-200):start]))


def norm_company(value: str) -> str:
    return ' '.join(re.findall(r'[a-z0-9]+', value.casefold()))


def focused_quotes(question, evidence):
    target = intent(question)
    if not target:return []
    aliases = set(LABELS[target])
    matching = [e for e in evidence if evidence_matches(question, e)]
    if not matching:return []
    if target in ("stipend", "salary"):
        for source in matching:
            text = source_text(source)
            for label, value, start, end in fields(text):
                if label not in aliases or not value:continue
                # Do not mistake a sentence unrelated to the amount for a field value.
                value = re.split(r"[\r\n]", value, maxsplit=1)[0].strip()
                value = re.split(r"(?<=\d)\s{2,}", value, maxsplit=1)[0].strip()
                if not re.search(r"\d", value):continue
                if len(value) > 100:continue  # ambiguous boundary: use normal LLM
                # Keep only the requested label and value in the citation excerpt.
                raw = text[start:end]
                quote = raw[:raw.find(value) + len(value)] if value in raw else raw[:min(len(raw), 1600)]
                # Quote may include following content when a document contains no
                # obvious label boundary. Only show the extracted requested value.
                answer = f"{label.title()}: {normalize(value)}."
                name = entity(question)
                if name and not _field_belongs_to_entity(name, text, start, source.source.get("title", "")):
                    continue
                return [VerifiedQuote(source.source_id, quote, answer)]
        return []
    # Complete-list/section questions: preserve exact section content and bullet order.
    results = []
    matching.sort(key=lambda e: (str(e.source.get("document_id", "")), e.source.get("page") or 0))
    for source in matching:
        text = source_text(source)
        for label, value, start, end in fields(text):
            if label not in aliases or not value:continue
            # Only return a section if its boundary is discoverable. Avoid giving a
            # giant arbitrarily truncated page as the supposed complete section.
            if len(value) > 5000:continue
            quote = text[start:end].strip()
            if not quote or len(quote) > 5000:continue
            answer = label.title() + ":\n" + value.strip()
            name = entity(question)
            if name and not (entity_matches(name, source.source.get("title", ""))
                             or entity_matches(name, text[:max(200, start+1)])):
                continue
            results.append(VerifiedQuote(source.source_id, quote, answer))
            # Continue onto adjacent pages, preserving a distinct page citation.
            last_page = source.source.get("page")
            doc_id = source.source.get("document_id")
            for following in matching:
                page = following.source.get("page")
                if (not last_page or page != last_page + 1 or
                        following.source.get("document_id") != doc_id):
                    continue
                full = source_text(following)
                boundaries = list(fields(full))
                # The next heading indicates that the requested section ended.
                if boundaries and boundaries[0][2] < 100 and not BULLET_RE.search(full[:boundaries[0][2]]):
                    break
                content = full[:boundaries[0][2]] if boundaries else full
                content = content.strip()
                if not content or len(content) > 5000:
                    break
                results.append(VerifiedQuote(following.source_id, content,
                                             label.title() + " (continued):\n" + content))
                last_page = page
                if boundaries:
                    break
            return results
    return results


def structured_answer_markdown(answer: str) -> str:
    """Safely present source-preserved sections as readable Markdown lists.

    Convert document bullets into numbered Markdown items without changing their
    contents. Escape markup from untrusted documents before introducing our own
    Markdown formatting. Preserve table rows as separate lines.
    """
    import html
    value = html.escape(str(answer or ''))
    value = re.sub(r'\s+([•●▪◦])\s+', r'\n\1 ', value)
    raw_lines = value.splitlines()
    # Preserve explicit tabular rows when the original extractor provided pipe
    # separators and a header (do not invent column labels for headerless data).
    formatted=[]
    i=0
    while i<len(raw_lines):
        line=raw_lines[i].strip()
        header=re.match(r'^Table columns\s*:\s*(.+)$',line,re.I)
        if header and '|' in header.group(1):
            cols=[x.strip() for x in header.group(1).split('|')]
            rows=[]
            j=i+1
            while j<len(raw_lines) and '|' in raw_lines[j]:
                cells=[x.strip() for x in raw_lines[j].split('|')]
                if len(cells)!=len(cols):break
                rows.append(cells)
                j+=1
            if rows:
                formatted.append('| '+' | '.join(cols)+' |')
                formatted.append('| '+' | '.join(['---']*len(cols))+' |')
                formatted.extend('| '+' | '.join(cells)+' |' for cells in rows)
                i=j
                continue
        formatted.append(raw_lines[i])
        i+=1
    raw_lines=formatted
    items=[]
    index=0
    for position, line in enumerate(raw_lines):
        s=line.strip()
        if not s:
            continue
        if s.startswith('| ') and s.endswith(' |'):
            items.append(s)
            continue
        m=re.match(r'^(?:[•●▪◦*\-–]|\(?\d{1,3}[.)])\s+(.+)$',s)
        if m:
            index+=1
            content=m.group(1).replace('`','\\`').replace('[','\\[').replace(']','\\]')
            items.append(f'{index}. {content}')
        elif s.endswith(':') or (len(s)<55 and not s.endswith(('.', ';', ',')) and '|' not in s
                and any(re.match(r'^\s*(?:[•●▪◦*\-–]|\(?\d{1,3}[.)])\s+', nxt)
                        for nxt in raw_lines[position+1:position+4])):
            # A role subheading resets the item numbering for the next role.
            index=0
            items.append(f'**{s.replace("*", "\\\\*")}**')
        else:
            if index and items and re.match(r'^\d+\. ',items[-1]):
                # Wrapped continuation of an existing bullet.
                items[-1]+=' '+s
            else:
                items.append(s)
    rendered = []
    for line in items:
        if line.startswith('| ') and rendered and rendered[-1].startswith('| '):
            rendered[-1] += '\n' + line
        else:
            rendered.append(line)
    return '\n\n'.join(rendered)
