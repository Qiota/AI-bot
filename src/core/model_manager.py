"""Model Manager - розподіл моделей по компонентах мозку Noxi."""

import asyncio
import requests
from typing import Optional, List, Dict, Any
from decouple import config
import logging

logger = logging.getLogger("Noxi")

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default=None)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MISTRAL_API_KEY = config("MISTRAL_API_KEY", default="J6QyRoQf4JkxvtoV9Cod9VyMGIwGzpXg")
MISTRAL_CHAT_URL = "https://api.mistral.ai/v1/chat/completions"


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
            ("ministral-3:14b", Provider.ollama),
            ("", Provider.PollinationsAI),
            ("", Provider.DeepInfra),
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
            "rundfunk",
            "однформація",
            "білебирда",
            "ufffd",
            "\ufffd",
            "стоп стоп",
            "одна формація",
            "інформація про",
            "ufffc",
            "",
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

        model_to_use = self._working_model
        if model_to_use is None:
            model_to_use = ""
        
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
                            logger.warning(f"[MODEL] Response repeated, retrying...")
                            continue
                        logger.info(f"[MODEL] g4f success (attempt {attempt + 1})")
                        return content.strip()
            except Exception as e:
                logger.warning(f"[MODEL] g4f failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)

        if MISTRAL_API_KEY:
            logger.info("[MODEL] Trying Mistral...")
            for attempt in range(3):
                try:
                    loop = asyncio.get_event_loop()

                    def _mistral_request():
                        import requests
                        return requests.post(
                            MISTRAL_CHAT_URL,
                            json={
                                "model": "mistral-small-latest",
                                "messages": all_msgs,
                                "max_tokens": max_tokens,
                            },
                            headers={
                                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                                "Content-Type": "application/json",
                            },
                            timeout=30,
                        )

                    resp = await loop.run_in_executor(None, _mistral_request)
                    if resp.status_code == 200:
                        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                        if self._is_valid(content):
                            logger.info(f"[MODEL] Mistral success (attempt {attempt + 1})")
                            return content.strip()
                    else:
                        logger.warning(f"[MODEL] Mistral non-200 (attempt {attempt + 1}): {resp.status_code} - {resp.text[:100]}")
                except Exception as e:
                    logger.warning(f"[MODEL] Mistral error (attempt {attempt + 1}): {e}")
                await asyncio.sleep(2)

        # Try Puter.js direct (free, no API key)
        logger.info("[MODEL] Trying Puter.js direct...")
        try:
            loop = asyncio.get_event_loop()

            def _puter_request():
                import requests
                return requests.post(
                    "https://api.puter.com/drivers/call",
                    json={
                        "interface": "puter-chat-completion",
                        "driver": "ai-chat",
                        "method": "complete",
                        "args": {
                            "messages": all_msgs,
                            "model": "gpt-4o-mini",
                            "stream": False
                        }
                    },
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "Origin": "http://docs.puter.com",
                        "Referer": "http://docs.puter.com/",
                    },
                    timeout=25,
                )

            resp = await loop.run_in_executor(None, _puter_request)
            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0] if "choices" in data else data.get("result", {})
                message = choice.get("message", {})
                content = message.get("content", "")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "").strip()
                            if text and self._is_valid(text):
                                logger.info("[MODEL] Puter.js success!")
                                return text
                elif content and self._is_valid(content):
                    logger.info("[MODEL] Puter.js success!")
                    return content.strip()
            elif resp.status_code == 401:
                logger.warning("[MODEL] Puter.js requires auth, skipping...")
        except Exception as e:
            logger.warning(f"[MODEL] Puter.js error: {e}")

        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        if OPENROUTER_API_KEY:
            for attempt in range(3):
                try:
                    import requests
                    payload = {
                        "model": "openrouter/free",
                        "messages": all_msgs,
                        "max_tokens": max_tokens,
                    }

                    def _openrouter_request():
                        import requests
                        return requests.post(
                            f"{OPENROUTER_BASE_URL}/chat/completions",
                            json=payload,
                            headers={
                                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                "Content-Type": "application/json",
                                "User-Agent": "Mozilla/5.0",
                            },
                            timeout=25,
                        )

                    loop = asyncio.get_event_loop()
                    resp = await loop.run_in_executor(None, _openrouter_request)
                    if resp.status_code == 200:
                        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                        if self._is_valid(content):
                            if self._is_repeated(content):
                                logger.warning(f"[MODEL] OpenRouter response repeated, retrying...")
                                continue
                            logger.info(f"[MODEL] OpenRouter success (attempt {attempt + 1})")
                            return content.strip()
                    elif resp.status_code == 429:
                        retry_after = resp.headers.get("retry-after", 5)
                        logger.warning(f"[MODEL] OpenRouter rate limited, waiting {retry_after}s...")
                        await asyncio.sleep(min(float(retry_after), 30))
                        continue
                    else:
                        logger.warning(f"[MODEL] OpenRouter non-200 (attempt {attempt + 1}): {resp.status_code}")
                except Exception as e:
                    logger.warning(f"[MODEL] OpenRouter error (attempt {attempt + 1}): {e}")
                await asyncio.sleep(1.5)

        logger.warning("[MODEL] OpenRouter failed, trying g4f fallback...")
        for attempt in range(2):
            try:
                from g4f.client import Client
                client = Client(provider=provider_to_use)
                
                def _g4f_fallback():
                    return client.chat.completions.create(
                        model="",
                        messages=all_msgs,
                        timeout=45
                    )
                
                loop = asyncio.get_event_loop()
                resp = await asyncio.wait_for(
                    loop.run_in_executor(None, _g4f_fallback),
                    timeout=50,
                )
                if resp and resp.choices:
                    content = resp.choices[0].message.content
                    if isinstance(content, str) and self._is_valid(content):
                        if self._is_repeated(content):
                            logger.warning(f"[MODEL] g4f fallback response repeated, retrying...")
                            continue
                        logger.info(f"[MODEL] g4f fallback success (attempt {attempt + 1})")
                        return content.strip()
            except Exception as e:
                logger.warning(f"[MODEL] g4f fallback failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)

        logger.warning("[MODEL] Trying ChatBotChatApp...")
        try:
            from src.utils.free_ai import _chatbot
            combined_input = f"{system_prompt}\n\n" if system_prompt else ""
            combined_input += f"Користувач: {user_message}\nNoxi:"
            result = await _chatbot.chat(combined_input, system_prompt)
            if result and self._is_valid(result):
                logger.info("[MODEL] ChatBotChatApp success!")
                return result.strip()
            else:
                logger.warning("[MODEL] ChatBotChatApp invalid response")
        except Exception as e:
            logger.warning(f"[MODEL] ChatBotChatApp error: {e}")

        logger.warning("[MODEL] Trying DeepAI as last resort...")
        try:
            from src.utils.free_ai import _deepai
            combined_input = f"{system_prompt}\n\n" if system_prompt else ""
            combined_input += f"Користувач: {user_message}\nNoxi:"
            result = await _deepai.chat(combined_input, system_prompt)
            if result and self._is_valid(result):
                logger.info("[MODEL] DeepAI success!")
                return result.strip()
            else:
                logger.warning("[MODEL] DeepAI invalid response")
        except Exception as e:
            logger.warning(f"[MODEL] DeepAI failed: {e}")

        logger.warning("[MODEL] All responses invalid, returning safe fallback")
        return None

    async def vision_chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 512,
    ) -> Optional[str]:
        from g4f import Provider
        provider = Provider.PollinationsAI
        for attempt in range(2):
            try:
                from g4f.client import Client
                client = Client(provider=provider)
                
                def _vision_call():
                    return client.chat.completions.create(
                        model="",
                        messages=messages,
                        timeout=60
                    )
                
                loop = asyncio.get_event_loop()
                resp = await asyncio.wait_for(
                    loop.run_in_executor(None, _vision_call),
                    timeout=65,
                )
                if resp and resp.choices:
                    content = resp.choices[0].message.content
                    if content and self._is_valid(str(content)):
                        logger.info(f"[VISION] g4f success (attempt {attempt + 1})")
                        return str(content).strip()
            except Exception as e:
                logger.warning(f"[VISION] g4f failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)

        if OPENROUTER_API_KEY:
            try:
                import requests
                payload = {
                    "model": "openrouter/free",
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.post(
                        f"{OPENROUTER_BASE_URL}/chat/completions",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                            "Content-Type": "application/json",
                            "User-Agent": "Mozilla/5.0",
                        },
                        timeout=25,
                    ),
                )
                if resp.status_code == 200:
                    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        return content.strip()
            except Exception as e:
                logger.warning(f"[VISION] OpenRouter failed: {e}")

        return None


model_manager = ModelManager()
