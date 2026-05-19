"""Model Manager - розподіл моделей по компонентах мозку Noxi з розширеним логуванням Termux."""

import asyncio
import logging
from typing import Optional, List, Dict, Any
import requests
from decouple import config

logger = logging.getLogger("Noxi")

# Настройки внешних API (Резервные)
OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default=None)
OPENROUTER_BASE_URL = "https://openrouter.ai"
MISTRAL_API_KEY = config("MISTRAL_API_KEY", default="J6QyRoQf4JkxvtoV9Cod9VyMGIwGzpXg")
MISTRAL_CHAT_URL = "https://mistral.ai"

# Настройки локальной Ollama в Termux (Основной приоритет)
OLLAMA_BASE_URL = config("OLLAMA_BASE_URL", default="https://4bef98ada8ddde1d-94-153-10-45.serveousercontent.com")
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
        """Прямой синхронный запрос к локальной Ollama в Termux с расширенным логированием."""
        base_url = OLLAMA_BASE_URL.strip().rstrip('/')
        url = f"{base_url}/api/chat"
        
        # Исправлено: Удален проблемный параметр "think": False из опций.
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.7
            }
        }
        
        logger.info(f"[TERMUX-OLLAMA] Отправка запроса на {url} (Модель: {OLLAMA_MODEL})")
        
        def _request():
            return requests.post(url, json=payload, timeout=90)
            
        try:
            start_time = asyncio.get_event_loop().time()
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, _request)
            end_time = asyncio.get_event_loop().time()
            
            logger.info(f"[TERMUX-OLLAMA] Сервер ответил за {end_time - start_time:.2f} сек. Статус-код: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("message", {}).get("content", "")
                if content:
                    logger.info(f"[TERMUX-OLLAMA] Успешный ответ получен. Длина текста: {len(content)} симв.")
                else:
                    logger.warning("[TERMUX-OLLAMA] Сервер вернул пустой текст ('content' пуст)")
                return content
            else:
                logger.error(f"[TERMUX-OLLAMA] Ошибка сервера Termux. Текст ответа: {response.text[:200]}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("[TERMUX-OLLAMA] Ошибка: Превышено время ожидания (Timeout).")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"[TERMUX-OLLAMA] Ошибка подключения: Сервер по адресу {base_url} недоступен.")
            return None
        except Exception as e:
            logger.error(f"[TERMUX-OLLAMA] Непредвиденная ошибка при запросе: {str(e)}")
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
        for attempt in range(2):
            logger.info(f"[MODEL] Попытка {attempt + 1} запустить локальную Ollama...")
            try:
                content = await asyncio.wait_for(
                    self._call_ollama(all_msgs, max_tokens),
                    timeout=95
                )
                if content and self._is_valid(content):
                    if self._is_repeated(content):
                        logger.warning("[MODEL] Ответ Ollama продублировался, повторный запрос...")
                        continue
                    return content.strip()
                else:
                    if content:
                        logger.warning(f"[MODEL] Ответ Ollama на попытке {attempt + 1} не прошел валидацию _is_valid")
            except asyncio.TimeoutError:
                logger.error(f"[MODEL] Общий таймаут asyncio на попытке {attempt + 1}")
            await asyncio.sleep(1)

        """ ПРИОРЕТЕТ 2: Резерв через g4f (ЗАКОММЕНТИРОВАНО)
        logger.info("[MODEL] Локальная Ollama не ответила. Переключаюсь на резерв g4f...")
        model_to_use = self._working_model or ""
        provider_to_use = self._working_provider or None
        
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
                    content = resp.choices.message.content
                    if isinstance(content, str) and self._is_valid(content):
                        if self._is_repeated(content):
                            continue
                        logger.info(f"[MODEL] g4f резерв сработал (попытка {attempt + 1})")
                        return content.strip()
            except Exception as e:
                logger.warning(f"[MODEL] g4f резерв упал: {e}")
            await asyncio.sleep(2)
        """

        """ ПРИОРЕТЕТ 3: Финальный резерв через официальное Mistral API (ЗАКОММЕНТИРОВАНО)
        if MISTRAL_API_KEY:
            logger.info("[MODEL] Пробую аварийный Mistral API...")
            headers = {"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
            payload = {"model": "mistral-tiny", "messages": all_msgs, "max_tokens": max_tokens}
            try:
                def _mistral_request():
                    return requests.post(MISTRAL_CHAT_URL, json=payload, headers=headers, timeout=30)
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(None, _mistral_request)
                if resp.status_code == 200:
                    content = resp.json()["choices"]["message"]["content"]
                    if content and self._is_valid(content):
                        logger.info("[MODEL] Аварийный Mistral API успешно ответил")
                        return content.strip()
            except Exception as e:
                logger.error(f"[MODEL] Критическая ошибка Mistral API: {e}")
        """

        logger.critical("[MODEL] Локальная Ollama отказала, а внешние резервы закомментированы.")
        return None


# Инициализация глобального экземпляра для корректного импорта в src.aichat
model_manager = ModelManager()
