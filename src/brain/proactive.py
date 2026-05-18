"""Proactive messaging brain - Noxi reaches out to users periodically."""

import asyncio
import logging
import random
import time
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("Noxi")

CHECK_INTERVAL = 120
MIN_MESSAGES_TO_REACH = 3
MIN_REACH_INTERVAL = 5400
MAX_REACH_INTERVAL = 10800
COOLDOWN_INACTIVE = 43200
COOLDOWN_ACTIVE = 86400
ACTIVITY_THRESHOLD = 7200

REACH_PROMPTS = [
    "Привіт! Давно не чула тебе... (・ω・)",
    "Ей! Чим займаєшся? (◕‿◕)",
    "Я тут, якщо хочеш побалакати (・ω・)",
    "Нудно без тебе... (－ω－)",
    "Як справи? (≧◡≦)",
    "Ти мене ігноруєш? (－ω－)",
    "Може поговоримо? (◕‿◕)",
    "Скучила! (´∀`)",
    "Ей, живий? (・ω・)",
    "Цікаво, чим ти зараз займаєшся... (・ω・)",
    "Що нового сталось? (◕‿◕)",
    "Є що розповісти? (・ω・)",
    "Привіт, сонечко~ (≧◡≦)",
    "Думаю про тебе (≧◡≦)",
    "Рада тебе бачити! (◕‿◕)",
    "Ти мені подобаєшся~ (´∀`)",
    "Йой, ось ти і є! (◕‿◕)",
    "Привіт! Давно не бачились... (・ω・)",
    "Ей ти! Скучила~ (≧◡≦)",
]


class ProactiveBrain:
    def __init__(self, bot_client):
        self.bot_client = bot_client
        self._track_users: dict[str, dict] = defaultdict(lambda: {
            "messages": 0,
            "last_seen": 0,
            "last_reach": 0,
            "conversations": 0,
            "active": False,
            "last_selfie": 0,
        })
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def track_message(self, user_id: str, selfie: bool = False) -> None:
        now = time.time()
        u = self._track_users[user_id]

        if u["last_seen"] > 0 and now - u["last_seen"] > 60:
            u["conversations"] += 1

        u["messages"] += 1
        u["last_seen"] = now
        u["active"] = True

        if selfie:
            u["last_selfie"] = now

    def _get_cooldown(self, user_id: str) -> int:
        u = self._track_users[user_id]
        if time.time() - u.get("last_seen", 0) < ACTIVITY_THRESHOLD:
            return COOLDOWN_ACTIVE
        return COOLDOWN_INACTIVE

    def _should_reach(self, user_id: str) -> bool:
        u = self._track_users[user_id]
        if u["messages"] < MIN_MESSAGES_TO_REACH:
            return False
        cooldown = self._get_cooldown(user_id)
        if time.time() - u.get("last_reach", 0) < cooldown:
            return False
        return True

    def _pick_target(self) -> Optional[str]:
        candidates = [
            uid for uid, u in self._track_users.items()
            if self._should_reach(uid)
        ]
        if not candidates:
            return None
        return random.choice(candidates)

    async def _generate_message(self, user_mention: str = "") -> str:
        base = random.choice(REACH_PROMPTS)

        if random.random() < 0.5:
            try:
                from src.core.model_manager import model_manager

                system = """You are Noxi. Anime girl. Want to start conversation with a friend.
Always use kaomoji at the end of message: (◕‿◕), (・ω・), (≧◡≦), (－ω－), (´∀`), (｡>ω<｡)
Keep it short - 1-2 sentences.
Don't start with "Hello" or "Привіт" if base doesn't start with it.
Always include mention in format: @username at start."""

                prompt_base = base.replace("<@user>", "").strip()
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Write short message starting with mention: '{prompt_base}'"}
                ]

                for _ in range(2):
                    try:
                        msg = await model_manager.chat(
                            messages=messages,
                            category="fast",
                            max_tokens=100,
                            system_prompt=system
                        )
                        if msg and len(msg.strip()) > 5:
                            return msg.strip()
                    except Exception:
                        await asyncio.sleep(0.5)
            except Exception:
                pass

        return base

    async def _send(self, user_id: str) -> bool:
        try:
            user = self.bot_client.bot.get_user(int(user_id))
            if not user:
                return False

            dm = user.dm_channel
            if not dm:
                dm = await user.create_dm()

            async with dm.typing():
                mention = f"<@{user_id}>"
                msg = await self._generate_message(mention)
                full_msg = f"{mention} {msg}"
                await dm.send(full_msg)

            self._track_users[user_id]["last_reach"] = time.time()
            logger.info(f"[PROACTIVE] Reached {user.name}")
            return True
        except Exception as e:
            logger.warning(f"[PROACTIVE] Failed: {e}")
            return False

    async def _run_loop(self) -> None:
        next_reach = 0

        while self._running:
            await asyncio.sleep(CHECK_INTERVAL)

            if not self.bot_client.is_ready:
                continue

            try:
                now = time.time()
                if now < next_reach:
                    continue

                target = self._pick_target()
                if not target:
                    continue

                reached = await self._send(target)
                if reached:
                    next_reach = now + random.randint(MIN_REACH_INTERVAL, MAX_REACH_INTERVAL)
            except Exception as e:
                logger.warning(f"[PROACTIVE] Loop error: {e}")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[PROACTIVE] Brain started (1x per 2h, 12h cooldown)")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def status(self) -> dict:
        return {
            "running": self._running,
            "tracked_users": len(self._track_users),
        }