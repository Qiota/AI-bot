"""Free AI using Google Search API for real answers."""

import requests
import re
import asyncio
import aiohttp
import time
from typing import Optional
from decouple import config

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