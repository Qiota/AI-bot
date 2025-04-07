from time import time
from typing import Dict

class CooldownManager:
    def __init__(self):
        self.cooldowns: Dict[str, Dict[int, float]] = {}

    def check_cooldown(self, command: str, user_id: int, cooldown_duration: float) -> float:
        """Check if the user is on cooldown for the given command. Returns remaining cooldown time (0 if none)."""
        if command not in self.cooldowns:
            self.cooldowns[command] = {}

        last_used = self.cooldowns[command].get(user_id, 0)
        elapsed = time() - last_used
        remaining = cooldown_duration - elapsed

        if remaining <= 0:
            self.cooldowns[command][user_id] = time()
            return 0
        return remaining

    def update_cooldown(self, command: str, user_id: int):
        """Update the cooldown timestamp for the user and command."""
        if command not in self.cooldowns:
            self.cooldowns[command] = {}
        self.cooldowns[command][user_id] = time()