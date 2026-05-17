"""Робоча пам'ять — абсолютно мінімальна версія для 0.1 vCPU, 512MB RAM."""

import json
import time
from typing import Dict, List, Optional
from pathlib import Path

DATA_DIR = Path("data")
MAX_ITEMS = 3              # Absolute minimum to save RAM
ITEM_TTL_MINUTES = 30      # Short TTL: 30 minutes
MAX_CONTENT_LENGTH = 30    # Very aggressive truncation

class WorkingMemory:
    """Робоча пам'ять: абсолютно мінімальна версія."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.file_path = DATA_DIR / f"wm_{user_id}.json"
        self.items: List[Dict] = []
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # Filter by TTL and limit
                cutoff = time.time() - (ITEM_TTL_MINUTES * 60)
                filtered = [i for i in raw if isinstance(i, dict) and i.get("ts", 0) > cutoff]
                self.items = filtered[-MAX_ITEMS:] if len(filtered) > MAX_ITEMS else filtered
            except Exception:
                self.items = []
        else:
            self.items = []

    def _save(self):
        try:
            # Filter and truncate before saving
            if self.items:
                cutoff = time.time() - (ITEM_TTL_MINUTES * 60)
                self.items = [i for i in self.items if isinstance(i, dict) and i.get("ts", 0) > cutoff]
                if len(self.items) > MAX_ITEMS:
                    self.items = self.items[-MAX_ITEMS:]
                # Truncate content
                for item in self.items:
                    if isinstance(item, dict) and "c" in item:
                        content = str(item["c"])
                        if len(content) > MAX_CONTENT_LENGTH:
                            item["c"] = content[:MAX_CONTENT_LENGTH] + "..."
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass  # Ignore save errors

    def add_note(self, content: str) -> Optional[str]:
        """Додати коротку нотатку. Повертає None при помилці."""
        if not isinstance(content, str):
            return None
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH] + "..."
        if not content.strip():
            return None
        
        item_id = str(int(time.time() * 1000))[-6:]  # Short ID
        item = {
            "id": item_id,
            "c": content.strip(),
            "t": "note",
            "ts": int(time.time()),
        }
        self.items.append(item)
        self._save()
        return item_id

    def get_recent(self, limit: int = 2) -> List[Dict]:  # Even fewer items
        """Отримати останні нотатки."""
        if not self.items:
            return []
        # Simple reverse for recent (newest last due to append)
        return self.items[-limit:]

    def clear_all(self):
        """Очистити всю робочу пам'ять."""
        self.items = []
        self._save()

    def format_for_context(self) -> str:
        """Форматувати для контексту LLM."""
        if not self.items:
            return ""
        
        lines = ["Нагадування:"]
        for item in self.get_recent(limit=2):
            if isinstance(item, dict):
                content = item.get("c", "")
                if content and len(content) > 1:
                    lines.append(f"• {content}")
        return "\n".join(lines) if len(lines) > 1 else ""