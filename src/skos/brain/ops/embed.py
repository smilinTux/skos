"""skos.brain.ops.embed — the mxbai embedder client (SB1.1).

The ONE isolated I/O step of the projection: chunk text -> vector(1024). The
HTTP call lives behind the ``Embedder`` protocol so the pure parse/chunk/plan
code never imports a network client and unit tests stub it trivially.

``MxbaiEmbedder`` targets the LIVE topology (mxbai-embed-large at
http://localhost:11434/api/embed, 1024-dim, ctx 512; the per-node failover
topology, memory skmemory-embed-failover-topology-2026-07-25). The transport is
injectable so even MxbaiEmbedder is testable without a network.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from skos.brain.ops.models import OpsChunk

# Live embedding contract (skmemory CLAUDE.md): mxbai-embed-large, 1024-dim.
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "mxbai-embed-large"
DEFAULT_DIM = 1024
DEFAULT_TIMEOUT = 30.0


class EmbedError(RuntimeError):
    """Raised on an embedding transport failure or a dimension mismatch."""


@runtime_checkable
class Embedder(Protocol):
    """Turn a batch of texts into a batch of 1024-dim vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def embed_chunks(chunks: list[OpsChunk], embedder: Embedder) -> list[OpsChunk]:
    """Return copies of *chunks* with ``embedding`` filled from *embedder*.

    One batched call for the whole list (empty list = no call). Pure aside from
    the injected embedder; the input OpsChunks are frozen, so new ones are built.
    """
    if not chunks:
        return []
    vectors = embedder.embed([c.content for c in chunks])
    if len(vectors) != len(chunks):
        raise EmbedError(f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks")
    return [
        OpsChunk(node_id=c.node_id, ord=c.ord, content=c.content, embedding=list(vec))
        for c, vec in zip(chunks, vectors)
    ]


# Transport signature: (url, json_body, timeout) -> response-with .json()/.raise_for_status()
Transport = Callable[[str, dict[str, Any], float], Any]


class MxbaiEmbedder:
    """mxbai-embed-large over Ollama's ``/api/embed`` (1024-dim).

    The transport defaults to ``requests.post`` (imported lazily so the module,
    and the whole pure pipeline, never hard-depends on requests). Inject a
    transport in tests to exercise request shape and response parsing offline.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        *,
        dim: int = DEFAULT_DIM,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self._transport = transport

    def _post(self, url: str, body: dict[str, Any]) -> Any:
        transport = self._transport
        if transport is None:
            try:
                import requests  # lazy: only when actually hitting the network
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise EmbedError(
                    "requests not installed; inject a transport or install requests"
                ) from exc

            # Map the (url, json_body, timeout) transport contract onto
            # requests.post's (url, data=, json=, timeout=) signature. Passing
            # positionally binds body->data= (form-encoded) and timeout->json=,
            # which sends a malformed request (Ollama returns 400). Bind by keyword.
            def transport(u: str, b: dict[str, Any], t: float) -> Any:
                return requests.post(u, json=b, timeout=t)

        return transport(url, body, self.timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/api/embed"
        body = {"model": self.model, "input": list(texts)}
        try:
            resp = self._post(url, body)
            resp.raise_for_status()
            payload = resp.json()
        except EmbedError:
            raise
        except Exception as exc:
            raise EmbedError(f"mxbai embed request failed: {exc}") from exc

        vectors = self._extract_vectors(payload)
        for v in vectors:
            if len(v) != self.dim:
                raise EmbedError(
                    f"embedding dimension {len(v)} != expected {self.dim} "
                    "(mxbai-embed-large must be 1024-dim across the mesh)"
                )
        return vectors

    @staticmethod
    def _extract_vectors(payload: dict[str, Any]) -> list[list[float]]:
        """Accept both the batch (``embeddings``) and single (``embedding``) shapes."""
        if "embeddings" in payload:
            return [list(v) for v in payload["embeddings"]]
        if "embedding" in payload:
            return [list(payload["embedding"])]
        raise EmbedError(f"no 'embeddings'/'embedding' key in response: {list(payload)}")
