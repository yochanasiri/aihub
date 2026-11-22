"""Conservative PyMuPDF table extraction shared by website crawling and uploads.

Stores rows as Markdown (with column labels) in addition to existing page text.
No OCR/LLM invention. If table detection fails, original extraction is retained.
"""
import re


def _cell(value):
    return re.sub(r'\s+', ' ', str(value or '')).replace('|', r'\|').strip()


def extract_tables(page, *, max_tables=20, max_rows=250, max_columns=20):
    result = []
    try:
        finder = page.find_tables()
        tables = list(finder.tables)[:max_tables]
    except (AttributeError, RuntimeError, ValueError, TypeError):
        return ''
    for table_no, table in enumerate(tables, 1):
        try:
            rows = table.extract()
        except (RuntimeError, ValueError, TypeError):
            continue
        if not rows or len(rows) < 2 or len(rows[0]) < 2:
            continue
        if len(rows) > max_rows or max(len(r) for r in rows) > max_columns:
            continue
        width = max(len(r) for r in rows)
        grid = [[_cell(r[i]) if i < len(r) else '' for i in range(width)] for r in rows]
        if sum(bool(c) for row in grid for c in row) < 4:
            continue
        result.append(f'### Detected table {table_no}')
        result.append('| ' + ' | '.join(grid[0]) + ' |')
        result.append('|' + '|'.join(['---']*width) + '|')
        for row in grid[1:]:
            if any(row):
                result.append('| ' + ' | '.join(row) + ' |')
        result.append('')
    return '\n'.join(result).strip()
