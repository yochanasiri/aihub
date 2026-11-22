import asyncio
import threading
from functools import lru_cache
from fastembed import TextEmbedding
from tokenizers import Tokenizer
from ..config import Settings


class QueryTooLong(ValueError):
    pass


class BGEEmbedder:
    """One local ONNX model per process; offloaded and serialized to bound CPU usage."""
    def __init__(self, config: Settings):
        self.config = config
        self._lock = threading.RLock()
        self._model = None
        self._tokenizer = None
        self.dim = TextEmbedding.get_embedding_size(config.embedding_model)

    def _load(self):
        with self._lock:
            if self._model is None:
                model = TextEmbedding(model_name=self.config.embedding_model,
                                      cache_dir=self.config.embedding_cache_dir,
                                      threads=self.config.embedding_threads)
                # Pinned FastEmbed ONNX implementation exposes the model tokenizer.
                # Clone it: never disable truncation/padding on the inference tokenizer.
                tokenizer = Tokenizer.from_str(model.model.tokenizer.to_str())
                tokenizer.no_truncation()
                tokenizer.no_padding()
                self._model, self._tokenizer = model, tokenizer

    def tokens(self, value: str):
        self._load()
        return self._tokenizer.encode(value, add_special_tokens=False).ids

    def windows(self, value: str):
        self._load()
        encoding = self._tokenizer.encode(value, add_special_tokens=False)
        offsets = encoding.offsets
        size, overlap = self.config.search_window_tokens, self.config.search_window_overlap
        result, start = [], 0
        while start < len(offsets):
            stop = min(start + size, len(offsets))
            text = value[offsets[start][0]:offsets[stop-1][1]].strip()
            # Wordpiece fragments can retokenize differently when sliced: shrink if necessary.
            while len(self.tokens(text)) > size and stop > start + 1:
                stop -= 1
                text = value[offsets[start][0]:offsets[stop-1][1]].strip()
            if text:
                result.append(text)
            if stop == len(offsets):
                break
            start = max(start + 1, stop - overlap)
        return result

    @lru_cache(maxsize=512)
    def _passage(self, value: str):
        with self._lock:
            self._load()
            if len(self.tokens(value)) > self.config.search_window_tokens:
                raise ValueError("Embedding input would exceed the configured token window")
            return tuple(float(v) for v in next(self._model.passage_embed([value])))

    async def passages(self, values: list[str]):
        # Small bounded batches; duplicate passages reuse vectors, not LLM answers.
        return await asyncio.to_thread(lambda: [list(self._passage(value)) for value in values])

    async def private_passages(self, values: list[str]):
        def embed():
            with self._lock:
                self._load()
                return list(self._model.passage_embed(values, batch_size=16))
        return await asyncio.to_thread(embed)

    async def validate_query(self, value: str):
        text = "Represent this sentence for searching relevant passages: " + value
        if len(await asyncio.to_thread(self.tokens, text)) > self.config.search_window_tokens:
            raise QueryTooLong("Please shorten the question to fit the embedding model input limit.")

    async def query(self, value: str):
        def embed():
            with self._lock:
                self._load()
                text = "Represent this sentence for searching relevant passages: " + value
                if len(self.tokens(text)) > self.config.search_window_tokens:
                    raise QueryTooLong("Please shorten the question to fit the embedding model's input limit.")
                return next(self._model.query_embed(text)).tolist()
        return await asyncio.to_thread(embed)

    async def warmup(self):
        await asyncio.to_thread(self._load)
