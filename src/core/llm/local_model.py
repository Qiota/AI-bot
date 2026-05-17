"""Локальна LLM модель — запуск та використання."""

import asyncio
from typing import Dict, List, Optional, AsyncGenerator
from pathlib import Path

from ..systemLog import logger

class LocalLLM:
    """Управління локальною LLM моделлю.

    Підтримує:
    1. Ollama (рекомендовано) — через HTTP API
    2. Transformers — пряме завантаження моделей
    3. llama-cpp-python — GGUF моделі

    Приклад використання:
        llm = LocalLLM(provider="ollama", model="mistral")
        response = await llm.chat([{"role": "user", "content": "Привіт!"}])
    """

    def __init__(
        self,
        provider: str = "ollama",
        model: str = "mistral",
        base_url: str = "http://localhost:11434",
        **kwargs,
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.kwargs = kwargs
        self._client = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Ініціалізувати провайдер."""
        try:
            if self.provider == "ollama":
                return await self._init_ollama()
            elif self.provider == "transformers":
                return await self._init_transformers()
            elif self.provider == "llama_cpp":
                return await self._init_llama_cpp()
            else:
                logger.error(f"Невідомий провайдер: {self.provider}")
                return False
        except Exception as e:
            logger.error(f"Помилка ініціалізації LLM: {e}")
            return False

    async def _init_ollama(self) -> bool:
        """Перевірити доступність Ollama."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/tags", timeout=5) as resp:
                    if resp.status == 200:
                        self._initialized = True
                        logger.info(f"Ollama підключено: {self.base_url}")
                        return True
                    else:
                        logger.warning(f"Ollama повернув статус {resp.status}")
                        return False
        except Exception as e:
            logger.warning(f"Ollama недоступний: {e}")
            return False

    async def _init_transformers(self) -> bool:
        """Ініціалізує transformers."""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
            import torch

            logger.info(f"Завантаження моделі {self.model}...")
            self.tokenizer = AutoTokenizer.from_pretrained(self.model)
            self.model_obj = AutoModelForCausalLM.from_pretrained(
                self.model,
                torch_dtype=torch.float16,
                device_map="auto",
            )
            self.pipe = pipeline(
                "text-generation",
                model=self.model_obj,
                tokenizer=self.tokenizer,
                **self.kwargs,
            )
            self._initialized = True
            logger.info(f"Модель {self.model} завантажено")
            return True
        except ImportError:
            logger.error("transformers не встановлено: pip install transformers torch")
            return False
        except Exception as e:
            logger.error(f"Помилка завантаження моделі: {e}")
            return False

    async def _init_llama_cpp(self) -> bool:
        """Ініціалізує llama-cpp-python."""
        try:
            from llama_cpp import Llama

            model_path = self.kwargs.get("model_path")
            if not model_path or not Path(model_path).exists():
                logger.error(f"Файл моделі не знайдено: {model_path}")
                return False

            self.model_obj = Llama(
                model_path=model_path,
                n_ctx=self.kwargs.get("n_ctx", 4096),
                n_gpu_layers=self.kwargs.get("n_gpu_layers", -1),
                verbose=False,
            )
            self._initialized = True
            logger.info(f"llama-cpp завантажено: {model_path}")
            return True
        except ImportError:
            logger.error("llama-cpp-python не встановлено: pip install llama-cpp-python")
            return False
        except Exception as e:
            logger.error(f"Помилка завантаження llama-cpp: {e}")
            return False

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Optional[str]:
        """Зробити запит до локальної моделі."""
        if not self._initialized:
            if not await self.initialize():
                return None

        try:
            if self.provider == "ollama":
                return await self._chat_ollama(messages, temperature, max_tokens)
            elif self.provider == "transformers":
                return await self._chat_transformers(messages, temperature, max_tokens)
            elif self.provider == "llama_cpp":
                return await self._chat_llama_cpp(messages, temperature, max_tokens)
        except Exception as e:
            logger.error(f"Помилка чату LLM: {e}")
            return None

    async def _chat_ollama(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        import aiohttp
        import json

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("message", {}).get("content", "").strip()
                else:
                    logger.error(f"Ollama помилка: {resp.status}")
                    return None

    async def _chat_transformers(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.pipe(
                text,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                return_full_text=False,
            )
        )

        if result:
            return result[0]["generated_text"].strip()
        return None

    async def _chat_llama_cpp(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        prompt = "\n".join([
            f"{m['role']}: {m['content']}" for m in messages
        ]) + "\nassistant:"

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.model_obj(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=["user:", "Human:", "\n\n"],
                echo=False,
            )
        )

        if result and "choices" in result:
            return result["choices"][0]["text"].strip()
        return None

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """Stream відповідь від моделі."""
        if not self._initialized:
            if not await self.initialize():
                return

        if self.provider == "ollama":
            async for chunk in self._stream_ollama(messages, temperature, max_tokens):
                yield chunk
        else:
            response = await self.chat(messages, temperature, max_tokens)
            if response:
                yield response

    async def _stream_ollama(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[str, None]:
        import aiohttp
        import json

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status == 200:
                    async for line in resp.content:
                        if line:
                            try:
                                data = json.loads(line)
                                content = data.get("message", {}).get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
                else:
                    logger.error(f"Ollama stream помилка: {resp.status}")

    def get_model_info(self) -> Dict:
        """Інформація про модель."""
        return {
            "provider": self.provider,
            "model": self.model,
            "initialized": self._initialized,
            "base_url": self.base_url if self.provider == "ollama" else None,
        }


async def create_local_llm(
    provider: str = "ollama",
    model: str = "mistral",
    **kwargs,
) -> Optional[LocalLLM]:
    """Фабрика для створення локальної LLM."""
    llm = LocalLLM(provider=provider, model=model, **kwargs)
    if await llm.initialize():
        return llm
    return None
