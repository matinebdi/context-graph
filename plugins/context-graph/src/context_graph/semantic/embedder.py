import math
import sys
from pathlib import Path

from context_graph.config import Environment, LocalCredentials


class Embedder:
    """Local text embeddings for semantic search, computed with a multilingual ONNX model.

    The model is loaded lazily and cached under ``~/.context-graph/models``. If it can't be loaded
    (no network on first use, unsupported platform, ``CONTEXT_GRAPH_EMBEDDINGS=off``), the
    embedder reports itself unavailable and callers fall back to keyword search.

    Attributes:
        model_name: Name of the fastembed model.
        cache_dir: Folder where the model files are stored.
    """

    MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    DIMENSIONS = 384
    # Body text beyond this length adds little to the meaning and slows embedding down.
    MAX_BODY_CHARS = 1500

    def __init__(self, model_name: str = MODEL, cache_dir: Path | None = None):
        self.model_name = model_name
        self.cache_dir = cache_dir or LocalCredentials().home / "models"
        self._model = None
        self._failed = Environment.get("CONTEXT_GRAPH_EMBEDDINGS").lower() in {"off", "0", "false"}

    @property
    def available(self) -> bool:
        """Whether embeddings can be computed, loading the model on first access."""
        if self._model is None and not self._failed:
            try:
                from fastembed import TextEmbedding

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir))
            except Exception as exc:  # any failure only disables semantic search
                print(f"[context-graph] semantic search disabled: {exc}", file=sys.stderr)
                self._failed = True
        return self._model is not None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeds texts into unit-length vectors.

        Args:
            texts: Texts to embed.

        Returns:
            One vector per text, or an empty list when the embedder is unavailable.
        """
        if not texts or not self.available:
            return []
        vectors = []
        for vector in self._model.embed(texts):
            norm = math.sqrt(sum(float(x) * float(x) for x in vector)) or 1.0
            vectors.append([float(x) / norm for x in vector])
        return vectors

    def embed_one(self, text: str) -> list[float] | None:
        """Embeds a single text. Returns None when the embedder is unavailable."""
        vectors = self.embed([text])
        return vectors[0] if vectors else None

    @classmethod
    def text_for(cls, title: str, summary: str, tags: list[str], body: str) -> str:
        """Builds the text that represents a node in the vector index.

        Args:
            title: Node title.
            summary: One-line summary.
            tags: Tags.
            body: Markdown body; truncated to ``MAX_BODY_CHARS``.

        Returns:
            The text to embed.
        """
        parts = [title, summary, " ".join(tags), body[: cls.MAX_BODY_CHARS]]
        return "\n".join(part for part in parts if part)
