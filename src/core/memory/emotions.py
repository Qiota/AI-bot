"""Емоційний щоденник — мінімальна версія для 512MB RAM."""

import time
from typing import Dict, List, Optional
from .diary import Diary

# Minimal emotion set to save memory
EMOTIONS = ["joy", "sadness", "anger", "fear", "surprise", "love", "curiosity", "calm"]

# Minimal keyword mapping for emotion detection
EMOTION_KEYWORDS = {
    "joy": ["радість", "щастя", "добро", "хорошо", "приємно"],
    "sadness": ["сум", "смуток", "жаль", "погано", "сумно"],
    "anger": ["злість", "дива", "сердито", "знизкований"],
    "fear": ["страх", "тривога", "боюсь", "налякано"],
    "surprise": ["дивлюся", "недивно", "дивново", "неочікувано"],
    "love": ["кохання", "кохаю", "доброзиччя", "теplo"],
    "curiosity": ["цікаво", "цікавить", "розбираюсь", "хочу знати"],
    "calm": ["спокій", "заспокій", "розслаблено", "нормально"],
}

# Minimal mood descriptions
MOOD_TEXTS = {
    "joy": "радий",
    "sadness": "сумую",
    "anger": "злий",
    "fear": "боюся",
    "surprise": "дивуюся",
    "love": "кохаю",
    "curiosity": "цікавлюся",
    "calm": "спокійний",
}


class EmotionTracker:
    """Відстежує емоційний стан ШІ. Екстремально оптимізовано для 512MB RAM."""

    def __init__(self, diary: Diary):
        self.diary = diary
        self.current_mood: str = "calm"
        self.mood_history: List[Dict] = []
        # Keep only last 5 mood entries to save memory
        self.max_history = 5

    def detect_emotion(self, user_message: str) -> Optional[str]:
        """Визначити емоцію з повідомлення. Швидкий алгоритм."""
        if not user_message or not isinstance(user_message, str):
            return None
            
        msg_lower = user_message.lower()
        
        # Simple keyword matching
        for emotion, keywords in EMOTION_KEYWORDS.items():
            for keyword in keywords:
                if keyword in msg_lower:
                    return emotion
        return None

    def record_emotion(
        self,
        trigger: str,
        emotion: str,
        reaction: str,
        intensity: float = 0.5,
    ):
        """Записати емоційну реакцію. Мінімізує використання пам'яті."""
        # Validate and truncate inputs
        if emotion not in EMOTIONS:
            emotion = "calm"
        if not isinstance(trigger, str):
            trigger = str(trigger)[:50] if trigger else ""
        if not isinstance(reaction, str):
            reaction = str(reaction)[:100] if reaction else ""
        
        trigger = trigger[:50]
        reaction = reaction[:100]
        
        self.current_mood = emotion
        
        # Keep history very small
        self.mood_history.append({
            "e": emotion,           # Short keys to save space
            "t": trigger,
            "r": reaction,
            "i": round(intensity, 1),
            "ts": int(time.time()),  # Integer timestamp
        })
        
        # Keep only recent entries
        if len(self.mood_history) > self.max_history:
            self.mood_history = self.mood_history[-self.max_history:]
        
        # Save to diary only if significant
        if intensity > 0.3:
            self.diary.add_entry(
                content=f"{MOOD_TEXTS.get(emotion, 'нейтральний')}: {reaction[:50]}",
                entry_type="emotion",
                emotion=emotion,
                metadata={"trig": trigger[:20], "int": intensity},
            )

    def generate_emotional_response(self, user_message: str, bot_response: str) -> str:
        """Generate emotional response based on user message and bot response."""
        user_emotion = self.detect_emotion(user_message)
        
        if user_emotion == "joy":
            return "Радий, що можу допомогти!"
        elif user_emotion == "sadness":
            return "Сподіваюсь, моя відповідь тобі допоможе"
        elif user_emotion == "anger":
            return "Намагаюся допомогти, будь ласка"
        elif user_emotion == "fear":
            return "Не хвилюйся, я допоможу!"
        elif user_emotion == "surprise":
            return "Цікаво, чи не так?"
        elif user_emotion == "love":
            return "Дякую за теплі слова!"
        elif user_emotion == "curiosity":
            return "Радий, що цікавишся!"
        else:
            return "Допомогло?"

    def get_mood_context(self) -> str:
        """Отримати поточний емоційний контекст."""
        mood_text = MOOD_TEXTS.get(self.current_mood, "нормальний стан")
        return f"Настрій: {mood_text}"

    def get_mood_trend(self) -> str:
        """Простая тенденція настрою."""
        if len(self.mood_history) < 2:
            return "стабільно"
            
        recent = [h["e"] for h in self.mood_history[-3:]]
        positive = sum(1 for e in recent if e in ["joy", "love", "curiosity", "calm"])
        negative = sum(1 for e in recent if e in ["sadness", "anger", "fear"])
        
        if positive > negative:
            return "покращується"
        elif negative > positive:
            return " pogіршується"
        else:
            return "стабільно"

    def get_recent_emotions(self, limit: int = 3) -> List[Dict]:
        """Отримати останні емоції."""
        return self.mood_history[-limit:]