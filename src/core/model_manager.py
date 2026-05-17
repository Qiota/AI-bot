"""Model Manager - розподіл моделей по компонентах мозку Noxi."""

import asyncio
import requests
from typing import Optional, List, Dict, Any
from decouple import config
import logging

logger = logging.getLogger("Noxi")

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default=None)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# OpenRouter - резервний роутер
OPENROUTER_POOL = {
    "fast": ["openrouter/free"],
    "balanced": ["openrouter/free"],
    "reasoning": ["openrouter/free"],
    "vision": ["openrouter/free"]
}

# Моделі для g4f fallback (якщо OpenRouter недоступний)
G4F_MODELS = {
    "fast": ["default"],
    "balanced": ["default"],
    "reasoning": ["default"],
    "vision": ["default"]
}

G4F_PROVIDERS = ["PollinationsAI", "LambdaChat", "DeepSeek"]

class ModelManager:
    """Менеджер моделей - розподіляє запити по різним моделям."""
    
    def __init__(self):
        self.current_model_index = {category: 0 for category in OPENROUTER_POOL}
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def get_model(self, category: str = "balanced") -> str:
        """Отримати наступну модель з пулу."""
        if category not in OPENROUTER_POOL:
            category = "balanced"
        
        pool = OPENROUTER_POOL[category]
        index = self.current_model_index[category]
        model = pool[index % len(pool)]
        
        return model
    
    def rotate_model(self, category: str):
        """Переключити на наступну модель (для уникнення rate limit)."""
        if category in OPENROUTER_POOL:
            self.current_model_index[category] = (self.current_model_index[category] + 1) % len(OPENROUTER_POOL[category])
    
    def _is_valid_output(self, text: str) -> bool:
        """Перевіряє чи не є вивід маячнею."""
        if not text or len(text) < 3:
            return False
        
        # Проста перевірка - чи є хоча б 30% літер
        letters = sum(1 for c in text if c.isalpha())
        if letters < len(text) * 0.3:
            return False
        
        return True
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        category: str = "balanced",
        max_tokens: int = 1024,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None
    ) -> Optional[str]:
        """g4f as primary - fallback to OpenRouter router."""
        
# g4f Client - новий інтерфейс (auto provider)
        # g4f as primary
        try:
            from g4f.client import Client as G4FClient
            import g4f

            # g4f primary, keep it resilient: if a provider chain fails (e.g. DeepInfra auth),
            # we still want OpenRouter backup to produce an answer.
            all_messages = messages.copy()
            if system_prompt:
                all_messages = [{"role": "system", "content": system_prompt}] + all_messages

            client = G4FClient()
            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                        loop.run_in_executor(
                    None,
                    lambda: client.chat.completions.create(
                        # NOTE: g4f provider chains differ by environment.
                        # "deepseek-v3" breaks on some providers (e.g. PollinationsAI legacy).
                        # "default" lets g4f auto-select a working backend/model.
                        model="default",
                        messages=all_messages  # type: ignore[arg-type]
                    )
                ),
                timeout=35,
            )

            if response:
                content = response.choices[0].message.content
                if isinstance(content, str):
                    content = content.strip()
                if content:
                    logger.info("[MODEL] g4f success")
                    return content

        except asyncio.TimeoutError:
            logger.warning("[MODEL] g4f timeout -> OpenRouter fallback")
        except Exception as e:
            logger.warning(f"[MODEL] g4f failed -> OpenRouter fallback: {str(e)}")

        # OpenRouter as backup
        return await self._openrouter_fallback(messages, max_tokens, system_prompt)

    
    async def _openrouter_fallback(
        self, messages: List[Dict[str, Any]], max_tokens: int, system_prompt: Optional[str]
    ) -> Optional[str]:
        """OpenRouter backup (tries a few times)."""

        if not OPENROUTER_API_KEY:
            logger.warning("[MODEL] OPENROUTER_API_KEY missing -> skip OpenRouter fallback")
            return None

        # OpenRouter as backup
        for attempt in range(3):
            try:
                all_messages = messages.copy()
                if system_prompt:
                    all_messages = [{"role": "system", "content": system_prompt}] + messages

                payload = {
                    "model": "openrouter/free",
                    "messages": all_messages,
                    "max_tokens": max_tokens,
                }

                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._session.post(
                        f"{OPENROUTER_BASE_URL}/chat/completions",
                        json=payload,
                        timeout=25,
                    ),
                )

                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        logger.info(f"[MODEL] OpenRouter success (attempt {attempt+1})")
                        return content

                logger.warning(
                    f"[MODEL] OpenRouter non-200 (attempt {attempt+1}): {response.status_code}"
                )

            except Exception as e:
                logger.warning(f"[MODEL] OpenRouter fallback failed (attempt {attempt+1}): {e}")

            await asyncio.sleep(0.7 * (attempt + 1))

        return None
    
    async def vision_chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 512
    ) -> Optional[str]:
        """Vision чат - швидкий запит."""
        
        try:
            payload = {
                "model": "openrouter/free",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._session.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    json=payload,
                    timeout=25
                )
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
        except Exception as e:
            logger.warning(f"[VISION] Failed: {e}")
        
        return None
    
    async def chat_g4f(
        self,
        messages: List[Dict[str, Any]],
        category: str = "balanced",
        max_tokens: int = 512
    ) -> Optional[str]:
        """g4f fallback - вимкнуто через повільність."""
        # g4f занадто повільний - не використовуємо
        return None


model_manager = ModelManager()