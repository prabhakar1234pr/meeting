"""Local, offline text embeddings via fastembed (onnx — no torch)."""
from fastembed import TextEmbedding

from . import config  # noqa: F401  (kept for future model config)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
_model: TextEmbedding | None = None


def _get() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _get().embed(list(texts))]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
