"""Model Manager - розподіл моделей по компонентах мозку Noxi з підтримкою локальної Ollama."""

import asyncio
import json
import logging
from typing import Optional, List, Dict, Any
import requests
from decouple import config

logger = logging.getLogger("Noxi")

# Настройки внешних API
OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default=None)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MISTRAL_API_KEY = config("MISTRAL_API_KEY", default="J6QyRoQf4JkxvtoV9Cod9VyMGIwGzpXg")
MISTRAL_CHAT_URL = "https://api.mistral.ai/v1/chat/completions"

# Настройки локальной Ollama в Termux
OLLAMA_BASE_URL = config("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_MODEL = config("OLLAMA_MODEL", default="qwen2.5:3b")


class ModelManager:
    def __init__(self):
        self._or_idx = 0
        self._last_response = ""
        self._repeated_count = 0
        self._working_model = None
        self._working_provider = None

    async def diagnose_g4f(self) -> dict:
        """Diagnose which g4f models are working."""
        test_message = [{"role": "user", "content": "Привіт! Скажи 'OK' українською."}]
        
        from g4f import Provider
        
        models_to_test = [
            ("", Provider.AnyProvider), 
            ("", None),
        ]
        
        results = {}
        
        logger.info("[DIAGNOSE] Starting g4f model diagnosis...")
        
        for model_name, provider_cls in models_to_test:
            try:
                from g4f.client import Client
                client = Client(provider=provider_cls)
                
                kwargs = {"messages": test_message, "timeout": 30}
                if model_name:
                    kwargs["model"] = model_name
                
                loop = asyncio.get_event_loop()
                resp = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: client.chat.completions.create(**kwargs)
                    ),
                    timeout=35,
                )
                
                if resp and resp.choices:
                    content = resp.choices[0].message.content
                    if content and len(content.strip()) > 2:
                        provider_name = provider_cls.__name__ if provider_cls else "auto"
                        model_key = f"{model_name or 'auto'}/{provider_name}"
                        results[model_key] = "OK"
                        logger.info(f"[DIAGNOSE] Working: {model_key} -> {content[:50]}")
                        if self._working_model is None:
                            self._working_model = model_name if model_name else ""
                            self._working_provider = provider_cls
                            if provider_cls is None:
                                self._working_provider = Provider.PollinationsAI
                        break
            except Exception as e:
                err_str = str(e).lower()
                if "timeout" not in err_str:
                    provider_name = provider_cls.__name__ if provider_cls else "auto"
                    logger.warning(f"[DIAGNOSE] Failed: {model_name}/{provider_name} -> {e}")
        
        return results

    def _is_valid(self, text: str) -> bool:
        if not text or len(text.strip()) < 3:
            return False
        letters = sum(1 for c in text if c.isalpha())
        if letters < len(text) * 0.3:
            return False
        if self._is_nonsense(text):
            return False
        return True

    def _is_nonsense(self, text: str) -> bool:
        """Detect gibberish/nonsense responses."""
        if not text:
            return True

        text_lower = text.lower()

        nonsense_patterns = [
            "rundfunk", "однформація", "білебирда", "ufffd", "\ufffd",
            "стоп стоп", "одна формація", "інформація про", "ufffc", ""
        ]
        for pattern in nonsense_patterns:
            if pattern.lower() in text_lower:
                return True

        words = text.split()
        if len(words) < 3:
            return True

        cyrillic_count = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        latin_count = sum(1 for c in text if 'a' <= c <= 'z' or 'A' <= c <= 'Z')
        total_letters = cyrillic_count + latin_count

        if total_letters > 10:
            mixed_ratio = min(cyrillic_count, latin_count) / max(cyrillic_count, latin_count)
            if mixed_ratio > 0.5 and cyrillic_count > 0 and latin_count > 0:
                pass

        repeated_words = 0
        seen = {}
        for w in words[:10]:
            w_lower = w.lower()
            if w_lower in seen:
                repeated_words += 1
            seen[w_lower] = True

        if len(words) > 5 and repeated_words > len(words) * 0.6:
            return True

        invalid_chars = sum(1 for c in text if ord(c) > 0xFFFF and not c.isalnum())
        if invalid_chars > len(text) * 0.1:
            return True

        return False

    def _is_repeated(self, text: str) -> bool:
        """Check if response is repeating itself."""
        if not text or not self._last_response:
            self._last_response = text
            self._repeated_count = 0
            return False

        similarity = 0
        words = text.split()
        last_words = self._last_response.split()
        if words and last_words:
            common = sum(1 for w in words[:5] if w in last_words[:5])
            if len(words[:5]) > 0:
                similarity = common / len(words[:5])

        self._last_response = text

        if similarity > 0.8:
            self._repeated_count += 1
            if self._repeated_count > 2:
                return True
        else:
            self._repeated_count = 0

        return False

    async def _call_ollama(self, messages: List[Dict[str, Any]], max_tokens: int) -> Optional[str]:
        """Прямой синхронный запрос к локальной Ollama, обернутый в executor."""
        url = f"{OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "think": False,       # Отключение режима генерации мыслей (размышлений) для экономии ресурсов
                "temperature": 0.7    # Оптимально для удержания роли и предотвращения бреда
            }
        }
        
        def _request():
            return requests.post(url, json=payload, timeout=60)
            
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _request)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("message", {}).get("content", "")
        else:
            logger.warning(f"[OLLAMA] Server returned status code {response.status_code}")
            return None

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        category: str = "balanced",
        max_tokens: int = 1024,
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        all_msgs = list(messages)
        if system_prompt:
            all_msgs = [{"role": "system", "content": system_prompt}] + all_msgs

        if category == "fast":
            max_tokens = min(max_tokens, 150)
        elif category == "balanced":
            max_tokens = min(max_tokens, 300)

        # ПРИОРЕТЕТ 1: Локальный запуск через Ollama (внутри Termux)
        logger.info(f"[MODEL] Trying local Ollama ({OLLAMA_MODEL})...")
        for attempt in range(2):
            try:
                content = await asyncio.wait_for(
                    self._call_ollama(all_msgs, max_tokens),
                    timeout=65
                )
                if content and self._is_valid(content):
                    if self._is_repeated(content):
                        logger.warning("[MODEL] Ollama response repeated, retrying...")
                        continue
                    logger.info(f"[MODEL] Ollama success (attempt {attempt + 1})")
                    return content.strip()
            except Exception as e:
                logger.warning(f"[MODEL] Ollama failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(1)

        # ПРИОРЕТЕТ 2: Резерв через g4f (если Ollama упала или выключена)
        logger.info("[MODEL] Ollama failed. Falling back to g4f...")
        model_to_use = self._working_model or ""
        provider_to_use = self._working_provider
        
        if provider_to_use is None:
            from g4f import Provider
            provider_to_use = Provider.Groq
        
        for attempt in range(3):
            try:
                from g4f.client import Client
                client = Client(provider=provider_to_use)
                
                def _g4f_call():
                    return client.chat.completions.create(
                        model=model_to_use,
                        messages=all_msgs,
                        timeout=45
                    )
                
                loop = asyncio.get_event_loop()
                resp = await asyncio.wait_for(
                    loop.run_in_executor(None, _g4f_call),
                    timeout=50,
                )
                if resp and resp.choices:
                    content = resp.choices[0].message.content
                    if isinstance(content, str) and self._is_valid(content):
                        if self._is_repeated(content):
                            logger.warning("[MODEL] g4f response repeated, retrying...")
                            continue
                        logger.info(f"[MODEL] g4f success (attempt {attempt + 1})")
                        return content.strip()
            except Exception as e:
                logger.warning(f"[MODEL] g4f failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)

        # ПРИОРЕТЕТ 3: Финальный резерв через Mistral API
        if MISTRAL_API_KEY:
            logger.info("[MODEL] Trying Mistral API fallback...")
            headers = {
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "mistral-tiny",
                "messages": all_msgs,
                "max_tokens": max_tokens
            }
            for attempt in range(3):
                try:
                    def _mistral_request():
                        return requests.post(MISTRAL_CHAT_URL, json=payload, headers=headers, timeout=30)
                    
                    loop = asyncio.get_event_loop()
                    resp = await asyncio.wait_for(
                        loop.run_in_executor(None, _mistral_request),
                        timeout=35
                    )
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"]
                        if content and self._is_valid(content):
                            logger.info("[MODEL] Mistral API success")
                            return content.strip()
                except Exception as e:
                    logger.warning(f"[MODEL] Mistral failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(2)

        return None
