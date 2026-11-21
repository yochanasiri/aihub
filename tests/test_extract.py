import pymupdf
import pytest
from crawler.extract import extract_html, extract_pdf

def test_html():
    result = extract_html(b'''<html><title>Academics</title><nav><a href="/dept">Departments</a></nav>
    <main><h1>Programs</h1><p>Official academic information.</p>
    <table><tr><td>B.Tech</td><td>4 years</td></tr></table><a href="/rules.pdf">Rules</a>
    <a href="https://evil.com">External</a></main><script>injected()</script><footer>Footer</footer></html>''',
    'https://iiti.ac.in/', ['iiti.ac.in'])
    assert '# Programs' in result.text and 'B.Tech | 4 years' in result.text
    assert 'Footer' not in result.text and 'injected' not in result.text
    assert result.links == ['https://iiti.ac.in/dept', 'https://iiti.ac.in/rules.pdf']
    assert result.metadata['headings'] == ['Programs']

def test_pdf():
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72,72), 'Academic regulations fixture')
        result = extract_pdf(doc.tobytes(), 'https://iiti.ac.in/rules.pdf', 2)
    assert result.metadata['pages'][0]['page'] == 1 and 'Academic regulations' in result.text

def test_scanned_pdf():
    with pymupdf.open() as doc:
        doc.new_page()
        with pytest.raises(ValueError, match='OCR'): extract_pdf(doc.tobytes(), 'https://iiti.ac.in/a.pdf', 2)
