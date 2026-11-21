"""Pure-Python tests; run: python -m unittest discover -s tests -p test_structure_sections.py"""
import unittest
from backend.app.rag.structure_sections import match_section, requested_heading


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.pages = [
            {'page': 1, 'text': ('Optum\nJob Description:\n'
             '• Robotics and technical clubs\n'
             '• GitHub engineering projects\n'
             '• Cloud Technologies (AWS, Azure, GCP)\n'
             '• Experience with GIT and GitHub\n')},
            {'page': 2, 'text': ('• IDE and CI/CD deployment pipeline\n'
             '• Understanding of Scrum, Kanban, SAFe, LeSS\n'
             'Eligibility Criteria:\nNo active backlog\n')},
        ]

    def test_complete_multipage_jd(self):
        section = match_section(self.pages, 'What is the complete JD of Optum?', 'Optum.pdf', 'Optum')
        self.assertIsNotNone(section)
        self.assertEqual([p.page for p in section.pieces], [1, 2])
        self.assertEqual(''.join(p.text for p in section.pieces).count('•'), 6)
        self.assertNotIn('No active backlog', ''.join(p.text for p in section.pieces))

    def test_wrong_company_cannot_steal_jd(self):
        self.assertIsNone(match_section(self.pages, 'Full JD of Samsung', 'Optum.pdf', 'Samsung'))

    def test_generic_heading_no_schema(self):
        pages = [{'page': 1, 'text': 'Hostel Rules:\n1. Keep quiet.\n2. Return keys.\nContact Details:\nOffice'}]
        section = match_section(pages, 'List all points in section Hostel Rules', 'hostel.pdf')
        self.assertIn('Return keys', ''.join(p.text for p in section.pieces))
        self.assertNotIn('Office', ''.join(p.text for p in section.pieces))

    def test_preserve_table_rows(self):
        pages = [{'page': 1, 'text': 'Fee Structure:\nTable columns: Year | Fee\nYear 1 | 20000\nYear 2 | 22000\nRefunds:\nOnly by policy'}]
        section = match_section(pages, 'Give complete fee structure', 'fees.pdf')
        self.assertIsNotNone(section)
        self.assertIn('Year 2 | 22000', section.pieces[0].text)
        self.assertNotIn('Refunds', section.pieces[0].text)

    def test_not_a_section_request(self):
        self.assertIsNone(requested_heading('How much is the stipend?'))


if __name__ == '__main__':
    unittest.main()
