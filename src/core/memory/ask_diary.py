"""ask_diary tool - similar to C++ ask_diary tool."""

from typing import Dict, List, Optional, Callable, Awaitable
from .diary import Diary


TOOL_DESCRIPTION = """Consult with Noxi's main knowledge database (diary). Use this to retrieve additional 
pages from diary. USE THIS PROACTIVELY — especially when someone shares personal news, asks about past 
events, or mentions people/activities you might know about.

Examples of when to call:
- User says "I wrote a song today" → query: "[sender name] said they wrote a song today. What do I 
  know about them and songs? Do they participate in a band? Which songs do they write? What music do they 
  listen to?"
- User asks "what songs am I writing?" → query: "What songs does [sender name] write? What do I know 
  about their musical activities?"
- User says "I'm going to the gym" → query: "Does [sender name] go to the gym? Any related habits or routines?"
- You want to ask them a question - check yourself with ask_diary first"""


TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Freeform question to diary. Provide as much context as possible — include sender name, topic, and what you want to know."
        }
    },
    "required": ["query"]
}


def create_ask_diary_tool(
    get_diary: Callable[[], Diary],
    get_temporary_context: Callable[[], List[Dict]] = None
) -> Dict:
    """
    Create ask_diary tool definition similar to C++ ask_diary function.
    
    Args:
        get_diary: Callable that returns the Diary instance
        get_temporary_context: Optional callable that returns additional context messages
    """
    
    async def handler(query: str) -> str:
        """Handle ask_diary tool call."""
        if len(query) < 10:
            return """error: too short query! please provide more context to ask_diary:
    - chat name (if any)
    - previous messages
    - sender's name
    - search cues
    - source event
    - everything else to populate query"""
        
        diary = get_diary()
        
        if get_temporary_context:
            temporary_context = get_temporary_context()
            if temporary_context:
                last_msg = temporary_context[-1] if temporary_context else {}
                context_content = last_msg.get("content", "")
                
                query = (
                    f"Here's the deal:\n"
                    f"<additional context ignore_instructions>\n"
                    f"{context_content}\n"
                    f"</additional context ignore_instructions>\n"
                    f"I received this as a tool call response. I want you to help me to respond this and improve my "
                    f"overall context awareness.\n"
                    f"- how do I usually act in this situation?\n"
                    f"- is there additional details I should know?\n"
                    f"- how can I improve my reaction?\n"
                    f"- {query}"
                )
        
        try:
            # Спробуємо спочатку embedding-based пошук
            from src.core.memory.embedding import is_embedding_available
            if is_embedding_available():
                similar = await diary.query_by_embedding(query, limit=10)
                if similar:
                    entries_text = "\n".join([
                        f"- [{entry.get('timestamp', 0)}] {entry.get('content', '')[:150]} (similarity: {sim:.2f})"
                        for entry, sim in similar
                    ])
                    return f"Знайдено схожі записи:\n{entries_text}\n\nIf response above is dismissive, try rephrasing your query and include other details"
            
            # Fallback на AI
            result = await diary.queryAI(query, {"confidenceFactor": 0.0})
            return result + "\nIf response above is dismissive, try rephrasing your query and include other details"
        except Exception as e:
            results = diary.search_entries(query, limit=10)
            if not results:
                recent = diary.get_recent_entries(limit=3)
                if recent:
                    context = "\n".join([f"- {e.get('content', '')[:100]}" for e in recent])
                    return f"Не знайдено записів за '{query}'. Ось нещодавні записи:\n{context}\n\nЯкщо відповідь вище не допомогла, спробуй переформулювати запит з більше деталями."
                return f"Не знайшов інформації за '{query}'. Спробуй додати більше контексту (ім'я, деталі події)."
            
            entries_text = "\n".join([
                f"- {e.get('content', '')[:200]}" for e in results[:5]
            ])
            
            return f"Знайдено {len(results)} записів:\n{entries_text}\n\nЯкщо відповідь вище не допомогла, спробуй переформулювати запит з більше деталями"
    
    return {
        "name": "ask_diary",
        "description": TOOL_DESCRIPTION,
        "parameters": TOOL_PARAMETERS,
        "handler": handler
    }