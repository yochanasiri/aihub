"""Regression tests reproducing the role layout in the Deloitte handbook screenshot.
Run: python -m unittest discover -s tests -p test_nested_role_answers.py -v
"""
import unittest
from backend.app.rag.structure_sections import match_section
from backend.app.rag.focused import fields, intent, structured_answer_markdown, entity_matches, focused_quotes

PAGES = [
    {'page': 12, 'text': '''4. Deloitte
Role: Analyst, Business Analyst
Job Description:
Analyst
• Develop and implement AI/ML models using NLP, Graph Neural Networks, and time series.
• Work on cloud-based data ecosystems, combining structured and unstructured data.
• Apply data analytics and robotics to client problems.
• Collaborate on innovation and proof-of-concept creation.
Business Analyst
• Analyze business problems, define requirements, and deliver insights.
• Conduct research, root cause analysis, and data synthesis.
• Work in global cross-functional teams across projects and roles.
• Requires strong communication, problem solving and collaboration skills.
Stipend: 900000 per year
Eligibility Criteria: No Backlog
'''},
    {'page': 13, 'text': '''5. L&T Finance
Job Description:
• Responsible for application development.
Stipend: 1000000 per year
'''}
]

class RoleAnswers(unittest.TestCase):
    def test_business_analyst_only(self):
        s=match_section(PAGES, 'job description for Deloitte Business Analyst', 'Placement Experience Handbook.pdf', 'Deloitte Business Analyst')
        self.assertIsNotNone(s)
        content='\n'.join(p.text for p in s.pieces)
        self.assertEqual(content.count('•'), 4)
        self.assertIn('Analyze business problems', content)
        self.assertNotIn('Develop and implement AI/ML', content)
        self.assertNotIn('900000', content)

    def test_analyst_only(self):
        s=match_section(PAGES, 'job description for Deloitte Analyst', 'Placement Experience Handbook.pdf', 'Deloitte Analyst')
        self.assertIsNotNone(s)
        content='\n'.join(p.text for p in s.pieces)
        self.assertEqual(content.count('•'), 4)
        self.assertIn('Develop and implement AI/ML',content)
        self.assertNotIn('Analyze business problems',content)

    def test_all_roles_when_role_not_specified(self):
        s=match_section(PAGES,'job description of Deloitte','Placement Experience Handbook.pdf','Deloitte')
        content='\n'.join(p.text for p in s.pieces)
        self.assertEqual(content.count('•'),8)
        self.assertNotIn('Stipend:',content)
        self.assertNotIn('L&T Finance',content)

    def test_no_company_leakage(self):
        s=match_section(PAGES,'job description of Samsung','Placement Experience Handbook.pdf','Samsung')
        self.assertIsNone(s)

    def test_entity_with_role_validates(self):
        self.assertTrue(entity_matches('Deloitte Business Analyst',PAGES[0]['text']))
        self.assertFalse(entity_matches('Samsung Business Analyst',PAGES[0]['text']))

    def test_stipend_uses_requested_company_only(self):
        # Both companies on one page, a frequent placement-handbook case.
        from types import SimpleNamespace
        text = "4. Deloitte\nRole: Analyst\nStipend: 900000 per year\n5. L&T Finance\nRole: Software Engineer\nStipend: 1000000 per year\n"
        node=SimpleNamespace(node=SimpleNamespace(metadata={'_source_page_text':text,'_document_text':text}))
        source=SimpleNamespace(node=node, text=text, source_id='S1', source={'title':'Handbook.pdf'})
        quotes=focused_quotes('stipend of Deloitte',[source])
        self.assertEqual(len(quotes),1)
        self.assertIn('900000 per year',quotes[0].answer)
        self.assertNotIn('1000000',quotes[0].answer)
        wrong=focused_quotes('stipend of Samsung',[source])
        self.assertEqual(wrong,[])

    def test_stipend_field_is_atomic(self):
        f=list(fields(PAGES[0]['text']))
        stipend=[(label,value) for label,value,*_ in f if label=='stipend']
        self.assertEqual(stipend,[('stipend','900000 per year')])
        self.assertEqual(intent('stipend of Deloitte'),'stipend')

    def test_markdown_points_are_numbered(self):
        answer=structured_answer_markdown('Job Description:\nBusiness Analyst\n• Analyze business problems\n• Conduct research')
        self.assertIn('1. Analyze business problems',answer)
        self.assertIn('2. Conduct research',answer)
        self.assertNotIn('•',answer)

if __name__=='__main__':unittest.main()
