"""ask_diary tool - similar to C++ ask_diary tool for Noxi."""

from typing import Dict, Callable, Optional
import logging

logger = logging.getLogger("Noxi")

TOOL_DESCRIPTION = """Consult with Noxi's main knowledge database (diary). Use this to retrieve additional 
pages from diary. USE THIS PROACTIVELY — especially when someone shares personal news, asks about past 
events, or mentions people/activities you might know about.

Examples of when to call:
- User says "I wrote a song today" → query: "[sender name] wrote a song. What do I know about them and songs?"
- User asks "what songs am I writing?" → query: "What songs does [sender name] write?"
- User says "I'm going to the gym" → query: "Does [sender name] go to the gym? Related habits or routines?"
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


class AskDiaryTool:
    """Ask diary tool implementation."""

    def __init__(
        self,
        get_memory: Callable,
        get_user_id: Callable[[], str]
    ):
        self.get_memory = get_memory
        self.get_user_id = get_user_id

    async def execute(self, query: str, context: str = "") -> str:
        """Execute ask_diary tool."""
        if len(query) < 10:
            return """error: too short query! please provide more context:
- sender's name
- previous messages
- search cues
- source event
- everything else to populate query"""

        try:
            memory = self.get_memory()
            user_id = self.get_user_id()

            if context:
                full_query = f"{context}\n\n{query}"
            else:
                full_query = query

            result = await memory.ask_diary(user_id, full_query, context)
            return result
        except Exception as e:
            logger.warning(f"[ASK_DIARY] Error: {e}")
            return f"Error accessing diary: {e}"


def create_ask_diary_tool(
    get_memory: Callable,
    get_user_id: Callable[[], str]
) -> Dict:
    """
    Create ask_diary tool definition similar to C++ ask_diary function.
    
    Returns dict with tool definition for model integration.
    """

    tool_handler = AskDiaryTool(get_memory, get_user_id)

    async def handler(query: str) -> str:
        return await tool_handler.execute(query)

    return {
        "name": "ask_diary",
        "description": TOOL_DESCRIPTION,
        "parameters": TOOL_PARAMETERS,
        "handler": handler
    }


TOOL_DEFINITION = {
    "name": "ask_diary",
    "description": TOOL_DESCRIPTION,
    "parameters": TOOL_PARAMETERS
}