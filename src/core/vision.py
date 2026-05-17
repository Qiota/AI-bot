"""Vision API для Noxi — аналіз зображень."""

import asyncio
import base64
from typing import Optional, List, Dict, Any
import requests
from decouple import config
import logging

logger = logging.getLogger("Noxi")


class VisionClient:
    """Клієнт для аналізу зображень з fallback."""

    def __init__(self):
        self.api_key = config("OPENROUTER_API_KEY", default=None)
        self.base_url = "https://openrouter.ai/api/v1"
        self._ready = bool(self.api_key)

        if self._ready:
            logger.info("[VISION] Увімкнено з OpenRouter")
        else:
            logger.info("[VISION] Використовуємо безкоштовний fallback (aiautotagging)")
            self._ready = True

    def is_available(self) -> bool:
        return self._ready

    def _detect_mime_type(self, data: bytes) -> str:
        if data.startswith(b'\x89PNG'):
            return "image/png"
        elif data.startswith(b'\xff\xd8\xff'):
            return "image/jpeg"
        elif data.startswith(b'GIF8'):
            return "image/gif"
        return "image/jpeg"

    async def _analyze_openrouter(
        self, image_data: bytes, image_url: Optional[str], prompt: str
    ) -> Optional[str]:
        """Аналіз через Model Manager (vision)."""
        try:
            from src.core.model_manager import model_manager

            if image_data:
                image_b64 = base64.b64encode(image_data).decode("utf-8")
                mime = self._detect_mime_type(image_data)
                image_url = f"data:{mime};base64,{image_b64}"

            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]}
            ]

            result = await model_manager.vision_chat(messages=messages, max_tokens=1024)
            return result

        except Exception as e:
            logger.warning(f"[VISION] Model Manager: {e}")

        return None

    async def _analyze_g4f(
        self, image_data: Optional[bytes], image_url: Optional[str], prompt: str
    ) -> Optional[str]:
        """g4f fallback для vision - використовуємо PollinationsAI (без ключа)."""
        try:
            import g4f
            from g4f import Provider

            content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]

            if image_data:
                image_b64 = base64.b64encode(image_data).decode("utf-8")
                mime = self._detect_mime_type(image_data)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image_b64}"}
                })
            elif image_url:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": image_url}
                })

            messages: List[Dict[str, Any]] = [{"role": "user", "content": content}]

            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: g4f.ChatCompletion.create(
                        model="gpt-4o",
                        messages=messages,  # type: ignore[arg-type]
                        provider=Provider.PollinationsAI
                    )
                ),
                timeout=45
            )

            return str(response).strip() if response else None

        except asyncio.TimeoutError:
            logger.warning("[VISION] g4f PollinationsAI timeout")
        except Exception as e:
            logger.warning(f"[VISION] g4f fallback: {e}")

        return None

    async def _analyze_free(
        self, image_data: Optional[bytes], image_url: Optional[str]
    ) -> Optional[str]:
        """Безкоштовний aiautotagging.com (500/день, без ключа)."""
        try:
            if not image_data and image_url:
                return None

            loop = asyncio.get_event_loop()
            files = {'file': ('image.jpg', image_data, 'image/jpeg')} if image_data else None

            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    'https://aiautotagging.com/api/tag/image',
                    files=files,
                    data={'url': image_url, 'maxTags': 8},
                    timeout=15
                )
            )

            if response.status_code == 200:
                data = response.json()
                tags = data.get('tags', [])
                if tags:
                    labels = [t.get('label', '') for t in tags[:6]]
                    return "Виявлено: " + ", ".join(filter(None, labels))

        except Exception as e:
            logger.warning(f"[VISION] Free API: {e}")

        return None

    async def analyze_image(
        self,
        image_data: bytes,
        image_url: Optional[str] = None,
        prompt: str = "Ти - Noxi, AI асистент. Опиши що бачиш на картинці детально українською."
    ) -> Optional[str]:
        # Спробуємо OpenRouter router (автоматично вибирає модель з vision)
        if self.api_key:
            try:
                result = await asyncio.wait_for(
                    self._analyze_openrouter(image_data, image_url, prompt),
                    timeout=50
                )
                if result:
                    return result
            except asyncio.TimeoutError:
                logger.warning("[VISION] OpenRouter timeout")

        # Fallback на g4f (з таймаутом)
        try:
            result = await asyncio.wait_for(
                self._analyze_g4f(image_data, image_url, prompt),
                timeout=40
            )
            if result:
                return result
        except asyncio.TimeoutError:
            logger.warning("[VISION] g4f timeout")

        # Fallback на безкоштовний aiautotagging
        return await self._analyze_free(image_data, image_url)


vision_client = VisionClient()


async def analyze_image(
    image_data: Optional[bytes] = None,
    image_url: Optional[str] = None,
    prompt: Optional[str] = None
) -> Optional[str]:
    return await vision_client.analyze_image(image_data or b"", image_url, prompt or "Що ти бачиш?")


def is_vision_available() -> bool:
    return vision_client.is_available()