from datetime import datetime
from qdrant_client import AsyncQdrantClient, models as qm
from qdrant_client.http.exceptions import UnexpectedResponse
from pydantic import BaseModel, Field, model_validator
from ..config import Settings


class RetrievalFilters(BaseModel):
    department: str | None = Field(None, max_length=200)
    doc_type: str | None = Field(None, pattern=r"^(html|pdf|text|image)$")
    source_url: str | None = Field(None, max_length=2048)
    date_from: datetime | None = None
    date_to: datetime | None = None

    @model_validator(mode="after")
    def dates(self):
        for value in (self.date_from, self.date_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("Date filters require timezone-aware timestamps")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not exceed date_to")
        return self


def condition(key, value):
    return qm.FieldCondition(key=key, match=qm.MatchValue(value=value))


class VectorStore:
    def __init__(self, config: Settings, dim: int, client=None):
        self.config, self.dim = config, dim
        self.client = client or AsyncQdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key, timeout=15, trust_env=False)
        self.collection = config.collection_name

    async def ensure(self):
        created = not await self.client.collection_exists(self.collection)
        if created:
            await self.client.create_collection(self.collection,
                vectors_config=qm.VectorParams(size=self.dim, distance=qm.Distance.COSINE),
                hnsw_config=qm.HnswConfigDiff(m=16, ef_construct=100))
        info = await self.client.get_collection(self.collection)
        if info.config.params.vectors.size != self.dim or info.config.params.vectors.distance != qm.Distance.COSINE:
            raise ValueError("Qdrant collection dimension/distance mismatch; use a new collection")
        indexes = {"document_id":qm.PayloadSchemaType.KEYWORD, "revision":qm.PayloadSchemaType.KEYWORD,
                   "index_signature":qm.PayloadSchemaType.KEYWORD, "source_url":qm.PayloadSchemaType.KEYWORD,
                   "department":qm.PayloadSchemaType.KEYWORD, "doc_type":qm.PayloadSchemaType.KEYWORD,
                   "date":qm.PayloadSchemaType.DATETIME, "verified_domain":qm.PayloadSchemaType.BOOL}
        for field, schema in indexes.items():
            if field not in info.payload_schema:
                await self.client.create_payload_index(self.collection, field, schema, wait=True)

        return created

    async def write(self, windows, vectors):
        if len(windows) != len(vectors):
            raise ValueError("Vector count mismatch")
        if not windows:
            return
        points = [qm.PointStruct(id=w.id, vector=v, payload={**w.node.metadata,
                  "text":w.node.text, "search_text":w.text}) for w,v in zip(windows,vectors)]
        await self.client.upsert(self.collection, points=points, wait=True)

    async def delete_document(self, document_id: str, keep_revision: str | None = None):
        filt = qm.Filter(must=[condition("document_id", document_id)],
                         must_not=[condition("revision", keep_revision)] if keep_revision else None)
        await self.client.delete(self.collection, points_selector=qm.FilterSelector(filter=filt), wait=True)

    async def search(self, vector, filters: RetrievalFilters, limit: int, min_score: float | None = None):
        must = [condition("verified_domain", True), condition("index_signature", self.config.index_signature)]
        for field in ("department", "doc_type", "source_url"):
            if value := getattr(filters, field):
                must.append(condition(field, value))
        if filters.date_from or filters.date_to:
            must.append(qm.FieldCondition(key="date", range=qm.DatetimeRange(gte=filters.date_from, lte=filters.date_to)))
        try:
            result = await self.client.query_points(self.collection, query=vector,
                query_filter=qm.Filter(must=must), limit=limit, with_payload=True,
                score_threshold=self.config.retrieval_min_score if min_score is None else min_score,
                search_params=qm.SearchParams(hnsw_ef=128))
        except (UnexpectedResponse, ValueError) as exc:
            # A fresh installation has no collection until the first publication.
            # Preserve upstream failures when the collection actually exists.
            if isinstance(exc, UnexpectedResponse) and exc.status_code != 404:
                raise
            if not await self.client.collection_exists(self.collection):
                return []
            raise
        return result.points

    async def close(self):
        await self.client.close()
