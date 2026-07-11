"""Entry point: `python -m llm_gym` or the `llm-gym` console script."""
from __future__ import annotations

import uvicorn

from .config import load_settings


def main() -> None:
    settings = load_settings()
    print(f"LLM Gym -> http://{settings.host}:{settings.port}")
    print(f"Ollama   -> {settings.ollama_host}")
    print(f"Backend  -> {settings.backend} (auto-detects MLX/PEFT, simulate fallback)")
    uvicorn.run("llm_gym.app:app", host=settings.host, port=settings.port,
                reload=False)


if __name__ == "__main__":
    main()
