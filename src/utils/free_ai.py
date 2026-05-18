"""Free AI using Google Search API for real answers."""

import requests
import re
import asyncio
import aiohttp
import time
from typing import Optional
from decouple import config


class DeepAIAI:
    """Last resort AI using DeepAI web interface."""

    def __init__(self):
        self.base_url = "https://deepai.org/chat"
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
            })
        return self._session

    async def chat(self, message: str, system_prompt: str = "") -> Optional[str]:
        """Get AI response via DeepAI web interface using requests."""
        try:
            session = self._get_session()
            loop = asyncio.get_event_loop()

            async def _scrape():
                resp = session.get(self.base_url, timeout=15)
                if resp.status_code != 200:
                    return None

                html = resp.text
                csrf_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
                csrf_token = csrf_match.group(1) if csrf_match else ""

                if not csrf_token:
                    cookies = session.cookies.get_dict()
                    resp2 = session.get(self.base_url, timeout=15)
                    html2 = resp2.text
                    csrf_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html2)
                    csrf_token = csrf_match.group(1) if csrf_match else ""

                if not csrf_token:
                    return None

                cookies = session.cookies.get_dict()
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": self.base_url,
                    "X-CSRFToken": csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                }

                api_url = "https://deepai.org/api/chat"
                data = {
                    "message": message[:500],
                    "csrfmiddlewaretoken": csrf_token,
                }

                resp3 = session.post(api_url, data=data, headers=headers, timeout=30)
                if resp3.status_code == 200:
                    try:
                        json_resp = resp3.json()
                        return json_resp.get("response", json_resp.get("html", ""))
                    except Exception:
                        return resp3.text[:500]

                return None

            result = await asyncio.wait_for(_scrape(), timeout=35)
            if result and len(result) > 5:
                return result

        except Exception as e:
            print(f"DeepAI error: {e}")

        return None


class ChatBotChatAppAI:
    """Free AI using chatbotchatapp.com"""

    def __init__(self):
        self.base_url = "https://chatbotchatapp.com"
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
                "Origin": self.base_url,
                "Referer": self.base_url,
            })
        return self._session

    async def chat(self, message: str, system_prompt: str = "") -> Optional[str]:
        """Get AI response via chatbotchatapp"""
        try:
            session = self._get_session()
            loop = asyncio.get_event_loop()

            async def _fetch():
                headers = {
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                }

                models = ["gpt-5", "deepseek-r1-0528", "qwen-3.5", "mistral-large-3"]

                for model in models:
                    try:
                        resp = session.post(
                            f"{self.base_url}/api/v1/chat",
                            json={
                                "message": message[:500],
                                "model": model,
                                "system_prompt": system_prompt[:200] if system_prompt else "",
                            },
                            headers=headers,
                            timeout=20,
                        )
                    except Exception:
                        continue

                    if resp.status_code == 200:
                            data = resp.json()
                            r1 = data.get("response")
                            r2 = data.get("message") if r1 is None else None
                            r3 = data.get("content") if r2 is None else None
                            content = r1 or r2 or r3 or ""
                            if content:
                                return content

                return None

            result = await asyncio.wait_for(_fetch(), timeout=25)
            if result and len(result) > 5:
                return result

        except Exception as e:
            print(f"ChatBotChatApp error: {e}")

        return None


_deepai = DeepAIAI()
_chatbot = ChatBotChatAppAI()


class GoogleAI:
    """AI using Google Search API - real web results as AI responses."""
    
    def __init__(self):
        self.G_SEARCH_KEY = config("G_SEARCH_KEY", default=None)
        self.G_CSE = config("G_CSE", default=None)
        self.base_url = "https://www.googleapis.com/customsearch/v1"
    
    async def chat(self, message: str) -> Optional[str]:
        """Get AI response via Google Search."""
        if not self.G_SEARCH_KEY or not self.G_CSE:
            return None
        
        try:
            params = {
                "key": self.G_SEARCH_KEY,
                "cx": self.G_CSE,
                "q": message,
                "num": 5,
                "safe": "off",
                "hl": "uk",
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    items = data.get("items", [])
                    
                    if not items:
                        return None
                    
                    response_parts = []
                    
                    for i, item in enumerate(items[:3], 1):
                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        
                        snippet = re.sub(r'<[^>]+>', '', snippet)
                        if len(snippet) > 200:
                            snippet = snippet[:200] + "..."
                        
                        response_parts.append(f"{i}. {title}\n{snippet}\n")
                    
                    if response_parts:
                        return (
                            "🔍 Знайшов інформацію:\n\n" +
                            "".join(response_parts) +
                            "\n💡 Напиши /google " + message + " для детальнішого пошуку!"
                        )
                    
        except Exception as e:
            print(f"Google Search error: {e}")
        
        return None


class LocalAI:
    """Local keyword-based AI."""
    
    def __init__(self):
        self.responses = {
            r'\b(привіт|вітаю|хай|hello|hi|hey)\b': 
                "Привіт! 👋 Я - AI бот для Discord.",
            r'\b(допоможи|help)\b':
                "Можу допомогти: /google <текст>, .mood, .reset",
            r'\b(як справи)\b': "Все добре! А як у тебе?",
            r'\b(що робиш)\b': "Я - AI асистент!",
            r'\b(хто ти)\b': "Я - AI бот з пам'яттю!",
            r'\b(кот)\b': "Мяу! 🐱",
            r'\b(собака)\b': "Гав-гав! 🐕",
            r'\b(їсти)\b': "Смачного! 🍕",
            r'\b(спати)\b': "Солодких снів! 💤",
            r'\b(час)\b': lambda: f"Зараз {time.strftime('%H:%M')} 🕐",
            r'\b(пошук|знайди)\b': "Використай /google <текст>!",
        }
    
    def chat(self, message: str) -> str:
        msg = message.lower()
        for pattern, response in self.responses.items():
            if re.search(pattern, msg, re.IGNORECASE):
                return response() if callable(response) else response
        
        return f"Напиши /google {message} для пошуку!"


_google_ai = GoogleAI()
_local_ai = LocalAI()


def get_ai_response(message: str, context: str = "") -> str:
    """Get AI response - try Google Search first, then local."""
    try:
        result = asyncio.run(_google_ai.chat(message))
        if result:
            return result
    except Exception as e:
        print(f"AI error: {e}")
    
    return _local_ai.chat(message)


async def scrape_url(url: str, max_words: int = 1500) -> Optional[str]:
    """Scrape web page using g4f's fetch_and_scrape."""
    try:
        from g4f.tools.fetch_and_scrape import fetch_and_scrape
        from aiohttp import ClientSession
        async with ClientSession() as session:
            content = await fetch_and_scrape(
                session=session,
                url=url,
                max_words=max_words,
                add_metadata=True
            )
            return content if content else None
    except Exception as e:
        print(f"scrape_url error: {e}")
        return None