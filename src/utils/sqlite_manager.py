import aiosqlite
import json
import asyncio
import logging
from typing import Dict, Any
from ..systemLog import logger

logger = logging.getLogger(__name__)

class SQLiteManager:
    """SQLite fallback for Firebase guild configs."""
    
    def __init__(self, db_path: str = 'guilds.db'):
        self.db_path = db_path
        self.db = None

    async def initialize(self) -> 'SQLiteManager':
        """Initialize DB connection and create table."""
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id TEXT PRIMARY KEY,
                config JSON NOT NULL DEFAULT '{}'
            )
        ''')
        await self.db.commit()
        logger.info(f"SQLiteManager инициализирован: {self.db_path}")
        return self

    async def load_guild_config(self, guild_id: str) -> Dict[str, Any]:
        """Load guild config as dict."""
        if not self.db:
            raise Exception("SQLiteManager not initialized")
        async with self.db.execute('SELECT config FROM guilds WHERE guild_id = ?', (guild_id,)) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else {}

    async def update_guild_fields(self, guild_id: str, updates: Dict[str, Any]) -> None:
        """Update guild fields, merge with existing."""
        if not self.db:
            raise Exception("SQLiteManager not initialized")
        
        # Load existing
        current = await self.load_guild_config(guild_id)
        current.update(updates)
        
        # Upsert
        await self.db.execute(
            'INSERT OR REPLACE INTO guilds (guild_id, config) VALUES (?, ?)',
            (guild_id, json.dumps(current))
        )
        await self.db.commit()
        logger.debug(f"SQLite обновлена гильдия {guild_id}")

    async def close(self):
        """Close DB."""
        if self.db:
            await self.db.close()
