"""Unified memory system backed by Firebase Realtime Database."""

import asyncio
import datetime
import json
import time
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger("Noxi")

MAX_DIARY_ENTRIES = 50
MAX_MOOD_HISTORY = 10
MAX_WORKING_ITEMS = 10
MAX_THOUGHTS = 5
WORKING_MEMORY_TTL_DAYS = 3

"""
Cognitive Psychology - Working Memory Definition:
Working memory is a limited-capacity system that temporarily stores and 
manipulates information for ongoing cognitive tasks, such as remembering 
a phone number before dialing or tracking conversation context.

Solution for Noxi:
Working memory is stored in Firebase as the "middle layer" of human memory 
and emulates it — things that matter for 1-3 days but aren't important 
enough to keep permanently in the diary.

- Loaded at session start and injected as <things_to_remember>
- Updated during diary consolidation by LLM:
  - Preserve all incomplete tasks, promises, reminders
  - Review relevant chats
  - Delete completed tasks and items older than 3 days
  - Add new important details from current conversation
  - Structure output with "last updated" timestamps
- Persists between program runs
- Always included in context so Noxi remembers
"""

EMOTIONS = ["joy", "sadness", "anger", "fear", "surprise", "love", "curiosity", "calm"]

MOOD_TEXTS = {
    "joy": "радий", "sadness": "сумую", "anger": "злий", "fear": "боюся",
    "surprise": "дивуюся", "love": "кохаю", "curiosity": "цікавлюся", "calm": "спокійний",
}

EMOTION_KEYWORDS = {
    "joy": ["радість", "щастя", "добро", "хорошо", "приємно"],
    "sadness": ["сум", "смуток", "жаль", "погано", "сумно"],
    "anger": ["злість", "дива", "сердито", "знизкований"],
    "fear": ["страх", "тривога", "боюсь", "налякано"],
    "surprise": ["дивлюся", "недивно", "дивново", "неочікувано"],
    "love": ["кохання", "кохаю", "доброзиччя", "тепло"],
    "curiosity": ["цікаво", "цікавить", "розбираюсь", "хочу знати"],
    "calm": ["спокій", "заспокій", "розслаблено", "нормально"],
}


class FirebaseMemory:
    _instance: Optional["FirebaseMemory"] = None
    _initialized = False

    def __init__(self) -> None:
        self._db = None
        self._fb_ready = False
        self._cache: Dict[str, Dict] = {}
        self._init_sync()

    def _init_sync(self) -> None:
        if FirebaseMemory._initialized:
            return
        try:
            from decouple import config
            import firebase_admin
            from firebase_admin import credentials, db

            db_url = config("FIREBASE_DATABASE_URL", default="https://noxi-90233-default-rtdb.europe-west1.firebasedatabase.app")
            cred_json = config("FIREBASE_CREDENTIALS", default="")

            if cred_json and cred_json.strip():
                cred_dict = json.loads(cred_json)
                cred = credentials.Certificate(cred_dict)
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred, {"databaseURL": db_url})
            else:
                logger.warning("[MEMORY] FIREBASE_CREDENTIALS not set, using in-memory fallback")
                FirebaseMemory._initialized = True
                return

            self._db = db.reference()
            self._fb_ready = True
            logger.info("[MEMORY] Firebase connected")
        except Exception as e:
            logger.warning(f"[MEMORY] Firebase init failed: {e}. Using in-memory fallback.")
            self._db = None
            self._fb_ready = False
        FirebaseMemory._initialized = True

    def _path(self, *parts: str) -> str:
        return "/".join(["memory"] + list(parts))

    def _sync(self, user_id: str) -> None:
        if not self._fb_ready or self._db is None:
            return
        try:
            self._db.child(self._path(user_id)).set(self._cache.get(user_id, {}))
        except Exception as e:
            logger.warning(f"[MEMORY] sync failed: {e}")

    def _sync_load(self, user_id: str) -> Dict:
        if not self._fb_ready or self._db is None:
            return self._cache.get(user_id, {})

        path = self._path(user_id)
        try:
            data = self._db.child(path).get()
            if data is None:
                data = {}
            self._cache[user_id] = data
            return data
        except Exception as e:
            logger.warning(f"[MEMORY] load failed: {e}")
            return self._cache.get(user_id, {})

    def detect_emotion(self, text: str) -> Optional[str]:
        if not text:
            return None
        t = text.lower()
        for emotion, keywords in EMOTION_KEYWORDS.items():
            for kw in keywords:
                if kw in t:
                    return emotion
        return None

    def add_diary_entry(
        self,
        user_id: str,
        content: str,
        entry_type: str = "event",
        emotion: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        data = self._sync_load(user_id)
        entries = data.get("diary", [])
        entry_id = str(int(time.time() * 1000))
        entry = {
            "id": entry_id,
            "content": content[:200],
            "type": entry_type,
            "emotion": emotion if emotion in EMOTIONS else None,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        entries.append(entry)
        if len(entries) > MAX_DIARY_ENTRIES:
            entries = entries[-MAX_DIARY_ENTRIES:]
        data["diary"] = entries
        self._cache[user_id] = data
        self._sync(user_id)
        return entry_id

    def get_diary_entries(
        self,
        user_id: str,
        limit: int = 10,
        entry_type: Optional[str] = None,
        since: Optional[float] = None,
    ) -> List[Dict]:
        data = self._sync_load(user_id)
        entries = data.get("diary", [])
        if entry_type:
            entries = [e for e in entries if e.get("type") == entry_type]
        if since:
            entries = [e for e in entries if e.get("timestamp", 0) >= since]
        entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return entries[:limit]

    def get_recent_entries(self, user_id: str, limit: int = 5) -> List[Dict]:
        return self.get_diary_entries(user_id, limit=limit)

    def search_entries(self, user_id: str, query: str, limit: int = 10) -> List[Dict]:
        data = self._sync_load(user_id)
        q = query.lower()
        results = [
            e for e in data.get("diary", [])
            if q in str(e.get("content", "")).lower() or q in str(e.get("type", "")).lower()
        ]
        results.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return results[:limit]

    async def query_by_embedding(self, user_id: str, query: str, limit: int = 10) -> List[tuple]:
        """Search entries using embedding similarity."""
        try:
            from src.core.memory.embedding import get_embedding, is_embedding_available
            if not is_embedding_available():
                return []

            query_emb = await get_embedding(query)
            if not query_emb:
                return []

            data = self._sync_load(user_id)
            entries = data.get("diary", [])
            results = []

            for entry in entries:
                emb = entry.get("metadata", {}).get("embedding", [])
                if emb and len(emb) == len(query_emb):
                    sim = self._cosine_similarity(query_emb, emb[:len(query_emb)])
                    if sim > 0.5:
                        results.append((entry, sim))

            results.sort(key=lambda x: x[1], reverse=True)
            return results[:limit]
        except Exception:
            return []

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0
        return dot / (norm_a * norm_b)

    async def ask_diary(self, user_id: str, query: str, context: str = "") -> str:
        """
        Ask diary tool - consult Noxi's knowledge database.
        
        Args:
            user_id: User ID to query diary for
            query: Freeform question to diary
            context: Optional additional context
            
        Returns:
            Formatted response from diary
        """
        if len(query) < 10:
            return """error: too short query! please provide more context:
- sender's name
- previous messages
- search cues
- source event
- everything else to populate query"""

        enhanced_query = query
        if context:
            enhanced_query = f"""Context: {context}

Query: {query}

Answer based on diary entries. Include dates and details."""

        try:
            similar = await self.query_by_embedding(user_id, enhanced_query, limit=10)
            if similar:
                entries_text = "\n".join([
                    f"- [{self._format_timestamp(e.get('timestamp', 0))}] {e.get('content', '')[:150]}"
                    for e, sim in similar
                ])
                return f"Found {len(similar)} relevant entries:\n\n{entries_text}"
        except Exception as e:
            logger.warning(f"[ASK_DIARY] Embedding search failed: {e}")

        keyword_results = self.search_entries(user_id, query, limit=10)
        if keyword_results:
            results_text = "\n".join([
                f"- [{self._format_timestamp(e.get('timestamp', 0))}] {e.get('content', '')[:150]}"
                for e in keyword_results
            ])
            return f"Found {len(keyword_results)} entries:\n\n{results_text}"

        return "no relevant entries found in diary"

    def _format_timestamp(self, ts: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

    def update_mood(self, user_id: str, mood: str) -> None:
        if mood not in EMOTIONS:
            return
        data = self._sync_load(user_id)
        data["current_mood"] = mood
        mood_history = data.get("mood_history", [])
        mood_history.append({"m": mood, "ts": time.time()})
        if len(mood_history) > MAX_MOOD_HISTORY:
            mood_history = mood_history[-MAX_MOOD_HISTORY:]
        data["mood_history"] = mood_history
        self._cache[user_id] = data
        self._sync(user_id)

    def get_current_mood(self, user_id: str) -> str:
        data = self._sync_load(user_id)
        return data.get("current_mood", "calm")

    def get_mood_history(self, user_id: str, limit: int = 5) -> List[Dict]:
        data = self._sync_load(user_id)
        mood_history = data.get("mood_history", [])
        return mood_history[-limit:]

    def get_mood_context(self, user_id: str) -> str:
        current = self.get_current_mood(user_id)
        mood_text = MOOD_TEXTS.get(current, "спокійний")
        return f"Поточний настрій: {mood_text}"

    def get_mood_trend(self, user_id: str) -> str:
        history = self.get_mood_history(user_id, limit=5)
        if not history:
            return "немає даних"
        if len(history) < 2:
            return "стабільний"
        moods = [h.get("m", "calm") for h in history]
        if moods[0] == moods[-1]:
            return "стабільний"
        return "змінюється"

    def add_working_note(self, user_id: str, note: str, priority: str = "normal") -> None:
        data = self._sync_load(user_id)
        working_memory = data.get("working_memory", [])
        note_entry = {
            "n": note[:100],
            "p": priority,
            "ts": time.time(),
        }
        working_memory.append(note_entry)
        if len(working_memory) > MAX_WORKING_ITEMS:
            working_memory = working_memory[-MAX_WORKING_ITEMS:]
        data["working_memory"] = working_memory
        self._cache[user_id] = data
        self._sync(user_id)

    def get_working_memory(self, user_id: str) -> List[str]:
        data = self._sync_load(user_id)
        working_memory = data.get("working_memory", [])
        now = time.time()
        ttl_seconds = WORKING_MEMORY_TTL_DAYS * 86400

        def _is_valid(entry: Dict) -> bool:
            ts = entry.get("ts", 0)
            age = now - ts
            if age > ttl_seconds:
                priority = entry.get("p", "normal")
                if priority in ("high", "critical"):
                    return True
                return False
            return True

        valid = [e.get("n", "") for e in working_memory if _is_valid(e) and e.get("n")]
        return valid[-MAX_WORKING_ITEMS:]

    def format_working_context(self, user_id: str) -> str:
        working = self.get_working_memory(user_id)
        if not working:
            return ""
        return "Що пам'ятаю: " + "; ".join(working)

    def get_working_memory_summary(self, user_id: str) -> str:
        working = self.get_working_memory(user_id)
        if not working:
            return "робоча пам'ять порожня"
        return "Робоча пам'ять: " + "; ".join(working)

    def update_working_memory_from_llm(self, user_id: str, content: str) -> None:
        self.add_working_note(user_id, content, priority="normal")

    def record_emotion(self, user_id: str, trigger: str, emotion: str, reaction: str = "", intensity: float = 0.5) -> None:
        detected = self.detect_emotion(emotion)
        if detected:
            self.update_mood(user_id, detected)
        self.add_diary_entry(
            user_id=user_id,
            content=f"Емоція: {emotion} | Реакція: {reaction[:50]} | Тригер: {trigger[:30]}",
            entry_type="emotion",
            emotion=emotion if emotion in EMOTIONS else None,
        )

    def set_embedding(self, user_id: str, entry_id: str, embedding: List[float]) -> None:
        data = self._sync_load(user_id)
        entries = data.get("diary", [])
        for entry in entries:
            if entry.get("id") == entry_id:
                if "metadata" not in entry:
                    entry["metadata"] = {}
                entry["metadata"]["embedding"] = embedding
                break
        data["diary"] = entries
        self._cache[user_id] = data
        self._sync(user_id)

    def add_thought(self, user_id: str, thought: str) -> None:
        data = self._sync_load(user_id)
        thoughts = data.get("thoughts", [])
        thoughts.append({"t": thought[:100], "ts": time.time()})
        if len(thoughts) > MAX_THOUGHTS:
            thoughts = thoughts[-MAX_THOUGHTS:]
        data["thoughts"] = thoughts
        self._cache[user_id] = data
        self._sync(user_id)

    def get_thoughts(self, user_id: str) -> List[str]:
        data = self._sync_load(user_id)
        thoughts = data.get("thoughts", [])
        return [t.get("t", "") for t in thoughts if t.get("t")]

    def format_thoughts_for_context(self, user_id: str, thoughts: List[str]) -> str:
        if not thoughts:
            return ""
        return "Думки: " + ", ".join(thoughts[:3])

    def clear_working_memory(self, user_id: str) -> None:
        data = self._sync_load(user_id)
        data["working_memory"] = []
        self._cache[user_id] = data
        self._sync(user_id)

    def reset_working_memory(self, user_id: str) -> None:
        """Reset working memory (clear and reinitialize from diary)."""
        self.clear_working_memory(user_id)

    def format_context(self, user_id: str) -> str:
        """Format all memory for AI context with cognitive psychology structure."""
        parts = []
        data = self._sync_load(user_id)
        mood = data.get("current_mood", "calm")
        parts.append(f"Настрій: {MOOD_TEXTS.get(mood, 'спокійний')}")
        wm = self.format_working_context(user_id)
        if wm:
            parts.append(wm)
        entries = data.get("diary", [])
        if entries:
            recent = entries[-3:]
            lines = []
            for e in recent:
                ts = time.strftime("%m-%d %H:%M", time.localtime(e.get("timestamp", 0)))
                emotion_str = f" [{e.get('emotion', '')}]" if e.get("emotion") else ""
                lines.append(f"[{ts}]{emotion_str} {str(e.get('content', ''))[:60]}")
            if lines:
                parts.append("Останні записи: " + ", ".join(lines))
        return "\n".join(parts)

    def get_entry_count(self, user_id: str) -> int:
        data = self._sync_load(user_id)
        return len(data.get("diary", []))


_memory: Optional[FirebaseMemory] = None


def get_memory() -> FirebaseMemory:
    global _memory
    if _memory is None:
        _memory = FirebaseMemory()
    return _memory


class MemoryConsolidationScheduler:
    """Consolidates memory at midnight for better analysis and memory."""

    def __init__(self, memory: FirebaseMemory, model_manager=None):
        self.memory = memory
        self.model_manager = model_manager
        self._task = None
        self._running = False

    async def _get_all_users(self) -> List[str]:
        """Get all users that have memory data."""
        if not self.memory._fb_ready or self.memory._db is None:
            return []
        try:
            data = self.memory._db.child("memory").get()
            return list(data.keys()) if data else []
        except Exception:
            return []

    async def _consolidate_user_day(self, user_id: str, date: datetime.datetime) -> Optional[str]:
        """Consolidate one user's day into summary using LLM."""
        start_of_day = datetime.datetime(date.year, date.month, date.day).timestamp()
        end_of_day = start_of_day + 86400

        entries = self.memory.get_diary_entries(user_id, limit=100)
        day_entries = [e for e in entries if start_of_day <= e.get("timestamp", 0) < end_of_day]

        if not day_entries:
            return None

        if not self.model_manager:
            return self._simple_consolidate(day_entries)

        content_parts = []
        for e in day_entries:
            ts = datetime.datetime.fromtimestamp(e.get("timestamp", 0)).strftime("%H:%M")
            emotion = e.get("emotion", "")
            emotion_str = f" [{emotion}]" if emotion else ""
            content_parts.append(f"[{ts}]{emotion_str} {e.get('content', '')}")

        daily_text = "\n".join(content_parts)

        prompt = f"""Ти - Нохі, штучний інтелект. Підсумуй свій день коротко і емоційно.

Список подій за сьогодні:
{daily_text}

Напиши 3-5 речень українською:
- Що важливого сталося
- Які емоції переважали
- Що запам'яталось найбільше
- Що плануєш на завтра

Будь короткою і теплою, як Нохі. Півтора-два рядки."""
        try:
            system_prompt = "Ти - Нохі, дружня AI-čka для Discord. Відповідай коротко і по суті."
            result = await self.model_manager.chat(
                messages=[{"role": "user", "content": prompt}],
                category="balanced",
                max_tokens=512,
                system_prompt=system_prompt
            )
            if result:
                return result.strip()
        except Exception as e:
            logger.warning(f"[CONSOLIDATE] LLM failed: {e}")

        return self._simple_consolidate(day_entries)

    def _simple_consolidate(self, entries: List[Dict]) -> str:
        """Simple consolidation without LLM."""
        emotions_count = {}
        types_count = {}

        for e in entries:
            emotion = e.get("emotion")
            if emotion:
                emotions_count[emotion] = emotions_count.get(emotion, 0) + 1
            entry_type = e.get("type", "event")
            types_count[entry_type] = types_count.get(entry_type, 0) + 1

        top_emotion = max(emotions_count, key=emotions_count.get) if emotions_count else "calm"
        emotion_texts = {
            "joy": "переважно радісний",
            "sadness": "був сум",
            "anger": "були моменти напруги",
            "fear": "було тривожно",
            "surprise": "багато дивувався",
            "love": "переважно любов і тепло",
            "curiosity": "цікавився різним",
            "calm": "спокійний день"
        }

        emotion_str = emotion_texts.get(top_emotion, "спокійний")
        count = len(entries)

        return f"Підсумок дня ({count} записів): {emotion_str}."

    async def consolidate_yesterday(self) -> Dict[str, str]:
        """Consolidate all users' yesterday memories."""
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        results = {}

        users = await self._get_all_users()
        logger.info(f"[CONSOLIDATE] Starting for {len(users)} users (date: {yesterday.date()})")

        for user_id in users:
            try:
                summary = await self._consolidate_user_day(user_id, yesterday)
                if summary:
                    self.memory.add_diary_entry(
                        user_id=user_id,
                        content=f"[ПІДСУМОК ДНЯ {yesterday.strftime('%d.%m')}] {summary}",
                        entry_type="daily_summary",
                        emotion=None,
                        metadata={"date": yesterday.strftime("%Y-%m-%d"), "consolidated": True}
                    )
                    results[user_id] = summary
                    logger.info(f"[CONSOLIDATE] Done for {user_id}")
                else:
                    logger.info(f"[CONSOLIDATE] No entries for {user_id}")
            except Exception as e:
                logger.warning(f"[CONSOLIDATE] Error for {user_id}: {e}")

        return results

    async def _sleep_until_midnight(self):
        """Calculate seconds until next midnight."""
        now = datetime.datetime.now()
        midnight = datetime.datetime(now.year, now.month, now.day) + datetime.timedelta(days=1)
        seconds = (midnight - now).total_seconds()
        logger.info(f"[CONSOLIDATE] Sleeping {seconds:.0f}s until midnight...")
        return seconds

    async def start(self):
        """Start the consolidation scheduler."""
        if self._running:
            return
        self._running = True

        async def run_loop():
            while self._running:
                try:
                    seconds = await self._sleep_until_midnight()
                    if seconds > 0:
                        await asyncio.sleep(seconds)

                    if self._running:
                        logger.info("[CONSOLIDATE] Running midnight consolidation...")
                        results = await self.consolidate_yesterday()
                        logger.info(f"[CONSOLIDATE] Completed: {len(results)} users processed")
                        await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"[CONSOLIDATE] Loop error: {e}")
                    await asyncio.sleep(60)

        self._task = asyncio.create_task(run_loop())
        logger.info("[CONSOLIDATE] Scheduler started")

    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[CONSOLIDATE] Scheduler stopped")

    async def run_now(self) -> Dict[str, str]:
        """Manually trigger consolidation for yesterday."""
        return await self.consolidate_yesterday()