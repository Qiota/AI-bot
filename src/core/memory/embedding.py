"""Embedding utilities for semantic search."""

import asyncio
import base64
from typing import List, Optional
import requests
from decouple import config
import logging
logger = logging.getLogger("Noxi")
from src.utils.math_utils import cosine_similarity


class EmbeddingClient:
    """Client for generating text embeddings."""
    
    def __init__(self):
        self.api_key = config("OPENROUTER_API_KEY", default=None)
        self.base_url = "https://openrouter.ai/api/v1"
        self._ready = True  # Завжди готовий - використовуємо word-based
        
        logger.info("[EMBEDDING] Готово (word-based vectors)")
    
    def is_available(self) -> bool:
        return self._ready
    
    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding vector for text using word-based approach."""
        return self._get_word_based_embedding(text)
    
    def _get_word_based_embedding(self, text: str) -> List[float]:
        """Word-based embedding - працює безкоштовно, без API."""
        words = text.lower().split()
        
        # Використовуємо хеш-кожного слова для створення вектора
        import hashlib
        vec = [0.0] * 128
        
        for i, word in enumerate(words):
            hash_val = int(hashlib.md5(word.encode()).hexdigest(), 16)
            # Розподіляємо хеш по вектору
            for j in range(min(8, 128)):
                idx = (hash_val >> (j * 4)) & 0x7F
                vec[idx] += 1.0
        
        # Нормалізуємо
        mag = (sum(v * v for v in vec) ** 0.5)
        if mag > 0:
            vec = [v / mag for v in vec]
        
        return vec
    
    def _get_simple_embedding(self, text: str) -> List[float]:
        """Simple fallback embedding using hash - для тестування."""
        import hashlib
        words = text.lower().split()
        vec = [0.0] * 128
        
        for i, word in enumerate(words[:128]):
            hash_val = int(hashlib.md5(word.encode()).hexdigest(), 16)
            vec[i % 128] += (hash_val % 1000) / 1000.0
        
        mag = (sum(v * v for v in vec) ** 0.5)
        if mag > 0:
            vec = [v / mag for v in vec]
        
        return vec
    
    async def find_similar(
        self, 
        query: str, 
        entries: List[dict], 
        limit: int = 5,
        min_similarity: float = 0.0
    ) -> List[tuple]:
        """
        Find similar entries using embeddings.
        
        Returns:
            List of (entry, similarity) tuples sorted by similarity
        """
        query_embedding = await self.get_embedding(query)
        if not query_embedding:
            return []
        
        results = []
        for entry in entries:
            entry_embedding = entry.get("embedding")
            if not entry_embedding:
                continue
            
            try:
                similarity = cosine_similarity(query_embedding, entry_embedding)
                if similarity >= min_similarity:
                    results.append((entry, similarity))
            except:
                continue
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


embedding_client = EmbeddingClient()


async def get_embedding(text: str) -> Optional[List[float]]:
    """Get embedding for text."""
    return await embedding_client.get_embedding(text)


async def find_similar_entries(
    query: str,
    entries: List[dict],
    limit: int = 5
) -> List[tuple]:
    """Find similar diary entries."""
    return await embedding_client.find_similar(query, entries, limit)


def is_embedding_available() -> bool:
    return embedding_client.is_available()