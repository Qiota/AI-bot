#!/usr/bin/env python3
"""Скрипт для запуску локальної LLM моделі.

Підтримує 3 способи:
1. Ollama (рекомендовано) — найпростіший
2. Transformers + HuggingFace
3. llama-cpp-python (GGUF файли)

Приклади використання:
    python run_local_llm.py --provider ollama --model mistral
    python run_local_llm.py --provider transformers --model microsoft/phi-2
    python run_local_llm.py --provider llama_cpp --model_path model.gguf
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.core.llm.local_model import LocalLLM, create_local_llm


async def interactive_chat(llm: LocalLLM):
    """Інтерактивний чат з локальною моделлю."""
    print(f"\n🤖 Модель: {llm.model} ({llm.provider})")
    print("Введіть повідомлення (або 'exit' для виходу, '/stream' для потокового режиму)\n")

    messages = [
        {"role": "system", "content": "Ти корисний асистент. Відповідай коротко."}
    ]

    use_stream = False

    while True:
        try:
            user_input = input("Ви: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 До побачення!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "вихід"):
            print("👋 До побачення!")
            break

        if user_input == "/stream":
            use_stream = not use_stream
            print(f"Потоковий режим: {'увімкнено' if use_stream else 'вимкнено'}")
            continue

        if user_input == "/reset":
            messages = messages[:1]
            print("🧹 Контекст очищено")
            continue

        messages.append({"role": "user", "content": user_input})

        if use_stream:
            print("Асистент: ", end="", flush=True)
            full_response = ""
            async for chunk in llm.chat_stream(messages):
                print(chunk, end="", flush=True)
                full_response += chunk
            print()
        else:
            print("🤔 Думаю...", end="", flush=True)
            response = await llm.chat(messages)
            print("\r" + " " * 20 + "\r", end="")
            if response:
                print(f"Асистент: {response}")
                messages.append({"role": "assistant", "content": response})
            else:
                print("Помилка: не вдалося отримати відповідь")


async def test_single_prompt(llm: LocalLLM, prompt: str):
    """Тестовий промпт."""
    print(f"\n🤖 Модель: {llm.model} ({llm.provider})")
    print(f"Промпт: {prompt}\n")

    messages = [{"role": "user", "content": prompt}]
    response = await llm.chat(messages)

    if response:
        print(f"Відповідь:\n{response}")
    else:
        print("Помилка: не вдалося отримати відповідь")


async def main():
    parser = argparse.ArgumentParser(description="Запуск локальної LLM моделі")
    parser.add_argument(
        "--provider",
        choices=["ollama", "transformers", "llama_cpp"],
        default="ollama",
        help="Провайдер LLM",
    )
    parser.add_argument(
        "--model",
        default="mistral",
        help="Назва моделі (для ollama/transformers)",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="Шлях до GGUF файлу (для llama_cpp)",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434",
        help="URL Ollama API",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Одиночний промпт (без інтерактивного режиму)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Температура генерації",
    )

    args = parser.parse_args()

    kwargs = {
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "temperature": args.temperature,
    }

    if args.model_path:
        kwargs["model_path"] = args.model_path

    print(f"🔧 Ініціалізація: {args.provider} / {args.model}")

    llm = await create_local_llm(**kwargs)
    if not llm:
        print("❌ Не вдалося ініціалізувати модель")
        print("\nПоради:")
        if args.provider == "ollama":
            print("1. Встановіть Ollama: https://ollama.ai")
            print("2. Запустіть: ollama serve")
            print("3. Завантажте модель: ollama pull mistral")
        elif args.provider == "transformers":
            print("1. pip install transformers torch")
            print("2. Переконайтесь, що достатньо RAM/VRAM")
        elif args.provider == "llama_cpp":
            print("1. pip install llama-cpp-python")
            print("2. Завантажте GGUF модель з HuggingFace")
            print("3. Вкажіть --model-path /шлях/до/model.gguf")
        sys.exit(1)

    info = llm.get_model_info()
    print(f"✅ Модель готова: {info}")

    if args.prompt:
        await test_single_prompt(llm, args.prompt)
    else:
        await interactive_chat(llm)


if __name__ == "__main__":
    asyncio.run(main())
