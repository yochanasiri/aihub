"""Fast, offline validation of the crawler-to-AIHUB conversion boundary."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# The parser has DB imports only for its async importer. Stub them for pure
# parser tests so we can run the tests without a live PostgreSQL instance.
sys.modules.setdefault('backend', types.ModuleType('backend'))
sys.modules.setdefault('backend.app', types.ModuleType('backend.app'))
db = types.ModuleType('backend.app.db'); db.Session=MagicMock(); db.engine=MagicMock()
models = types.ModuleType('backend.app.models'); models.CrawlLog=MagicMock()
extract = types.ModuleType('crawler.extract')
class Extracted:
    def __init__(self, title, text, doc_type, links=None, metadata=None):
        self.title,self.text,self.doc_type,self.links,self.metadata=title,text,doc_type,links or [],metadata or {}
extract.Extracted=Extracted
store=types.ModuleType('crawler.store'); store.save_document=MagicMock()
sys.modules['backend.app.db']=db
sys.modules['backend.app.models']=models
sys.modules.setdefault('crawler',types.ModuleType('crawler'))
sys.modules['crawler.extract']=extract
sys.modules['crawler.store']=store

MODULE_FILE = Path(__file__).parents[1] / 'backend' / 'app' / 'site_import.py'
spec = importlib.util.spec_from_file_location('site_import_tested', MODULE_FILE)
mod = importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class ConversionTests(unittest.TestCase):
    def test_whitelist(self):
        self.assertTrue(mod.valid_public_url('https://academic.iiti.ac.in/test.pdf'))
        self.assertFalse(mod.valid_public_url('https://iiti.ac.in.evil.com/x'))
        self.assertFalse(mod.valid_public_url('http://iiti.ac.in/x'))
        self.assertFalse(mod.valid_public_url('https://google.com/x'))
    def test_page_boundaries(self):
        pages = mod.pdf_pages('# Source\n## Page 1\n\nJD\n- one\n## Page 2\n\n- two')
        self.assertEqual([p['page'] for p in pages], [1,2])
        self.assertIn('- two', pages[1]['text'])
    def test_html_and_pdf_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'markdown').mkdir()
            url='https://www.iiti.ac.in/about'
            (root/'markdown'/'html.md').write_text('# IIT Indore\n\n'+('Institute information. '*20), encoding='utf-8')
            rec={'status':'html_extracted','text_path':'/other-machine/markdown/html.md','title':'About IIT Indore','mime':'text/html','sha256':'abc'}
            item=mod.record_to_extracted(root,url,rec)
            self.assertEqual(item.doc_type, 'html')
            self.assertEqual(item.metadata['origin'], 'iiti_deep_crawler')
            (root/'markdown'/'pdf.md').write_text('# File\n\n## Page 1\n\n'+('JD bullet. '*20)+'\n\n## Page 2\n\n'+('More bullets. '*20),encoding='utf-8')
            doc=mod.record_to_extracted(root,'https://www.iiti.ac.in/notice.pdf',{'status':'pdf_extracted','text_path':'/tmp/pdf.md','mime':'application/pdf','page_count':2})
            self.assertEqual(doc.doc_type,'pdf')
            self.assertEqual(len(doc.metadata['pages']),2)
            with self.assertRaises(ValueError):
                mod.record_to_extracted(root,'https://www.iiti.ac.in/notice.pdf',{'status':'pdf_partial_review_needed','text_path':'/tmp/pdf.md'})
    def test_missing_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'markdown').mkdir()
            import json
            (root/'manifest.json').write_text(json.dumps({'https://www.iiti.ac.in/about': {'status':'html_extracted','text_path':'missing.md'}, 'https://www.iiti.ac.in/broken': {'status':'failed'}}))
            rows, skipped = mod.plan(root)
            self.assertEqual(rows,[])
            self.assertEqual(skipped['failed'],1)
            self.assertEqual(skipped['invalid_or_missing_text'],1)

if __name__=='__main__':unittest.main()
