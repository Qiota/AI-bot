"""Vision API для Noxi — аналіз зображень."""

import asyncio
import base64
from typing import Optional, List, Dict, Any
import requests
from decouple import config
import logging

logger = logging.getLogger("Noxi")


class VisionClient:
    def __init__(self):
        self.api_key = config("OPENROUTER_API_KEY", default=None)
        self.base_url = "https://openrouter.ai/api/v1"
        self._ready = bool(self.api_key)

        if self._ready:
            logger.info("[VISION] OpenRouter enabled")
        else:
            logger.info("[VISION] Using g4f fallback")
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

    async def analyze_image(
        self,
        image_data: bytes,
        image_url: Optional[str] = None,
        prompt: str = "Ти - Noxi, AI асистент. Опиши що бачиш на картинці детально українською."
    ) -> Optional[str]:
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

        return await self._vision(messages)

    async def _vision(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        if self.api_key:
            try:
                import requests as _req
                payload = {
                    "model": "openrouter/free",
                    "messages": messages,
                    "max_tokens": 512,
                }
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: _req.post(
                        self.base_url + "/chat/completions",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                            "User-Agent": "Mozilla/5.0",
                        },
                        timeout=25,
                    ),
                )
                if resp.status_code == 200:
                    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        logger.info("[VISION] OpenRouter success")
                        return content.strip()
            except Exception as e:
                logger.warning(f"[VISION] OpenRouter failed: {e}")

        for attempt in range(2):
            try:
                from g4f.client import Client
                client = Client()
                loop = asyncio.get_event_loop()
                resp = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: client.chat.completions.create(model="", messages=messages)  # type: ignore[arg-type]
                    ),
                    timeout=45,
                )
                if resp and resp.choices:
                    content = resp.choices[0].message.content
                    if content:
                        logger.info(f"[VISION] g4f success (attempt {attempt + 1})")
                        return str(content).strip()
            except Exception as e:
                logger.warning(f"[VISION] g4f failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)

        return None


vision_client = VisionClient()


async def analyze_image(
    image_data: Optional[bytes] = None,
    image_url: Optional[str] = None,
    prompt: Optional[str] = None
) -> Optional[str]:
    return await vision_client.analyze_image(image_data or b"", image_url, prompt or "Що ти бачиш?")


def is_vision_available() -> bool:
    return vision_client.is_available()