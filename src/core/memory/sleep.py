"""Сон — проста консолідація пам'яті без виклику LLM (через обмежені ресурси)."""

import time
import random
from typing import List, Optional
from .diary import Diary


class SleepConsolidation:
    """Проста консолідація пам'яті без LLM. Для систем з ekstремно обмеженими ресурсами."""

    def __init__(self, diary: Diary):
        self.diary = diary
        self.is_sleeping = False
        self.last_sleep_time = 0.0
        self.sleep_interval = 1800  # 30 minutes instead of 24h for testing
        self.max_sleep_time = 30    # Max 30 seconds sleep processing

    def should_sleep(self) -> bool:
        """Перевірити, чи час для простого очищення пам'яті."""
        now = time.time()
        return (now - self.last_sleep_time) >= self.sleep_interval

    async def start_sleep_cycle(self) -> dict:
        """Проста консолідація: видалення старих записів, об'єднання дублікатів."""
        if self.is_sleeping:
            return {"status": "already_sleeping"}

        self.is_sleeping = True
        start_time = time.time()
        
        try:
            # Get entries to process
            entries = self.diary.get_entries_for_sleep(max_entries=20)
            if not entries:
                return {"status": "no_entries", "processed": 0}
            
            processed = 0
            consolidated = 0
            discarded = 0
            
            # Simple deduplication by content similarity
            seen_contents = set()
            entries_to_keep = []
            
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                    
                content = str(entry.get("content", "")).strip().lower()
                if not content:
                    continue
                
                # Simple similarity check: if we've seen similar content, skip
                is_duplicate = False
                for seen in seen_contents:
                    if len(content) > 10 and len(seen) > 10:
                        # Simple overlap check
                        common_chars = sum(1 for c in content if c in seen)
                        similarity = common_chars / max(len(content), len(seen))
                        if similarity > 0.7:  # 70% similarity threshold
                            is_duplicate = True
                            break
                
                if not is_duplicate:
                    seen_contents.add(content)
                    entries_to_keep.append(entry)
                else:
                    discarded += 1
                
                processed += 1
                
                # Check time limit
                if time.time() - start_time > self.max_sleep_time:
                    break
            
            # Update diary with deduplicated entries
            # Keep only non-consolidated entries plus our processed ones
            current_entries = self.diary.get_entries(limit=100)  # Get current state
            unprocessed = [e for e in current_entries if e.get("timestamp", 0) < 
                          (time.time() - 3600)]  # Older than 1 hour
            
            # Build new entry list: old unprocessed + our deduplicated recent entries
            new_entries = unprocessed + entries_to_keep
            
            # Apply limits
            if len(new_entries) > 30:  # Hard limit
                new_entries = new_entries[-30:]
                
            # Save back to diary (this will trigger _save which does its own filtering)
            self.diary.entries = new_entries
            self.diary._save()
            
            consolidated = len(entries_to_keep)
            
            return {
                "status": "completed",
                "processed": processed,
                "consolidated": consolidated,
                "discarded": discarded,
                "duration": time.time() - start_time,
            }
            
        except Exception as e:
            return {"status": "error", "error": str(e)}
        finally:
            self.is_sleeping = False
            self.last_sleep_time = time.time()

    def get_sleep_status(self) -> dict:
        """Отримати статус сну."""
        now = time.time()
        next_sleep = self.last_sleep_time + self.sleep_interval
        time_until_sleep = max(0, next_sleep - now)
        
        return {
            "is_sleeping": self.is_sleeping,
            "last_sleep": time.strftime("%H:%M", time.localtime(self.last_sleep_time)) if self.last_sleep_time else "n/a",
            "next_sleep_in_minutes": round(time_until_sleep / 60, 1),
        }