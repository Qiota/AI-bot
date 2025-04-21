from typing import Tuple, Optional, Union
import discord
from ..utils.firebase.firebase_manager import FirebaseManager
from ..systemLog import logger

class Checker:
    """Класс для проверки ограничений пользователей."""
    _instance = None
    _restriction_cache = {}
    _firebase_manager = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def initialize(self):
        """Инициализация FirebaseManager."""
        if self._firebase_manager is None:
            self._firebase_manager = await FirebaseManager.initialize()
            logger.debug("FirebaseManager инициализирован в Checker")
        return self._firebase_manager

    async def check_user_restriction(self, obj: Union[discord.Interaction, discord.Message]) -> Tuple[bool, Optional[str]]:
        """Проверка, ограничен ли пользователь."""
        guild_id = str(obj.guild.id) if obj.guild else None
        user_id = None
        try:
            # Определение user_id в зависимости от типа объекта
            if isinstance(obj, discord.Interaction):
                user_id = str(obj.user.id)
            elif isinstance(obj, discord.Message):
                user_id = str(obj.author.id)
            else:
                raise ValueError(f"Неподдерживаемый тип объекта: {type(obj)}")

            # Проверка в Firebase
            if not guild_id:
                logger.debug(f"Нет guild_id для {user_id}, доступ разрешен (DM)")
                return True, None

            # Инициализация FirebaseManager
            firebase_manager = await self.initialize()
            if not guild_id:
                raise ValueError("guild_id не определен для проверки конфигурации гильдии")
            
            # Очистка кэша для пользователя перед проверкой
            self.clear_cache(user_id)
            logger.debug(f"Кэш очищен для пользователя {user_id}")

            # Загрузка конфигурации
            logger.debug(f"Вызов load_guild_config для guild_id={guild_id}")
            config = await firebase_manager.load_guild_config(guild_id)
            restricted_users = config.get("restricted_users", []) if config else []
            logger.debug(f"restricted_users для guild_id={guild_id}: {restricted_users}")
            
            is_restricted = user_id in restricted_users
            cache_key = f"{guild_id}:{user_id}"
            self._restriction_cache[cache_key] = not is_restricted
            logger.debug(f"Обновлен кэш для {cache_key}: {not is_restricted}")

            return not is_restricted, None if not is_restricted else "Ваш доступ к боту ограничен."

        except Exception as e:
            logger.error(f"Ошибка проверки ограничений для {user_id or 'неизвестного пользователя'} в {guild_id or 'DM'}: {e}", exc_info=True)
            return False, f"Ошибка проверки: {e}"

    def clear_cache(self, user_id: Optional[str] = None) -> None:
        """Очистка кэша ограничений."""
        if user_id:
            keys_to_remove = [key for key in self._restriction_cache if key.endswith(f":{user_id}")]
            for key in keys_to_remove:
                del self._restriction_cache[key]
                logger.debug(f"Очищен кэш для пользователя {user_id}: {key}")
        else:
            self._restriction_cache.clear()
            logger.debug("Кэш ограничений полностью очищен")

checker = Checker()