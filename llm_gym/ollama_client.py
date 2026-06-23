"""Thin synchronous Ollama client.

Only the calls the gym needs: list installed models, pull a base model, create a
model from a Modelfile (used when assigning a fused adapter), and a one-shot
generate for smoke tests. Everything degrades gracefully when Ollama is offline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass
class OllamaModel:
    name: str
    size_gb: float
    family: str


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str, timeout: float = 30.0) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(base_url=self.host, timeout=timeout or self.timeout)

    def is_up(self) -> bool:
        try:
            with self._client(timeout=3.0) as c:
                return c.get("/api/version").status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[OllamaModel]:
        try:
            with self._client() as c:
                data = c.get("/api/tags").json()
        except Exception as exc:  # noqa: BLE001
            raise OllamaError(f"Cannot reach Ollama at {self.host}: {exc}") from exc
        out: list[OllamaModel] = []
        for m in data.get("models", []):
            details = m.get("details", {}) or {}
            out.append(OllamaModel(
                name=m.get("name", "?"),
                size_gb=round(m.get("size", 0) / 1e9, 2),
                family=details.get("family", ""),
            ))
        return out

    def pull(self, model: str) -> None:
        """Blocking pull. Streams progress; raises on an HTTP error or on an error
        reported mid-stream (Ollama returns 200 then streams {"error": ...})."""
        with self._client(timeout=None) as c:
            with c.stream("POST", "/api/pull", json={"model": model}) as r:
                if r.status_code != 200:
                    raise OllamaError(f"Pull of {model} failed (HTTP {r.status_code}).")
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and obj.get("error"):
                        raise OllamaError(f"Pull of {model} failed: {obj['error']}")

    def create(self, name: str, modelfile: str) -> None:
        with self._client(timeout=None) as c:
            r = c.post("/api/create", json={"model": name, "modelfile": modelfile})
            if r.status_code != 200:
                raise OllamaError(f"Create of {name} failed: {r.text[:200]}")

    def generate(self, model: str, prompt: str, num_predict: int = 128,
                 system: str = "") -> str:
        body: dict = {
            "model": model, "prompt": prompt, "stream": False,
            "options": {"num_predict": num_predict},
        }
        if system:
            body["system"] = system
        with self._client() as c:
            r = c.post("/api/generate", json=body)
            if r.status_code != 200:
                raise OllamaError(f"Generate failed: {r.text[:200]}")
            return r.json().get("response", "")
