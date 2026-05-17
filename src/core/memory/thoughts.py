"""Думки — система думок для Noxi з використанням Model Manager."""

import time
import asyncio
from typing import Dict, List, Optional
from .diary import Diary
from ..model_manager import model_manager


class ThoughtGenerator:
    """Генерує думки з використанням Model Manager."""

    def __init__(self, diary: Diary):
        self.diary = diary
        self.thought_history: List[Dict] = []
        self.max_history = 5

    async def generate_thoughts(
        self,
        user_message: str,
        context_entries: Optional[List[Dict]] = None,
    ) -> List[str]:
        """Згенерувати думки з використанням Model Manager (думки для думок)."""
        if not user_message or not isinstance(user_message, str):
            return []
        
        # Системний промпт для генерації думок
        system_prompt = """Ти - система генерації думок для AI персонажа Noxi.
Твоя задача - генерувати короткі, релевантні думки про повідомлення користувача.
Думки повинні бути:
- Короткі (1-3 слова)
- Релевантні до контексту
- Від першої особи Noxi
- Різноманітні (емоції, спогади, асоціації)

Приклади:
- "Цікаво..."
- "Нагадує про минуле"
- "Хвилює"
- "Потрібно запам'ятати"
- "сміятися"

Відповідай українською. Дай 2-3 думки."""
        
        # Отримуємо контекст з diary
        recent = self.diary.get_recent_entries(limit=3)
        context_text = "\n".join([
            f"- {e.get('content', '')[:100]}" for e in recent if isinstance(e, dict)
        ])
        
        user_content = f"""Повідомлення: {user_message}

Недавні записи:
{context_text}

Згенеруй думки Noxi про це повідомлення:"""
        
        messages = [{"role": "user", "content": user_content}]
        
        # Використовуємо "reasoning" модель для генерації думок
        try:
            result = await model_manager.chat(
                messages=messages,
                category="reasoning",
                max_tokens=200,
                system_prompt=system_prompt
            )
            
            if result:
                # Розбиваємо результат на окремі думки
                thoughts = [t.strip() for t in result.split('\n') if t.strip()]
                thoughts = thoughts[:3]
            else:
                thoughts = self._simple_thoughts(user_message, recent)
                
        except Exception as e:
            # Fallback на прості думки
            thoughts = self._simple_thoughts(user_message, recent)
        
        # Store in history
        for thought in thoughts:
            self.thought_history.append({
                "t": thought,
                "ts": int(time.time()),
            })
        
        if len(self.thought_history) > self.max_history:
            self.thought_history = self.thought_history[-self.max_history:]
            
        return thoughts
    
    def _simple_thoughts(self, message: str, recent_entries: List[Dict]) -> List[str]:
        """Fallback - прості асоціації без LLM."""
        thoughts = []
        hour = int(time.strftime("%H", time.localtime()))
        
        if 6 <= hour < 12:
            thoughts.append("Доброго ранку")
        elif 12 <= hour < 18:
            thoughts.append("Доброго дня")
        elif 18 <= hour < 22:
            thoughts.append("Доброго вечора")
        else:
            thoughts.append("Доброї ночі")
        
        if len(message) > 50:
            thoughts.append("Довге повідомлення")
        elif len(message) < 10:
            thoughts.append("Коротке повідомлення")
        
        return thoughts[:2]

    def find_related_entries(self, user_message: str, limit: int = 2) -> List[Dict]:
        """Знайти пов'язані записи за простим ключовим словом."""
        if not user_message or not isinstance(user_message, str):
            return self.diary.get_recent_entries(limit=1)
        
        # Extract simple keywords (first few words)
        words = user_message.lower().split()[:3]
        # Filter out very common words
        stop_words = {"я", "ти", "ви", "ми", "що", "як", "не", "і", "в", "на", "з", "за", "до", "від"}
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        if not keywords:
            return self.diary.get_recent_entries(limit=1)
        
        # Simple search
        results = []
        for entry in self.diary.get_recent_entries(limit=5):
            if not isinstance(entry, dict):
                continue
            content = str(entry.get("content", "")).lower()
            if any(keyword in content for keyword in keywords):
                results.append(entry)
                if len(results) >= limit:
                    break
        
        return results if results else self.diary.get_recent_entries(limit=1)

    def get_recent_thoughts(self, limit: int = 2) -> List[Dict]:
        """Отримати останні думки."""
        return self.thought_history[-limit:]

    def format_thoughts_for_context(self, thoughts: List[str]) -> str:
        """Форматувати думки для контексту."""
        if not thoughts:
            return ""
        return "Думки: " + ", ".join(thoughts)