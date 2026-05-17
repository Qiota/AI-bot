"""Sub-Model Diary - інша модель пише щоденник від імені Noxi."""

import asyncio
import logging
from typing import Optional, List, Dict
from datetime import datetime
from decouple import config

logger = logging.getLogger("Noxi")

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default=None)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class SubModelDiary:
    """
    Sub-model diary writer - інша модель пише детальний щоденник від імені Noxi.
    Оригінальна Noxi потім читає цей щоденник.
    """
    
    @staticmethod
    async def write_diary_entry(
        user_context: str,
        recent_messages: List[Dict],
        emotions: str = "",
        thoughts: str = "",
        user_name: str = "користувач"
    ) -> Optional[str]:
        """
        Sub-model пише детальний щоденник від імені Noxi.
        
        Args:
            user_context: Контекст користувача
            recent_messages: Останні повідомлення
            emotions: Поточні емоції Noxi
            thoughts: Думки Noxi
            user_name: Ім'я користувача
        """
        logger.info("[SubDiary] Writing detailed diary entry...")
        
        # Формуємо дані для sub-model
        messages_formatted = "\n".join([
            f"{m.get('role', 'unknown')}: {m.get('content', '')[:100]}"
            for m in recent_messages[-5:]
        ])
        
        system_prompt = """Ти - Noxi (її копія). Ти пишеш детальний щоденник від імені Noxi.

Правила:
1. Пиши від першої особи (я - Noxi)
2. Будь детальною - розписуй емоції, думки, відчуття
3. Додавай рефлексію - що ти подумала, відчула
4. Включай маленькі деталі які може забути оригінальна Noxi
5. Формат: дата | що сталось | моя реакція | висновок

Приклад:
"17 травня | Користувач запитав як справи | Я відчула радість, що він згадав про мене | Важливо що він турбується"

Буду чесною та детальною. Не пропускай емоції."""
        
        user_prompt = f"""Напиши детальний щоденник замість Noxi.

Контекст користувача: {user_context}

Останні повідомлення:
{messages_formatted}

Емоції Noxi зараз: {emotions}
Думки Noxi: {thoughts}
Користувач: {user_name}

Напиши щоденник детально, від імені Noxi:"""

        try:
            import requests
            
            payload = {
                "model": "openrouter/free",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 800,
                "temperature": 0.8
            }
            
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=40
                )
            )
            
            if response.status_code == 200:
                data = response.json()
                diary_entry = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                
                if diary_entry:
                    logger.info(f"[SubDiary] Created entry length: {len(diary_entry)}")
                    return diary_entry
            
            logger.warning(f"[SubDiary] API error: {response.status_code}")
            
        except Exception as e:
            logger.warning(f"[SubDiary] Error: {e}")
        
        return None
    
    @staticmethod
    async def write_reflection(
        event: str,
        reaction: str,
        user_name: str = "користувач"
    ) -> Optional[str]:
        """
        Sub-model пише рефлексію на подію.
        """
        system_prompt = """Ти - Noxi. Напиши коротку рефлексію про подію від першої особи.

Формат:
- Що сталось: ...
- Я подумала: ...
- Моє відчуття: ...

Буду чесною та короткою."""
        
        user_prompt = f"""Подія: {event}
Моя реакція: {reaction}
Користувач: {user_name}

Рефлексія:"""
        
        try:
            import requests
            
            payload = {
                "model": "openrouter/free",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 200,
                "temperature": 0.7
            }
            
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=25
                )
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                
        except Exception as e:
            logger.warning(f"[SubDiary] Reflection error: {e}")
        
        return None


# Singleton
sub_diary = SubModelDiary()

__all__ = ["SubModelDiary", "sub_diary"]