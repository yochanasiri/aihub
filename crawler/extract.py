import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from bs4 import BeautifulSoup, Comment
import pymupdf
from .policy import normalize_url
from backend.app.pdf_layout import extract_tables


@dataclass
class Extracted:
    title: str
    text: str
    doc_type: str
    links: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self):
        # Include title because it becomes retrieval metadata.
        return hashlib.sha256((self.title + "\n" + self.text).encode()).hexdigest()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def extract_html(body: bytes, url: str, domains: list[str]) -> Extracted:
    soup = BeautifulSoup(body, "html.parser")
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    title = clean(soup.title.get_text(" ")) if soup.title else url
    links = []
    # Discover links before removing navigation, so department pages remain reachable.
    for tag in soup.select("a[href]"):
        try:
            link = normalize_url(tag["href"], domains, url)
            if link not in links:
                links.append(link)
        except ValueError:
            continue
    for tag in soup.select("script,style,noscript,nav,footer,header,form,aside,[hidden],[aria-hidden='true']"):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    # Keep heading boundaries and table rows instead of flattening everything.
    blocks, headings = [], []
    for element in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]):
        if element.find_parent(["p", "li", "tr"]):
            continue
        if element.name == "tr":
            value = " | ".join(clean(c.get_text(" ")) for c in element.find_all(["td", "th"], recursive=False))
        else:
            value = clean(element.get_text(" "))
        if not value:
            continue
        if element.name.startswith("h"):
            headings.append(value)
            value = "#" * int(element.name[1]) + " " + value
        blocks.append(value)
    # Legacy pages often have bare text in divs; preserve it without duplicating semantic blocks.
    for node in root.find_all(string=True):
        if node.find_parent(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]):
            continue
        value = clean(str(node))
        if value:
            blocks.append(value)
    return Extracted(title, "\n\n".join(blocks), "html", links, {"headings": headings})


def extract_pdf(body: bytes, url: str, max_pages: int) -> Extracted:
    with pymupdf.open(stream=body, filetype="pdf") as pdf:
        if pdf.is_encrypted or pdf.page_count > max_pages:
            raise ValueError("Encrypted PDF or page limit exceeded")
        title = clean((pdf.metadata or {}).get("title") or url.rsplit("/", 1)[-1])
        pages, text_parts = [], []
        for i, page in enumerate(pdf):
            # Blocks retain more readable paragraph boundaries than one flattened page.
            text = "\n\n".join(clean(b[4]) for b in page.get_text("blocks") if b[6] == 0 and clean(b[4]))
            tables = extract_tables(page)
            if tables:
                text = text + "\n\n" + tables
            pages.append({"page": i + 1, "text": text})
            text_parts.append(f"## Page {i + 1}\n\n{text}")
        if not any(p["text"] for p in pages):
            raise ValueError("PDF has no extractable text; OCR review required")
        return Extracted(title, "\n\n".join(text_parts), "pdf", metadata={"pages": pages, "page_count": len(pages)})
