"""Simple DuckDuckGo search scraper."""

import requests
import re
import time

class DDGSearch:
    """Simple search using DuckDuckGo."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://duckduckgo.com/',
        })
    
    def search(self, query: str) -> str:
        """Search and return formatted results."""
        try:
            response = self.session.get(
                'https://duckduckgo.com/html/',
                params={'q': query},
                timeout=15
            )
            
            if response.status_code != 200:
                return self._fallback(query)
            
            html = response.text
            
            # Check if we're blocked
            if 'captcha' in html.lower() or 'blocked' in html.lower():
                return self._fallback(query)
            
            # Extract results
            # Format: <a class="result__a" href="URL">Title</a>
            results = []
            
            # Find all result links
            link_pattern = r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
            snippet_pattern = r'<a class="result__snippet"[^>]*>([^<]+)</a>'
            
            links = re.findall(link_pattern, html)
            snippets = re.findall(snippet_pattern, html)
            
            if not links:
                return self._fallback(query)
            
            output = f"🔍 '{query}':\n\n"
            
            for i, (url, title) in enumerate(links[:3], 1):
                # Clean title
                title = re.sub(r'<[^>]+>', '', title).strip()
                
                # Get corresponding snippet
                snippet = ""
                if i-1 < len(snippets):
                    snippet = re.sub(r'<[^>]+>', '', snippets[i-1])
                    snippet = snippet.replace('&amp;', '&').replace('&quot;', '"')
                    snippet = snippet.strip()
                    if len(snippet) > 100:
                        snippet = snippet[:100] + "..."
                
                output += f"{i}. {title}"
                if snippet:
                    output += f"\n   {snippet}"
                output += "\n\n"
            
            return output.strip()
            
        except Exception as e:
            print(f"DDG error: {e}")
            return self._fallback(query)
    
    def _fallback(self, query: str) -> str:
        """Simple responses for common queries."""
        query_lower = query.lower()
        
        # Greetings and common responses
        responses = {
            'привіт': "Привіт! 👋 Я - AI бот. Напиши мені що-небудь!",
            'вітаю': "Вітаю! 😊 Радий тебе бачити!",
            'хай': "Привіт! 👋",
            'hello': "Hello! 👋",
            'hi': "Hi there! 👋",
            'допоможи': "Напиши /google <текст> для пошуку!",
            'help': "Commands: /google, /info, .mood, .reset",
            'як справи': "Все добре! Дякую. А як у тебе? 😊",
            'що робиш': "Спілкуюсь з тобою! 💬",
            'хто ти': "Я - AI бот для Discord!",
            'кот': "Мяу! 🐱",
            'собака': "Гав! 🐕",
            'їсти': "Смачного! 🍕",
            'спати': "Солодких снів! 💤",
            'час': f"Зараз {time.strftime('%H:%M')} 🕐",
            'погода': "Напиши /google погода в [місто]!",
            'музика': "Напиши /google пісня [назва]! 🎵",
            'відео': "Напиши /google відео [назва]! 🎬",
            'новини': "Напиши /google новини! 📰",
            'рецепт': "Напиши /google рецепт [страва]! 🍳",
        }
        
        for key, response in responses.items():
            if key in query_lower:
                return response
        
        # Default - suggest search
        return f"Напиши /google {query} для пошуку інформації!"


_ddg = DDGSearch()

def get_ai_response(message: str) -> str:
    """Get AI response - try DDG search first."""
    # First try quick local responses
    msg_lower = message.lower().strip()
    
    # Direct responses for simple cases
    quick_responses = {
        'привіт': 'Привіт! 👋 Радий тебе бачити!',
        'вітаю': 'Вітаю! 😊',
        'хай': 'Привіт! 👋',
        'допоможи': 'Можу допомогти! Напиши /google для пошуку',
        'як справи': 'Все добре! 🤖',
        'що робиш': 'Спілкуюся з тобою! 💬',
        'хто ти': 'Я - AI бот!',
        'кот': 'Мяу! 🐱',
        'собака': 'Гав! 🐕',
        'час': f'{time.strftime("%H:%M")} 🕐',
    }
    
    for key, response in quick_responses.items():
        if key in msg_lower:
            return response
    
    # Try DDG search
    result = _ddg.search(message)
    return result