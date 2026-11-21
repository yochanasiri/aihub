"""Bounded text extraction. Originals are not retained or served."""
import json
import re
import subprocess
import urllib.request
from io import BytesIO
from pathlib import PurePath

import pymupdf as fitz
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD = 20 * 1024 * 1024
MAX_TEXT = 400_000
MAX_PDF_PAGES = 300
MAX_SCANNED_PAGES = 20
MAX_PIXELS = 16_000_000
EXTENSIONS = {".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg"}
PREFIX = "/api/campus/knowledge/"
MENU_HEADERS = {"day", "breakfast", "lunch", "dinner"}


def upload_document(doc):
    return doc.url == PREFIX + doc.id and doc.metadata_.get("origin") == "admin_upload"


def clean_text(value):
    return value.replace("\x00", "").strip()


def _llm_structure_menu(text):
    """Ask the configured local model to label menu table cells.

    The layout-derived text is always retained below the structured version;
    model output is accepted only when it uses words present in the source.
    This makes the LLM a layout assistant, never a source of new facts.
    """
    try:
        from .config import settings

        endpoint = settings.llm_base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            # Use the installed small local model for bounded upload latency.
            # The full configured model remains responsible for chat answers.
            "model": "qwen2.5:1.5b-instruct",
            "temperature": 0,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (
                    "Organize extracted text from a dining menu table into labeled rows. Return only "
                    "JSON: {\"text\": string}. Keep every source word exactly as written, including "
                    "spelling and singular/plural forms. Do not invent times, ingredients, explanations, "
                    "or menu items. Use Breakfast, Lunch, and Dinner labels only when those labels "
                    "appear in the source. If uncertain, return the source wording unchanged."
                )},
            {"role": "user", "content": text[:5000]},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            endpoint, data=body,
            headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=min(settings.llm_timeout_seconds, 12)) as response:
            result = json.loads(response.read(256_000))
        content = json.loads(result["choices"][0]["message"]["content"])["text"]
        if not isinstance(content, str) or len(content.strip()) < 20:
            return ""
        source_words = set(re.findall(r"[\w]+", text.casefold()))
        output_words = set(re.findall(r"[\w]+", content.casefold()))
        if output_words - source_words:
            return ""
        return content.strip()
    except Exception:
        # A slow/unavailable local model must not make a readable PDF unusable.
        return ""


def ocr(pixmap):
    if pixmap.width * pixmap.height > MAX_PIXELS:
        raise ValueError("Image exceeds the 16 megapixel limit")
    return ocr_png(pixmap.tobytes("png"))


def ocr_png(data):
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "eng"],
            input=data, capture_output=True, check=True,
            timeout=15, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "OMP_THREAD_LIMIT": "1"},
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise ValueError("OCR failed or timed out. Try a clearer image or upload the text as .txt.") from exc
    return clean_text(result.stdout.decode("utf-8", errors="replace"))


def extract_pdf_page(page):
    """Read text by visual lines and preserve aligned PDF table columns.

    PDF text order is often stored column-by-column. Grouping words by their
    vertical position and then by the wide horizontal gaps between cells keeps
    dates, activities, and remarks together even when a table detector misses
    borderless or unevenly sized columns.
    """
    words = [word for word in page.get_text("words") if clean_text(word[4])]
    if not words:
        return ""
    # Menu PDFs often store each meal column as an independent text block. Use
    # the printed header and weekday positions to rebuild complete rows before
    # sending the compact, already-aligned data to the local language model.
    header_words = {word[4].casefold(): word for word in words}
    if MENU_HEADERS.issubset(header_words):
        header_x = [
            (header_words[name][0] + header_words[name][2]) / 2
            for name in ("day", "breakfast", "lunch", "dinner")
        ]
        cuts = [(header_x[i] + header_x[i + 1]) / 2 for i in range(3)]
        weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        day_words = sorted(
            (word for word in words if word[4].casefold().strip(".,:") in weekdays),
            key=lambda word: (word[1] + word[3]) / 2,
        )
        if day_words:
            centers = [(word[1] + word[3]) / 2 for word in day_words]
            header_y = (header_words["day"][1] + header_words["day"][3]) / 2
            footer_top = page.rect.height
            regards = [word for word in words if word[4].casefold().strip(".,:") == "regards"]
            with_words = [word for word in words if word[4].casefold().strip(".,:") == "with"]
            for regards_word in regards:
                regards_y = (regards_word[1] + regards_word[3]) / 2
                if any(abs((word[1] + word[3]) / 2 - regards_y) <= 3 for word in with_words):
                    footer_top = min(word[1] for word in [regards_word, *with_words]
                                     if abs((word[1] + word[3]) / 2 - regards_y) <= 3)
                    break
            lines = []
            for idx, day_word in enumerate(day_words):
                top = (header_y + centers[idx]) / 2 if idx == 0 else (centers[idx - 1] + centers[idx]) / 2
                bottom = (centers[idx] + centers[idx + 1]) / 2 if idx + 1 < len(centers) else page.rect.height
                columns = [[], [], [], []]
                for word in words:
                    cy = (word[1] + word[3]) / 2
                    if top <= cy < bottom and word[1] < footer_top:
                        x = (word[0] + word[2]) / 2
                        col = 0 if x < cuts[0] else 1 if x < cuts[1] else 2 if x < cuts[2] else 3
                        columns[col].append(word)
                values = []
                for col in columns:
                    col.sort(key=lambda word: ((word[1] + word[3]) / 2, word[0]))
                    values.append(" ".join(word[4] for word in col))
                lines.append(
                    f"{day_word[4].title()} | Breakfast: {values[1]} | Lunch: {values[2]} | Dinner: {values[3]}"
                )
            aligned = "\n".join(lines)
            # Trust geometry when every weekday has all three meal cells.
            # Spend a short local-model call only when the layout still has
            # gaps, where semantic row assignment can help.
            complete_rows = all(
                all(value.strip() for value in line.split(" | ")[1:])
                for line in lines
            )
            structured = "" if complete_rows else _llm_structure_menu(aligned)
            return clean_text((structured + "\n\n" if structured else "") + "Menu rows (layout aligned):\n" + aligned)
    words.sort(key=lambda word: ((word[1] + word[3]) / 2, word[0]))
    lines = []
    for word in words:
        center = (word[1] + word[3]) / 2
        if not lines or center - lines[-1][0] > 3.5:
            lines.append([center, [word]])
        else:
            lines[-1][1].append(word)

    parsed = []
    for center, line_words in lines:
        line_words.sort(key=lambda word: word[0])
        cells = []
        for word in line_words:
            if cells and word[0] - cells[-1][-1][2] > 24:
                cells.append([word])
            elif cells:
                cells[-1].append(word)
            else:
                cells.append([word])
        parsed.append((center, line_words, [[cell[0][0], " ".join(word[4] for word in cell)] for cell in cells]))

    # A header row gives stable semantic labels for subsequent cell values.
    header_index = None
    labels = []
    menu_anchors = []
    for index, (_center, _words, cells) in enumerate(parsed):
        names = [text.strip().rstrip(":") for _x, text in cells]
        row_tokens = {token.casefold() for word in _words for token in re.findall(r"[\w]+", word[4])}
        if MENU_HEADERS.issubset(row_tokens):
            header_index = index
            labels = ["Day", "Breakfast", "Lunch", "Dinner"]
            menu_anchors = [
                min(word[0] for word in _words if token in re.findall(r"[\w]+", word[4].casefold()))
                for token in ("day", "breakfast", "lunch", "dinner")
            ]
            break
        if len(names) >= 2 and (
            any(name.lower() in {"date", "remarks", "activity", "event"} for name in names)
            or MENU_HEADERS.issubset({name.lower() for name in names})
        ):
            header_index, labels = index, names
            break

    # Body text in two-column PDFs is often stored as visually aligned rows.
    # Reading those rows left-to-right interleaves unrelated paragraphs, so
    # detect stable prose columns and emit each column in reading order. Known
    # tables keep their row layout through the header-aware path below.
    if header_index is None:
        paired = [cells for _center, _line_words, cells in parsed if len(cells) == 2]
        prose_pairs = [cells for cells in paired if all(len(text.split()) >= 5 for _x, text in cells)]
        if len(prose_pairs) >= 3:
            left_x = [cells[0][0] for cells in prose_pairs]
            right_x = [cells[1][0] for cells in prose_pairs]
            stable_left = max(left_x) - min(left_x) <= 20
            stable_right = max(right_x) - min(right_x) <= 20
            separated = min(right_x) - max(left_x) >= 60
            if stable_left and stable_right and separated:
                left_column = " ".join(cells[0][1] for cells in prose_pairs)
                right_column = " ".join(cells[1][1] for cells in prose_pairs)
                return clean_text(f"Column 1: {left_column}\nColumn 2: {right_column}")

    anchors = menu_anchors or ([x for x, _name in parsed[header_index][2]] if header_index is not None else [])
    output = []
    for index, (_center, line_words, cells) in enumerate(parsed):
        if index == header_index:
            output.append("Table columns: " + " | ".join(labels))
            continue
        if len(anchors) >= 2 and index > header_index:
            grouped = [[] for _ in anchors]
            for word in line_words:
                column = max((i for i, anchor in enumerate(anchors) if word[0] >= anchor - 3), default=0)
                grouped[column].append(word[4])
            values = [" ".join(group).strip() for group in grouped]
            output.append(" | ".join(
                f"{labels[i] if i < len(labels) else f'Column {i + 1}'}: {value}"
                for i, value in enumerate(values) if value
            ))
        else:
            output.append(" ".join(text for _x, text in cells))
    extracted = clean_text("\n".join(output))
    if header_index is not None and MENU_HEADERS.issubset({name.lower() for name in labels}):
        structured = _llm_structure_menu(extracted)
        if structured:
            return clean_text(structured + "\n\nOriginal layout extraction:\n" + extracted)
    return join_dated_pdf_rows(extracted)


_PDF_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_PDF_DATE_ROW_START = re.compile(
    rf"^(?:{_PDF_MONTH})\.?\s+\d{{1,2}}(?:\s*[–-]\s*(?:(?:{_PDF_MONTH})\.?\s+)?\d{{1,2}})?,?\s+\d{{4}}\b",
    re.IGNORECASE,
)


def join_dated_pdf_rows(text):
    """Join wrapped table cells to their date row without crossing row/page headings."""
    output, current = [], None
    for raw_line in text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        if _PDF_DATE_ROW_START.match(line):
            if current:
                output.append(current)
            current = line
        elif current and re.match(r"^(?:Section\s+\d|IIT Indore\s+[—-]|Table columns:)", line, re.IGNORECASE):
            output.append(current)
            current = None
            output.append(line)
        elif current:
            current = f"{current} {line}"
        else:
            output.append(line)
    if current:
        output.append(current)
    return "\n".join(output)


def extract(filename, data):
    ext = PurePath(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError("Use PDF, TXT, MD, PNG or JPEG")
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("File must be non-empty and at most 20 MiB")
    if ext in {".txt", ".md"}:
        try:
            value = clean_text(data.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError("Save text files as UTF-8") from exc
        pages, doc_type = [{"page": 1, "text": value}], "text"
    elif ext == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise ValueError("File is not a PDF")
        pages, doc_type, scans, total = [], "pdf", 0, 0
        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or not 0 < len(pdf) <= MAX_PDF_PAGES:
                raise ValueError(f"Use an unlocked PDF with at most {MAX_PDF_PAGES} pages")
            for page in pdf:
                value = extract_pdf_page(page)
                if len(value) < 10 and page.rect.width > 0 and page.rect.height > 0:
                    scans += 1
                    if scans > MAX_SCANNED_PAGES:
                        raise ValueError(f"Split scanned PDFs into batches of at most {MAX_SCANNED_PAGES} scanned pages")
                    scale = min(2.5, (MAX_PIXELS / page.rect.get_area()) ** 0.5 * 0.95)
                    value = ocr(page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB))
                total += len(value)
                if total > MAX_TEXT:
                    raise ValueError("Extracted text exceeds 200,000 characters; split the file")
                pages.append({"page": page.number + 1, "text": value})
    else:
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") if ext == ".png" else data.startswith(b"\xff\xd8\xff")):
            raise ValueError("Image bytes do not match the file extension")
        try:
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Image exceeds the 16 megapixel limit")
                if image.format != ("PNG" if ext == ".png" else "JPEG"):
                    raise ValueError("Image bytes do not match the file extension")
                foreground = image.convert("RGBA")
                background = Image.new("RGBA", foreground.size, "white")
                background.alpha_composite(foreground)
                output = BytesIO()
                background.convert("RGB").save(output, format="PNG")
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            raise ValueError("Image is damaged or too large") from exc
        value = ocr_png(output.getvalue())
        pages, doc_type = [{"page": 1, "text": value}], "image"
    validate_pages(pages)
    return doc_type, pages


def validate_pages(pages):
    value = "\n".join(p["text"] for p in pages).strip()
    if len(value) > MAX_TEXT:
        raise ValueError("Extracted text exceeds 200,000 characters; split the file")
    if len(value) < 20:
        raise ValueError("Too little readable text. Try a clearer scan or upload a text transcript.")
    return value
