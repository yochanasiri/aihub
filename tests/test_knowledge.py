import shutil

import httpx
import pymupdf
import pytest
from sqlalchemy import func, select

from backend.app.knowledge_ingest import extract, join_dated_pdf_rows
from backend.app.main import create_app
from backend.app.models import Message, User
from backend.app.rag.vector import RetrievalFilters
from test_rag import rig

pytestmark = pytest.mark.filterwarnings('ignore:Payload indexes have no effect in the local Qdrant')
TEXT = "Admission applications for the example program close on 15 June. Contact the academic office."


def pdf_fixture(scanned=False):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 100), TEXT, fontsize=10)
        if not scanned:
            return document.tobytes()
        image = page.get_pixmap(matrix=pymupdf.Matrix(2,2))
        with pymupdf.open() as scan:
            scan.new_page().insert_image(page.rect, pixmap=image)
            return scan.tobytes(), image.tobytes("png"), image.tobytes("jpeg")


def test_pdf_text():
    kind, pages = extract("notice.pdf", pdf_fixture())
    assert kind == "pdf"
    assert pages == [{"page":1,"text":TEXT}]
    with pytest.raises(ValueError):
        extract("image.jpg", b"not an image")


def test_pdf_two_column_prose_is_extracted_column_by_column():
    with pymupdf.open() as document:
        page = document.new_page()
        left = [
            "Admission review begins after applications close.",
            "Applicants must upload all required certificates.",
            "Late applications are not considered for this cycle.",
            "Submit corrections to the admissions office.",
        ]
        right = [
            "Scholarship decisions follow the official merit list.",
            "Awards are announced after committee approval.",
            "Recipients receive notification by institute email.",
            "The award covers tuition for eligible students.",
        ]
        for index, (left_line, right_line) in enumerate(zip(left, right)):
            y = 70 + index * 20
            page.insert_text((40, y), left_line, fontsize=10)
            page.insert_text((320, y), right_line, fontsize=10)
        kind, pages = extract("columns.pdf", document.tobytes())
    text = pages[0]["text"]
    assert kind == "pdf"
    assert text.startswith("Column 1: ")
    assert text.index(left[-1]) < text.index(right[0])
    assert text.endswith("Column 2: " + " ".join(right))


def test_pdf_wrapped_table_cells_stay_with_their_date_row():
    text = ("Oct. 5, 2026 (Monday) Classes resume after mid semester\n"
            "break All students\n"
            "Oct. 10, 2026 (Saturday) Last date for showing evaluated answer sheets\n"
            "of the mid semester examination All courses")
    rows = join_dated_pdf_rows(text).splitlines()
    assert rows == [
        "Oct. 5, 2026 (Monday) Classes resume after mid semester break All students",
        "Oct. 10, 2026 (Saturday) Last date for showing evaluated answer sheets of the mid semester examination All courses",
    ]


@pytest.mark.skipif(not shutil.which("tesseract"), reason="OCR engine unavailable")
def test_scans_and_images_use_real_ocr():
    pdf, png, jpeg = pdf_fixture(scanned=True)
    for name, raw in [("scan.pdf",pdf),("photo.png",png),("photo.jpeg",jpeg)]:
        assert "15 June" in extract(name,raw)[1][0]["text"]


async def test_admin_publish_retrieve_tune_and_unpublish(rig):
    app = create_app(rig.config, rig.service, rig.auth)
    async with rig.sessions() as db:
        admin = User(email="admin@iiti.ac.in",name="Admin",role="Admin",email_verified=True,is_demo=False)
        db.add(admin)
        await db.flush()
        grant = await rig.auth.grant(db,admin,"password")
        await db.commit()
    admin = {"Authorization":"Bearer "+grant["session_token"]}
    student = {"Authorization":"Bearer "+rig.session_token}
    upload_headers = {"Content-Type":"application/octet-stream","X-AIHUB-Filename":"Example%20notice.txt"}
    tuning = {"top_k":2,"min_score":40}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        assert (await c.post("/v1/admin/knowledge/upload",headers={**student,**upload_headers},content=TEXT)).status_code == 403
        assert (await c.patch("/v1/admin/retrieval-profile",headers=student,json=tuning)).status_code == 403
        response = await c.post("/v1/admin/knowledge/upload",headers={**admin,**upload_headers},content=TEXT)
        assert response.status_code == 201, response.text
        doc = response.json()
        assert not doc["published"] and doc["title"] == "Example notice.txt"
        id = doc["id"]
        endpoint = "/v1/admin/knowledge/"+id
        source = "/v1/knowledge/"+id
        assert (await c.get(source,headers=student)).status_code == 404
        assert (await c.get(endpoint,headers=student)).status_code == 403
        await rig.store.ensure()
        hits, _ = await rig.service.retriever.retrieve("admission", RetrievalFilters())
        assert not hits
        revised = TEXT.replace("15 June","16 June")
        assert (await c.patch(endpoint,headers=admin,json={"title":"Admission notice","department":"Academics","pages":[revised]})).status_code == 200
        response = await c.post(endpoint+"/publish",headers=admin,json={})
        assert response.status_code == 200, response.text
        hits, _ = await rig.service.retriever.retrieve("When do admission applications close?", RetrievalFilters())
        assert len(hits) == 1 and hits[0].text == revised
        assert hits[0].source["url"] == "/api/campus/knowledge/"+id
        assert revised in (await c.get(source,headers=student)).text
        assert (await c.get(source)).status_code == 401
        response = await c.post("/v1/admin/retrieval-preview",headers=admin,json={**tuning,"question":"When do admission applications close?"})
        assert response.status_code == 200, response.text
        assert "16 June" in response.json()["answer"]
        assert response.json()["candidates"][0]["text"] == revised
        async with rig.sessions() as db:
            assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert (await c.patch("/v1/admin/retrieval-profile",headers=admin,json=tuning)).json() == tuning
        assert (await c.get("/v1/admin/retrieval-profile",headers=admin)).json() == tuning
        strict = {"top_k":2,"min_score":95}
        assert (await c.patch("/v1/admin/retrieval-profile",headers=admin,json=strict)).status_code == 200
        hits, _ = await rig.service.retriever.retrieve("admission and hostel", RetrievalFilters())
        assert not hits
        assert (await c.patch("/v1/admin/retrieval-profile",headers=admin,json=tuning)).status_code == 200
        response = await c.post("/v1/chat/stream",headers=student,json={"question":"When do admission applications close?"})
        assert response.status_code == 200
        assert "16 June" in response.text and "/api/campus/knowledge/"+id in response.text
        assert (await c.post(endpoint+"/unpublish",headers=admin,json={})).status_code == 200
        assert (await c.get(source,headers=student)).status_code == 404
        hits, _ = await rig.service.retriever.retrieve("When do admission applications close?", RetrievalFilters())
        assert not hits
