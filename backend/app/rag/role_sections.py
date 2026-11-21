"""Source-preserving nested role sections for multi-role job descriptions.

Uses the roles and company headings found in the document itself, not a
company-specific schema. This module never invents missing information.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

COMPANY_LINE = re.compile(r'^\s*(?:#{1,6}\s*)?(?:\d{1,3}\s*[.)]\s+)?(?P<name>[^:•|]{2,75})\s*$')
NUMBERED_COMPANY = re.compile(r'^\s*(?:#{1,6}\s*)?\d{1,3}\s*[.)]\s+([\w][^:•|]{1,75})\s*$')
FIELD_START = re.compile(r'^\s*(?:stipend|salary|ctc|compensation|eligibility(?: criteria)?|selection process|open for|number of select(?:s|ions)|location|application process|deadline|requirements|qualifications)\s*:', re.I)
JD_START = re.compile(r'^\s*(?:#{1,6}\s*)?(?:job description|job responsibilities|responsibilities|jd)\s*:\s*(.*)$', re.I)
ROLE_START = re.compile(r'^\s*role(?:s)?\s*:\s*(.+)$', re.I)
BULLET = re.compile(r'^\s*(?:[•●▪◦*\-–]|\(?\d{1,2}[.)])\s+')


def normalize(value: str) -> str:
    return ' '.join(re.findall(r'[a-z0-9]+', value.casefold()))


def is_company_heading(line: str) -> bool:
    """Reject numbered list items while allowing 4. Deloitte / 5. L&T Finance."""
    match=NUMBERED_COMPANY.match(line)
    if not match:
        return False
    name=match.group(1).strip()
    words=name.split()
    if not words or len(words)>6 or len(name)>55:
        return False
    # In a document, numbered JD bullets are usually normal sentences with
    # lowercase connecting words, whereas company titles are mostly proper nouns.
    acceptable={'and','of','the','&','for','in','pvt','ltd','private','limited'}
    return all(w.casefold() in acceptable or w[0].isupper() or w[0].isdigit()
               for w in words if w)


def company_headers(lines: list[str]) -> list[tuple[int,str]]:
    headers=[]
    for i,line in enumerate(lines):
        m=NUMBERED_COMPANY.match(line)
        if m and is_company_heading(line):
            headers.append((i,m.group(1).strip()))
        else:
            m=re.match(r'^\s*(?:company|organization|employer)\s*:\s*(.+)$',line,re.I)
            if m:
                headers.append((i,m.group(1).strip()))
                continue
            # Unnumbered company headers are common in uploaded individual JDs.
            # Only accept one when the following lines confirm a role label.
            candidate=line.strip()
            following=[x.strip() for x in lines[i+1:i+5] if x.strip()]
            if (2 <= len(candidate) <= 60 and 1 <= len(candidate.split()) <= 6
                    and not BULLET.match(candidate) and ':' not in candidate
                    and not JD_START.match(candidate)
                    and all(w[0].isupper() or w.casefold() in {'and','of','&','the'}
                            for w in candidate.split() if w)
                    and any(ROLE_START.match(x) for x in following[:2])):
                headers.append((i,candidate))
    return headers


def possible_roles(lines: list[str], jd_start: int) -> list[str]:
    """Discover role names in the source, including Role: X, Y headings."""
    roles=[]
    for line in lines[max(0,jd_start-22):jd_start]:
        m=ROLE_START.match(line)
        if m:
            for part in re.split(r'[,;/]|\s+\band\b\s+', m.group(1), flags=re.I):
                item=part.strip(' .')
                if item and 1 <= len(item.split()) <= 7 and item not in roles:
                    roles.append(item)
    # When a Role: label is absent, short standalone headings followed by bullets
    # still establish role subheadings, but only if at least two such headings exist.
    for i in range(jd_start+1, min(len(lines)-1,jd_start+130)):
        s=lines[i].strip()
        if FIELD_START.match(s) or is_company_heading(s):break
        nextline=next((x.strip() for x in lines[i+1:i+4] if x.strip()),'')
        if (s and len(s)<=65 and 1<=len(s.split())<=5 and
            not any(x in s for x in ':|•.') and
            not BULLET.match(s) and BULLET.match(nextline) and
            (s[0].isupper() or s.isupper())):
            if normalize(s) not in [normalize(r) for r in roles]:roles.append(s)
    return roles


def choose_role(question: str, roles: list[str]) -> str | None:
    """Match the longest *document-discovered* role explicitly named by user."""
    q=' '+normalize(question)+' '
    options=[r for r in roles if ' '+normalize(r)+' ' in q]
    return max(options,key=lambda x:len(normalize(x))) if options else None


def company_for_query(question: str, headers: list[tuple[int,str]], title: str = '') -> str | None:
    q=' '+normalize(question)+' '
    matched=[name for _,name in headers if ' '+normalize(name)+' ' in q]
    if matched:return max(matched,key=lambda n:len(normalize(n)))
    # A single-company uploaded PDF can identify its company through the filename.
    base= re.sub(r'\.pdf$', '',title,flags=re.I)
    if base and ' '+normalize(base)+' ' in q:return base
    return None


def _flatten_bullets(line: str) -> str:
    # Some old text extractors stored every bullet on one long line.
    return re.sub(r'\s+([•●▪◦])\s+', r'\n\1 ', line)


def job_description(pages: list[dict], question: str, title: str='', entity: str|None=None):
    """Return (heading, [(page, content), ...]) for a complete JD or one role.

    No LLM reconstruction: bullet text and subheadings are original source text.
    """
    ordered=sorted(pages,key=lambda p:int(p['page']))
    records=[]
    for page in ordered:
        for raw in page.get('text','').splitlines():
            for line in _flatten_bullets(raw).splitlines():
                records.append((int(page['page']),line))
    lines=[x for _,x in records]
    headers=company_headers(lines)
    org=company_for_query(question,headers,title)
    # If the question names a specific company present as a heading, scope to it.
    # For long handbooks, never fall back to a different company's section.
    org_starts=[i for i,name in headers if org and normalize(name)==normalize(org)]
    spans=[]
    if org_starts:
        for start in org_starts:
            end=next((i for i,_ in headers if i>start),len(lines))
            spans.append((start,end))
    elif not headers:
        # An uploaded one-company PDF may not include a numbered company heading.
        # If another organization is explicitly requested, do not reuse its JD.
        wanted = normalize(entity or '')
        title_norm = normalize(title)
        if wanted and title_norm and not (wanted in title_norm or title_norm in wanted):
            return None
        spans=[(0,len(lines))]
    elif not entity:
        # An unqualified request may be answered only from an unambiguous source.
        if len(headers)==1:spans=[(headers[0][0],len(lines))]
    if not spans:return None
    for lo,hi in spans:
        jd_indices=[i for i in range(lo,hi) if JD_START.match(lines[i])]
        if not jd_indices:continue
        start=jd_indices[0]
        roles=possible_roles(lines[lo:hi],start-lo)
        selected=choose_role(question,roles)
        stop=hi
        for i in range(start+1,hi):
            if FIELD_START.match(lines[i]) or is_company_heading(lines[i]):
                stop=i;break
        # Collect all role segments under the JD heading, not only the first.
        role_boundaries=[]
        for i in range(start+1,stop):
            if any(normalize(lines[i].strip())==normalize(role) for role in roles):
                if lines[i].strip() and not BULLET.match(lines[i]):
                    role_boundaries.append((i,lines[i].strip()))
        if selected and role_boundaries:
            selected_boundaries=[i for i,name in role_boundaries if normalize(name)==normalize(selected)]
            if not selected_boundaries:return None
            begin=selected_boundaries[0]
            finish=next((i for i,_ in role_boundaries if i>begin),stop)
            relevant=records[begin:finish]
            heading=f'Job Description — {selected}'
        else:
            relevant=records[start:stop]
            heading='Job Description'
        if not any(BULLET.match(t) for _,t in relevant):
            # Still permit paragraphs that are genuinely under the heading.
            if not ''.join(t for _,t in relevant).strip():continue
        # Bound output size to avoid presenting an arbitrarily large fragment.
        if sum(len(t) for _,t in relevant)>20000:return None
        chunks=[]
        for page,line in relevant:
            if chunks and chunks[-1][0]==page:
                chunks[-1]=(page,chunks[-1][1]+'\n'+line)
            else:chunks.append((page,line))
        return heading,chunks
    return None
