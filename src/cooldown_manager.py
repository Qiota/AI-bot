from time import time
from typing import Dict

class CooldownManager:
    def __init__(self):
        self.cooldowns: Dict[str, Dict[int, float]] = {}

    def check_cooldown(self, command: str, user_id: int, cooldown_duration: float) -> float:
        """Проверяет кулдаун для команды. Возвращает остаток времени (0, если нет)."""
        last_used = self.cooldowns.setdefault(command, {}).get(user_id, 0)
        remaining = cooldown_duration - (time() - last_used)

        if remaining <= 0:
            self.cooldowns[command][user_id] = time()
            return 0
        return remaining

    def update_cooldown(self, command: str, user_id: int):
        """Обновляет время кулдауна."""
        self.cooldowns.setdefault(command, {})[user_id] = time()