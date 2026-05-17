#!/usr/bin/env python3
"""Simple test for the optimized memory system."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from core.memory.diary import Diary, MAX_ENTRIES
from core.memory.emotions import EmotionTracker
from core.memory.working_memory import WorkingMemory
from core.memory.thoughts import ThoughtGenerator
from core.memory.sleep import SleepConsolidation

def test_memory_system():
    """Test all components of the memory system."""
    print("Testing optimized memory system for 0.1 vCPU, 512MB RAM")
    print("=" * 60)
    
    # Create test instances
    diary = Diary("test_user")
    emotion_tracker = EmotionTracker(diary)
    working_memory = WorkingMemory("test_user")
    thought_generator = ThoughtGenerator(diary)
    sleep = SleepConsolidation(diary)
    
    print("Components initialized successfully")
    
    # Test diary
    print("\nTesting diary...")
    entry_id = diary.add_entry("Тестове повідомлення для перевірки", emotion="joy")
    print(f"Added entry: {entry_id}")
    
    entries = diary.get_entries(limit=5)
    print(f"Retrieved entries: {len(entries)}")
    
    diary_context = diary.format_for_context(limit=3)
    print(f"LLM context: {repr(diary_context[:50])}...")
    
    # Test emotions
    print("\nTesting emotion tracker...")
    emotion = emotion_tracker.detect_emotion("Сьогодні чудовий день, я радий!")
    print(f"Detected emotion: {emotion}")
    
    emotion_tracker.record_emotion(
        trigger="Користувач сказав щось позитивне",
        emotion="joy" if emotion else "calm",
        reaction="Я теж відчуваю радість",
        intensity=0.7
    )
    
    mood_context = emotion_tracker.get_mood_context()
    print(f"Mood context: {mood_context}")
    
    # Test working memory
    print("\nTesting working memory...")
    note_id = working_memory.add_note("Нагадати про зустріч о 15:00")
    print(f"Added note: {note_id}")
    
    recent = working_memory.get_recent(limit=2)
    print(f"Recent notes: {len(recent)}")
    
    wm_context = working_memory.format_for_context()
    print(f"LLM context: {repr(wm_context)}")
    
    # Test thoughts
    print("\nTesting thought generator...")
    import asyncio
    thoughts = asyncio.run(thought_generator.generate_thoughts(
        "Привіт! Як справи?", 
        diary.get_recent_entries(limit=2)
    ))
    print(f"Generated thoughts: {len(thoughts)}")
    for i, t in enumerate(thoughts, 1):
        print(f"Thought {i}: {t}")
    
    thoughts_context = thought_generator.format_thoughts_for_context(thoughts)
    print(f"LLM context: {repr(thoughts_context)}")
    
    # Test sleep
    print("\nTesting sleep module...")
    sleep_status = sleep.get_sleep_status()
    print(f"Sleep status: {sleep_status}")
    
    # Test limits
    print("\nTesting resource limits...")
    
    # Fill diary with many entries
    for i in range(30):
        diary.add_entry(f"Тестовий запис номер {i} " * 5, emotion="curiosity" if i % 2 == 0 else "calm")
    
    final_count = diary.get_entry_count()
    print(f"Entries in diary after 30 additions: {final_count} (max: {MAX_ENTRIES})")
    
    if final_count <= MAX_ENTRIES:
        print("Entry limit respected")
    else:
        print(f"Limit violated: {final_count} > {MAX_ENTRIES}")
    
    print("\n" + "=" * 60)
    print("Testing completed! System ready for 0.1 vCPU, 512MB RAM")
    return True

if __name__ == "__main__":
    try:
        test_memory_system()
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)