"""Freegen.app client for image generation using WebSocket protocol."""

import asyncio
import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import aiohttp
import backoff
from bs4 import BeautifulSoup

logger = logging.getLogger("Noxi")

SIGNER_URL = "https://prompt-signer.freegen.app"
GENERATOR_URL = "https://image-generator.freegen.app"
WEBSOCKET_URL = "wss://websocket-bridge.freegen.app/ws"
STATS_URL = "https://stats.freegen.app"

PROMPT = (
    "one, photo realistic, semi-anime close-up selfie of the same gothic catgirl, "
    "mature woman around 30, slim, random natural selfie poses, arm stretched toward the camera as if holding a hidden phone, "
    "forearm partly in frame but no phone visible, varying selfie angles and slight head tilts, always looking into the camera lens, "
    "relaxed casual posture, soft low-contrast lighting, porcelain skin with cool undertones, light blush on cheeks and nose, "
    "smooth realistic shading, delicate V-shaped face, small pointed chin, soft jawline, "
    "messy shoulder-length dark navy-blue hair with cobalt highlights, tousled strands over shoulders and collarbones, "
    "uneven jagged bangs partly covering one eye, tall fluffy black cat ears with dark gray inner fur, "
    "deep dark sapphire eyes, almost midnight-blue, strong dark limbal rings, soft reflections, small catchlights, "
    "sleepy seductive gaze, slightly downturned outer corners, long eyelashes, thin navy eyebrows, "
    "subtle reddish-purple eyeshadow, faint under-eye shadows, small semi-realistic nose, "
    "glossy muted rose-pink lips slightly parted, tiny sharp vampire fangs, gentle melancholic smile, "
    "realistic semi-anime girl look, wearing black gothic lace lingerie dress with deep neckline, "
    "semi-transparent lace cups and trim, thin straps, sheer mesh on chest, "
    "loose translucent black chiffon robe with wide sleeves and lace edges slipping off one shoulder, "
    "large black tattoo between collarbones and cleavage, black lace choker with dark rose, "
    "long silver cross pendant, thin silver chains, layered necklaces, dangling gothic earrings with dark gemstones, "
    "moody gothic bedroom background, selfie depth behind her, soft dim blue ambient light, soft shadows, "
    "subtle candle glow, faint light rays, small dust particles, dark furniture, framed monochrome art, "
    "gothic black cat statue with blue pendant, dark teal and blue palette, cinematic but gentle contrast, "
    "painterly shading, detailed hair, fabric and jewelry, expressive realistic semi-anime face and chest."
)

REF_IMAGE_PATH = Path("data/character/Noxi-main.jpg")


def _create_ws_auth(job_id: str, timestamp: int) -> str:
    """Create WebSocket auth hash (matches browser crypto.subtle logic)."""
    message = f"{job_id}{timestamp}"
    digest = hashlib.sha256(message.encode()).digest()
    return base64.b64encode(digest).decode()


class FreegenClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _load_ref_image(self) -> Optional[bytes]:
        if not REF_IMAGE_PATH.exists():
            logger.warning(f"[FREEGEN] Ref image not found: {REF_IMAGE_PATH}")
            return None
        return REF_IMAGE_PATH.read_bytes()

    def _resize_image(self, image_bytes: bytes, max_size: int = 1024) -> str:
        """Resize image to max dimension, return base64 data URL."""
        import io
        try:
            from PIL import Image
        except ImportError:
            logger.warning("[FREEGEN] PIL not available, using original bytes")
            return base64.b64encode(image_bytes).decode()

        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        buf = io.BytesIO()
        fmt = "PNG" if img.mode == "RGBA" else "JPEG"
        img.save(buf, format=fmt, quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
        return f"data:{mime};base64,{b64}"

    @backoff.on_exception(backoff.expo, (aiohttp.ClientError, asyncio.TimeoutError), max_time=20)
    async def _sign_prompt(self, session: aiohttp.ClientSession, prompt: str) -> Optional[dict]:
        """Step 1: Get signed timestamp + signature from signer service."""
        try:
            async with session.post(
                SIGNER_URL,
                json={"prompt": prompt},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning(f"[FREEGEN] Signer status: {resp.status}")
                return None
        except Exception as e:
            logger.warning(f"[FREEGEN] Signer error: {e}")
            return None

    @backoff.on_exception(backoff.expo, (aiohttp.ClientError, asyncio.TimeoutError), max_time=20)
    async def _request_generation(
        self,
        session: aiohttp.ClientSession,
        prompt: str,
        ts: str,
        sig: str,
        ratio_id: str,
        image_data: Optional[str] = None,
    ) -> Optional[dict]:
        """Step 2: Request image generation, get job_id."""
        body = {
            "prompt": prompt,
            "ts": ts,
            "sig": sig,
            "ratio_id": ratio_id,
        }
        if image_data:
            body["image_data"] = image_data

        try:
            async with session.post(
                GENERATOR_URL,
                json=body,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                text = await resp.text()
                logger.warning(f"[FREEGEN] Generator status: {resp.status} - {text[:200]}")
                return None
        except Exception as e:
            logger.warning(f"[FREEGEN] Generator error: {e}")
            return None

    async def generate(self, timeout: int = 180) -> Optional[bytes]:
        ref_bytes = self._load_ref_image()
        if not ref_bytes:
            return None

        session = await self._get_session()
        start = time.time()

        signed = await self._sign_prompt(session, PROMPT)
        if not signed:
            logger.warning("[FREEGEN] Failed to sign prompt")
            return None

        ts = signed.get("ts", "")
        sig = signed.get("sig", "")
        ts_str = str(ts)
        sig_str = str(sig)
        logger.info(f"[FREEGEN] Signed: ts={ts_str[:10]}..., sig={sig_str[:20]}...")

        resized = self._resize_image(ref_bytes) if ref_bytes else None

        job_data = await self._request_generation(
            session, PROMPT, ts, sig, "3:4", resized
        )
        if not job_data:
            logger.warning("[FREEGEN] Failed to get job_id")
            return None

        job_id = job_data.get("job_id")
        if not job_id:
            logger.warning("[FREEGEN] No job_id in response")
            return None

        logger.info(f"[FREEGEN] Job started: {job_id}")

        ws_auth = _create_ws_auth(job_id, int(time.time()))

        queue = asyncio.Queue()

        async def on_ws_message(message: str):
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")
                if msg_type == "result":
                    image_data = data.get("image_data", "")
                    if image_data:
                        img_bytes = self._decode_image_data(image_data)
                        if img_bytes:
                            logger.info(f"[FREEGEN] Image received: {len(img_bytes)} bytes")
                            await queue.put(("result", img_bytes))
                        else:
                            await queue.put(("error", None))
                    else:
                        await queue.put(("error", None))
                elif msg_type == "error":
                    logger.warning(f"[FREEGEN] WS error: {data}")
                    await queue.put(("error", None))
            except Exception as e:
                logger.warning(f"[FREEGEN] WS parse error: {e}")
                await queue.put(("error", None))

        ws_task = None
        ws = None
        ws_result = None

        try:
            ws = await session.ws_connect(
                WEBSOCKET_URL,
                timeout=aiohttp.ClientTimeout(total=300),
            )

            auth_msg = json.dumps({
                "type": "subscribe",
                "job_id": job_id,
                "auth": ws_auth,
            })
            await ws.send_str(auth_msg)
            logger.info("[FREEGEN] WS subscribed")

            ws_task = asyncio.create_task(self._ws_reader(ws, on_ws_message))

            remaining = timeout
            while remaining > 0:
                try:
                    ws_result = await asyncio.wait_for(queue.get(), timeout=min(remaining, 5))
                    break
                except asyncio.TimeoutError:
                    remaining -= 5
                    if remaining <= 0:
                        break
                    continue
                except Exception as e:
                    logger.warning(f"[FREEGEN] WS error: {e}")
                    break

                if ws_task.done() and not queue.empty():
                    break

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.warning(f"[FREEGEN] WS error: {e}")

        if ws_task:
            ws_task.cancel()

        if ws:
            try:
                await ws.close()
            except Exception:
                pass

        if ws_result and ws_result[0] == "result":
            return ws_result[1]

        logger.warning("[FREEGEN] Timeout or failed")
        return None

    def _decode_image_data(self, data: str) -> Optional[bytes]:
        """Decode base64 image data (data URL or raw)."""
        try:
            if data.startswith("data:"):
                b64 = data.split(",", 1)[1]
            else:
                b64 = data
            return base64.b64decode(b64)
        except Exception as e:
            logger.warning(f"[FREEGEN] Image decode error: {e}")
            return None

    async def _ws_reader(self, ws: aiohttp.ClientWebSocketResponse, callback):
        try:
            while True:
                msg = await ws.receive_str()
                await callback(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[FREEGEN] WS reader error: {e}")


freegen_client = FreegenClient()