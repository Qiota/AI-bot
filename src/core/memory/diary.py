"""Щоденник — довгострокове зберігання подій та взаємодій. Екстремально оптимізовано для 0.1 vCPU, 512MB RAM."""

import json
import os
import time
import uuid
from typing import Dict, List, Optional
from pathlib import Path
import os

from ...utils.koyeb_files import create_and_write_file

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

DIARY_DIR = DATA_DIR / "diary"
MAX_ENTRIES = 20           # Very aggressive limit for 512MB RAM
MAX_ENTRY_LENGTH = 100     # Aggressive truncation
ENTRY_TTL_DAYS = 3         # Short TTL to limit growth

class Diary:
    """Зберігає, завантажує та керує записами щоденника. Екстремально оптимізовано для низьких ресурсів."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.diary_path = DIARY_DIR / f"{user_id}.json"
        self.entries: List[Dict] = []
        self._load()

    def _ensure_dir(self):
        try:
            DIARY_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # Ignore if directory exists or permission issues

    def _load(self):
        self._ensure_dir()
        if self.diary_path.exists():
            try:
                with open(self.diary_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        self.entries = []
                        return
                    raw = json.loads(content)
                # Filter by TTL and limit entries immediately on load
                cutoff = time.time() - (ENTRY_TTL_DAYS * 86400)
                filtered = [e for e in raw if isinstance(e, dict) and e.get("timestamp", 0) > cutoff]
                # Keep only most recent entries to minimize memory footprint
                self.entries = filtered[-MAX_ENTRIES:] if len(filtered) > MAX_ENTRIES else filtered
            except (json.JSONDecodeError, IOError, ValueError, TypeError):
                self.entries = []
        else:
            self.entries = []

    def _save(self):
        self._ensure_dir()
        # Apply aggressive filtering before saving
        if self.entries:
            cutoff = time.time() - (ENTRY_TTL_DAYS * 86400)
            self.entries = [e for e in self.entries if isinstance(e, dict) and e.get("timestamp", 0) > cutoff]
            # Enforce hard limit
            if len(self.entries) > MAX_ENTRIES:
                self.entries = self.entries[-MAX_ENTRIES:]
            # Truncate overly long entries
            for entry in self.entries:
                if isinstance(entry, dict) and "content" in entry:
                    content = str(entry["content"])
                    if len(content) > MAX_ENTRY_LENGTH:
                        entry["content"] = content[:MAX_ENTRY_LENGTH] + "..."
        try:
            create_and_write_file(
                base_dir=self.diary_path.parent,
                rel_path=self.diary_path.name,
                content=self.entries,
                content_type="json",
            )
        except Exception:
            pass  # Silently fail if disk is full or permission denied


    def add_entry(
        self,
        content: str,
        entry_type: str = "event",
        emotion: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Додати новий запис у щоденник. Мінімізує використання пам'яті."""
        # Aggressive truncation at input
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        if len(content) > MAX_ENTRY_LENGTH:
            content = content[:MAX_ENTRY_LENGTH] + "..."
            
        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "content": content,
            "type": entry_type if entry_type in ["event", "emotion", "thought"] else "event",
            "emotion": emotion if emotion in ["joy", "sadness", "anger", "fear", "surprise", "love", "curiosity", "calm", None] else None,
            "timestamp": time.time(),
            "created_at": time.time(),
            "metadata": {} if metadata is None else {k: str(v)[:50] for k, v in metadata.items() if isinstance(k, str)},
        }
        self.entries.append(entry)
        self._save()
        return entry_id

    def get_entries(
        self,
        limit: int = 10,
        entry_type: Optional[str] = None,
        since: Optional[float] = None,
        emotion: Optional[str] = None,
    ) -> List[Dict]:
        """Отримати записи з мінімальними обчисленнями."""
        # Return early if no entries
        if not self.entries:
            return []
            
        # Filter by type first (fastest check)
        if entry_type:
            filtered = [e for e in self.entries if e.get("type") == entry_type]
        else:
            filtered = self.entries
            
        # Filter by emotion
        if emotion:
            filtered = [e for e in filtered if e.get("emotion") == emotion]
            
        # Filter by time
        if since:
            filtered = [e for e in filtered if e.get("timestamp", 0) >= since]
            
        # Sort by timestamp descending (newest first) and limit
        try:
            filtered.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        except (TypeError, KeyError):
            pass  # Keep original order if sorting fails
        return filtered[:limit]

    def get_recent_entries(self, limit: int = 5) -> List[Dict]:  # Reduced default limit
        """Отримати найновіші записи."""
        return self.get_entries(limit=limit)

    def get_entries_for_sleep(self, max_entries: int = 10) -> List[Dict]:  # Much smaller for sleep
        """Отримати записи для обробки під час сну. Мінімальний розмір для економії CPU."""
        if not self.entries:
            return []
            
        # Simple approach: just return most recent entries
        # Skip complex randomization to save CPU on 0.1 vCPU
        try:
            sorted_entries = sorted(self.entries, key=lambda e: e.get("timestamp", 0), reverse=True)
            return sorted_entries[:max_entries]
        except (TypeError, KeyError):
            return self.entries[:max_entries]

    def update_entry(self, entry_id: str, updates: Dict) -> bool:
        """Оновити запис за ID. Оптимізовано для швидкості."""
        if not self.entries or not entry_id:
            return False
        for entry in self.entries:
            if isinstance(entry, dict) and entry.get("id") == entry_id:
                entry.update({k: v for k, v in updates.items() if isinstance(k, str)})
                self._save()
                return True
        return False

    def delete_entry(self, entry_id: str) -> bool:
        """Видалити запис за ID."""
        if not self.entries or not entry_id:
            return False
        original_len = len(self.entries)
        self.entries = [e for e in self.entries if e.get("id") != entry_id]
        if len(self.entries) < original_len:
            self._save()
            return True
        return False

    def mark_consolidated(self, entry_id: str):
        """Позначити запис як консолідований."""
        self.update_entry(entry_id, {"consolidated": True})

    def get_entry_count(self) -> int:
        """Кількість записів."""
        return len(self.entries)

    def clear_old_entries(self, max_age_days: int = 1):  # Very aggressive cleanup
        """Видалити старі записи. Екстремально агресивне очищення для економії місця."""
        if not self.entries:
            return
        cutoff = time.time() - (max_age_days * 86400)
        original_len = len(self.entries)
        self.entries = [e for e in self.entries if e.get("timestamp", 0) > cutoff]
        if len(self.entries) < original_len:
            self._save()

    def format_for_context(self, limit: int = 3) -> str:  # Minimal context for LLM
        """Форматувати записи для передачі в контекст LLM. Мінімальний розмір."""
        if not self.entries:
            return ""
            
        entries = self.get_recent_entries(limit)
        if not entries:
            return ""
            
        lines = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                ts = time.strftime("%m-%d %H:%M", time.localtime(entry.get("timestamp", 0)))
                emotion_str = f" [{entry['emotion']}]" if entry.get("emotion") else ""
                content = str(entry.get("content", ""))[:50]  # Further truncate for context
                if content:
                    lines.append(f"[{ts}]{emotion_str} {content}")
            except (ValueError, TypeError, KeyError, OSError):
                continue  # Skip malformed entries
                
        return "\n".join(lines)

    def search_entries(self, query: str, limit: int = 10) -> List[Dict]:
        """Search entries by query string (simple substring matching)."""
        if not query or not self.entries:
            return []
        
        query_lower = query.lower()
        results = []
        
        for entry in self.entries:
            if not isinstance(entry, dict):
                continue
            content = str(entry.get("content", "")).lower()
            entry_type = str(entry.get("type", "")).lower()
            emotion = str(entry.get("emotion", "")).lower()
            
            if (query_lower in content or 
                query_lower in entry_type or 
                query_lower in emotion):
                results.append(entry)
        
        results.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return results[:limit]

    def set_embedding(self, entry_id: str, embedding: List[float]) -> bool:
        """Додає embedding до запису."""
        for entry in self.entries:
            if entry.get("id") == entry_id:
                if "metadata" not in entry:
                    entry["metadata"] = {}
                entry["metadata"]["embedding"] = embedding[:128]
                self._save()
                return True
        return False

    def get_entries_with_embeddings(self) -> List[Dict]:
        """Отримує записи з embedding."""
        return [e for e in self.entries if e.get("metadata", {}).get("embedding")]

    async def query_by_embedding(
        self,
        query: str,
        limit: int = 10,
        confidence_factor: float = 0.0
    ) -> List[tuple]:
        """Semantic search using embeddings."""
        from .embedding import find_similar_entries
        
        entries_with_emb = self.get_entries_with_embeddings()
        if not entries_with_emb:
            return []
        
        return await find_similar_entries(query, entries_with_emb, limit)

    async def queryAI(
        self,
        query: str,
        options: Optional[Dict] = None
    ) -> str:
        """
        Query diary using AI (similar to C++ diary.queryAI).
        
        Args:
            query: Question to ask the diary
            options: Optional settings (e.g., confidenceFactor)
            
        Returns:
            AI-generated response based on diary entries
        """
        import asyncio
        from typing import Any
        
        opts = options or {}
        confidence_factor = opts.get("confidenceFactor", 0.5)
        
        if not self.entries:
            return "No diary entries available."
        
        recent_entries = self.get_recent_entries(limit=20)
        if not recent_entries:
            return "No recent diary entries found."
        
        entries_text = "\n".join([
            f"[{e.get('timestamp', 0)}] {e.get('content', '')[:150]}"
            for e in recent_entries
        ])
        
        system_prompt = f"""You are querying a personal diary. Based on the following diary entries, 
answer the user's question. Be specific and reference relevant entries.

Diary entries:
{entries_text}

Confidence factor: {confidence_factor}
If confidence is low, indicate uncertainty."""

        user_prompt = f"Question: {query}\n\nProvide a helpful answer based on the diary entries above."

        try:
            import g4f
            from g4f.client import Client as G4FClient
            
            client = G4FClient()
            response = client.chat.completions.create(
                model="deepseek-v3",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            fallback_results = self.search_entries(query, limit=5)
            if fallback_results:
                entries_str = "\n".join([f"- {e.get('content', '')[:100]}" for e in fallback_results])
                return f"Знайдено {len(fallback_results)} записів:\n{entries_str}"
            return f"Не знайшов інформації: {str(e)[:100]}"