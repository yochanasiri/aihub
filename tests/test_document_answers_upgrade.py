import asyncio
import unittest
from types import SimpleNamespace
from backend.app.rag.document_answers import (
    exhaustive_question, is_verified, verify_items, select_full_document, document_answer
)
from backend.app.pdf_layout import extract_tables


def ev(ref, page, text, doc='doc-1'):
    return SimpleNamespace(source_id=ref, text=text,
        source={'document_id':doc,'page':page},
        node=SimpleNamespace(node=SimpleNamespace(metadata={'_full_document_page':True})))


class DocumentAnswerTests(unittest.TestCase):
    def test_generic_query_trigger(self):
        self.assertTrue(exhaustive_question('How many rules apply to hostel visitors?'))
        self.assertTrue(exhaustive_question('List every obligation under this contract'))
        self.assertFalse(exhaustive_question('What is the stipend?'))

    def test_quote_must_be_literal(self):
        self.assertTrue(is_verified('Mid semester break runs from September 27 through October 4.',
            'Mid semester break runs from September 27\nthrough October 4.'))
        self.assertFalse(is_verified('Christmas is on 25 December', 'Excluding Sunday and govt. holiday'))

    def test_verify_rejects_fabricated_rows_and_counts(self):
        p=[ev('S1',1,'Classes before exams exclude Sundays and govt. holidays.'),
           ev('S2',2,'Semester break is December 7 through January 3.')]
        bad={'items':[{'source_id':'S1','quote':'Christmas on December 25',
               'answer':'Christmas — 25 December'}],'complete':True}
        self.assertEqual(verify_items(bad,p,'list holidays')[0],[])

    def test_full_doc_pages_single_source_order(self):
        p=[ev('S2',2,'Page two'),ev('S1',1,'Page one'),ev('S3',1,'Wrong',doc='doc-2')]
        self.assertEqual([x.source_id for x in select_full_document(p)],['S1','S2'])

    def test_llm_uses_full_pages_and_verified_items(self):
        p=[ev('S1',1,'Scholarship A requires a minimum score of 7.0.'),
           ev('S2',2,'Scholarship B is available to postgraduate students.')]
        class Client:
            async def create(self,**kwargs):
                self.kwargs=kwargs
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=
                    '{"items":[{"source_id":"S1","quote":"Scholarship A requires a minimum score of 7.0.","answer":"Scholarship A — minimum score 7.0"},{"source_id":"S2","quote":"Scholarship B is available to postgraduate students.","answer":"Scholarship B — postgraduate students"}],"complete":true}'))])
        client=Client()
        llm=SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(completions=client)),
                            config=SimpleNamespace(llm_model='test',llm_max_tokens=1600))
        items,complete=asyncio.run(document_answer(llm,'List all scholarships',p))
        self.assertEqual(len(items),2)
        self.assertTrue(complete)
        self.assertIn('Scholarship B',client.kwargs['messages'][1]['content'])

    def test_pdf_table_extraction(self):
        class Table:
            def extract(self):return [['Activity','Date'],['Orientation','January 4'],['Classes','January 6']]
        class Page:
            def find_tables(self):return SimpleNamespace(tables=[Table()])
        out=extract_tables(Page())
        self.assertIn('| Activity | Date |',out)
        self.assertIn('| Classes | January 6 |',out)


if __name__=='__main__': unittest.main()
