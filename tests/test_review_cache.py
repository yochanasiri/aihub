import json

import httpx
from redis.exceptions import ConnectionError

from backend.app.main import create_app
from backend.app.models import User
from backend.app.rag.cache import QuoteCache
from test_rag import add, rig, sync


def sse_done(response):
    assert response.status_code == 200, response.text
    return json.loads(response.text.split("event: done\ndata: ")[1].split("\n")[0])


async def test_feedback_and_escalation_respect_ownership(rig):
    app = create_app(rig.config, rig.service, rig.auth)
    student = {"Authorization":f"Bearer {rig.session_token}"}
    async with rig.sessions() as db:
        admin_user = User(email="review@iiti.ac.in", name="Review", role="Admin", email_verified=True, is_demo=False)
        db.add(admin_user)
        await db.flush()
        grant = await rig.auth.grant(db, admin_user, "password")
        await db.commit()
    admin = {"Authorization":"Bearer "+grant["session_token"]}
    await rig.store.ensure()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        data = sse_done(await client.post("/v1/chat/stream",headers=student,json={"question":"Where is the hostel?"}))
        assert data["escalation_available"]
        path = "/v1/messages/"+data["message_id"]
        assert (await client.post(path+"/feedback",headers=admin,json={"rating":1})).status_code == 404
        assert (await client.post(path+"/escalate",headers=admin,json={})).status_code == 404
        assert (await client.get("/v1/admin/review",headers=student)).status_code == 403
        assert (await client.post(path+"/feedback",headers=student,json={"rating":1})).status_code == 200
        assert (await client.post(path+"/feedback",headers=student,json={"rating":-1,"comment":"Missing hostel details"})).status_code == 200
        created = (await client.post(path+"/escalate",headers=student,json={"description":"Please add the source"})).json()
        repeated = (await client.post(path+"/escalate",headers=student,json={})).json()
        assert created["id"] == repeated["id"]
        assert (await client.get("/v1/feedback/me",headers=admin)).json()["items"] == []
        queue = (await client.get("/v1/admin/review",headers=admin)).json()
        assert len(queue["ratings"]) == len(queue["tickets"]) == len(queue["gaps"]) == 1
        assert queue["ratings"][0]["comment"] == "Missing hostel details"
        assert queue["tickets"][0]["question"] == "Where is the hostel?"
        assert (await client.patch("/v1/admin/gaps/"+queue["gaps"][0]["id"],headers=admin,json={})).status_code == 409
        assert (await client.patch("/v1/admin/review/"+created["id"],headers=admin,json={"status":"resolved"})).status_code == 200
        assert (await client.get("/v1/feedback/me",headers=student)).json()["items"][0]["status"] == "resolved"
        messages = (await client.get(f"/v1/conversations/{data['conversation_id']}/messages",headers=student)).json()["items"]
        assert messages[-1]["feedback"]["rating"] == -1
        assert messages[-1]["escalation_status"] == "resolved"


class MemoryRedis:
    def __init__(self):
        self.rows = {}
        self.offline = False

    async def get(self,key):
        if self.offline:
            raise ConnectionError("fixture offline")
        return self.rows.get(key)

    async def set(self,key,value,ex):
        if self.offline:
            raise ConnectionError("fixture offline")
        assert ex == 300
        self.rows[key] = value


async def test_cache_saves_generation_but_rechecks_source_and_falls_back(rig):
    doc = await add(rig)
    await sync(rig)
    redis = MemoryRedis()
    rig.config.metrics_enabled = True
    rig.service.cache = QuoteCache(rig.config, redis)
    app = create_app(rig.config, rig.service, rig.auth)
    student = {"Authorization":f"Bearer {rig.session_token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        async def ask():
            return sse_done(await client.post("/v1/chat/stream",headers=student,json={"question":"Where are the admission details?"}))
        first, second = await ask(), await ask()
        assert first["status"] == second["status"] == "answered"
        assert first["message_id"] != second["message_id"]
        assert second["metrics"]["cache_hit"] and rig.llm.calls == 1
        redis.rows[next(iter(redis.rows))] = b'[{"source_id":"S1","quote":"A forged statement with no source."}]'
        assert not (await ask())["metrics"]["cache_hit"]
        assert rig.llm.calls == 2
        redis.offline = True
        assert (await ask())["status"] == "answered" and rig.llm.calls == 3
        redis.offline = False
        async with rig.sessions() as db:
            row = await db.get(type(doc), doc.id)
            row.active = False
            await db.commit()
        result = await ask()
        assert result["status"] == "unanswered" and not result["metrics"]["cache_hit"]
        assert rig.llm.calls == 3
        metrics = (await client.get("/metrics")).text
        assert "aihub_rag_duration_seconds_bucket" in metrics
        assert "aihub_rag_cache_hits_total" in metrics
        assert "Where are the admission details?" not in metrics
