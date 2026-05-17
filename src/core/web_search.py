"""Web Search функція для Noxi - адаптовано з C++."""

import asyncio
import logging
import requests
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from decouple import config

logger = logging.getLogger("Noxi")

# Google Search API налаштування
G_SEARCH_KEY = config("G_SEARCH_KEY", default=None)
G_CSE = config("G_CSE", default=None)

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

@dataclass
class SearchResult:
    title: str
    url: str
    content: str


async def web_search(query: str, max_results: int = 5) -> str:
    """
    Виконує веб-пошук через Google Search API.
    Повертає відформатований рядок як у C++.
    """
    logger.info(f"[WebSearch] Google API: {query}")
    
    if not G_SEARCH_KEY or not G_CSE:
        logger.warning("[WebSearch] Google API keys not configured")
        return "Пошук недоступний (немає ключа)."
    
    try:
        params = {
            "key": G_SEARCH_KEY,
            "cx": G_CSE,
            "q": query,
            "num": min(max_results, 10),
            "safe": "off",
            "hl": "uk"
        }
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: requests.get(GOOGLE_SEARCH_URL, params=params, timeout=10)
        )
        
        if response.status_code != 200:
            logger.warning(f"[WebSearch] API error: {response.status_code}")
            return f"Помилка API: {response.status_code}"
        
        data = response.json()
        items = data.get("items", [])
        
        if not items:
            return "Не знайдено результатів."
        
        # Форматуємо як у C++ <search_result>
        output = f"🔍 Результати пошуку '{query}':\n\n"
        
        for i, item in enumerate(items[:max_results], 1):
            title = item.get("title", "")
            url = item.get("link", "")
            snippet = item.get("snippet", "")
            
            output += f"<search_result title=\"{title}\">\n{snippet}\n</search_result>\n"
        
        logger.info(f"[WebSearch] Found {len(items)} results")
        return output.strip()
                
    except Exception as e:
        logger.warning(f"[WebSearch] Error: {e}")
        return f"Помилка пошуку: {e}"


async def search_with_ai(query: str, system_prompt: Optional[str] = None) -> str:
    """
    AI-орієнтований веб-пошук - формує оптимальні запити та повертає результати.
    """
    from src.core.model_manager import model_manager
    
    # System prompt для пошуку
    search_system = system_prompt or """Ти - дослідник в інтернеті.
Твоя робота - шукати інформацію через #query інструмент.
Формулюй запити коротко і чітко, як для Google.
Відповідай на основі знайденої інформації. Не видумуй факти."""
    
    search_user = f"""Знайди інформацію про: {query}

Використай пошук і надай корисну інформацію.
Відповідай українською або мовою запиту."""
    
    # Спочатку шукаємо
    results = await web_search(query, max_results=5)
    
    if not results:
        return "Не знайдено результатів."
    
    # Форматуємо результати
    formatted = "\n\n".join([
        f"**{r.title}**\n{r.content}\n🔗 {r.url}"
        for r in results
    ])
    
    # Додаткова обробка через AI для кращої відповіді
    try:
        ai_response = await model_manager.chat(
            messages=[
                {"role": "user", "content": f"На основі цієї інформації відповіди на питання: {query}\n\n{formatted}"}
            ],
            category="fast",
            max_tokens=500,
            system_prompt="Ти асистент. На основі наданої інформації дай коротку відповідь українською."
        )
        if ai_response:
            return ai_response
    except Exception as e:
        logger.warning(f"[WebSearch] AI summary failed: {e}")
    
    return formatted


async def search_and_respond(
    query: str,
    user_message: str,
    openai_client: Any = None
) -> str:
    """
    Повний цикл пошуку з відповіддю - як C++ searchAI.
    """
    logger.info(f"[WebSearch] search_and_respond: {query}")
    
    # Шукаємо інформацію
    results = await web_search(query, max_results=3)
    
    if not results:
        return "Не знайшов інформації. Спробуй інший запит."
    
    # Форматуємо для контексту
    context = "\n".join([
        f"- {r.title}: {r.content[:150]}..."
        for r in results
    ])
    
    # Запитуємо AI для формування відповіді
    if openai_client:
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Ти дослідник. Відповідай на основі наданих результатів пошуку."},
                    {"role": "user", "content": f"Питання: {user_message}\n\nЗнайдено:\n{context}"}
                ],
                max_tokens=300
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"[WebSearch] AI response failed: {e}")
    
    # Fallback - просто повертаємо результати
    return f"Знайшов:\n\n{context}"


class WebSearchTool:
    """
    Інструмент веб-пошуку для використання в LLM як tool.
    Адаптовано з C++ OpenAITools.
    """
    
    @staticmethod
    def get_tool_definition() -> Dict[str, Any]:
        """Повертає definition для tool_call."""
        return {
            "name": "web_search",
            "description": "Пошук інформації в інтернеті. Використовуй для актуальних даних.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Запит для пошуку (оптимізований для Google)"
                    }
                },
                "required": ["query"]
            }
        }
    
    @staticmethod
    async def execute(query: str) -> str:
        """Виконання пошуку."""
        results = await web_search(query, max_results=5)
        
        if not results:
            return "Не знайдено результатів."
        
        formatted = ""
        for r in results:
            formatted += f"<search_result title=\"{r.title}\">\n{r.content}\n</search_result>\n"
        
        return formatted


# Експортуємо для використання
__all__ = ["web_search", "search_with_ai", "search_and_respond", "WebSearchTool", "SearchResult"]