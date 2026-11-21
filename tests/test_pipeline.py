from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.models import Base, Document, CrawlLog, now
from crawler.extract import Extracted
from crawler.fetch import FetchResult
from crawler.policy import CrawlConfig
from crawler.run import crawl
from crawler.store import save_document, mark_stale, mark_unavailable

@pytest.fixture
async def sessions():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def test_changes_and_duplicates(sessions):
    async with sessions() as s:
        parsed = Extracted('Academics', 'Official text '*20, 'html')
        doc, status = await save_document(s,'https://iiti.ac.in/a',parsed,'Academics',{})
        assert status == 'created' and doc.needs_indexing
        doc.needs_indexing = False
        _, status = await save_document(s,doc.url,parsed,'Academics',{})
        assert status == 'unchanged' and not doc.needs_indexing
        alias, _ = await save_document(s,'https://iiti.ac.in/b',parsed,'Academics',{})
        assert alias.duplicate_of == doc.id
        parsed.text += ' updated'
        _, status = await save_document(s,doc.url,parsed,'Academics',{})
        assert status == 'updated' and doc.needs_indexing
        assert alias.duplicate_of is None and alias.needs_indexing
        await mark_unavailable(s,doc)
        assert not doc.active and doc.text

async def test_stale(sessions):
    async with sessions() as s:
        doc, _ = await save_document(s,'https://iiti.ac.in/a',Extracted('Title','text','html'),'Institute',{})
        doc.last_crawled = now() - timedelta(days=31)
        doc.needs_indexing = False
        await s.flush()
        await mark_stale(s,30)
        assert doc.stale and doc.needs_indexing

class FakeFetcher:
    status = 200
    def fetch(self,url,headers):
        return FetchResult(url,self.status,b'<main><h1>Academics</h1><p>Official fixture text for pipeline testing.</p></main>',
                           {'Content-Type':'text/html','ETag':'"v1"'})
    def close(self): pass

async def test_pipeline(sessions):
    config = CrawlConfig(allowed_domains=['iiti.ac.in'],seeds=[{'url':'https://iiti.ac.in/'}],min_text_characters=20)
    f = FakeFetcher()
    assert (await crawl(config,sessions,f))['counts'] == {'created':1}
    f.status = 304
    assert (await crawl(config,sessions,f))['counts'] == {'unchanged':1}
    f.status = 503
    assert (await crawl(config,sessions,f))['counts'] == {'error':1}
    async with sessions() as s:
        doc = await s.scalar(select(Document))
        assert doc.active and 'Official fixture' in doc.text
    f.status = 404
    await crawl(config,sessions,f)
    async with sessions() as s:
        assert not (await s.scalar(select(Document))).active
        assert len((await s.scalars(select(CrawlLog))).all()) == 8
