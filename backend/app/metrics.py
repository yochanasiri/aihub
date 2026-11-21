"""Low-cardinality service metrics. No user, question or document identifiers."""
from prometheus_client import CollectorRegistry, Counter, Histogram

registry = CollectorRegistry()
requests = Counter("aihub_rag_requests_total", "RAG attempts by outcome", ("outcome","mode"), registry=registry)
hits = Counter("aihub_rag_cache_hits_total", "Verified quote cache hits", registry=registry)
latency = Histogram("aihub_rag_duration_seconds", "Time spent processing the RAG stream", ("mode",),
                    buckets=(.1,.25,.5,1,2,4,8,15,30,60,120,300), registry=registry)
