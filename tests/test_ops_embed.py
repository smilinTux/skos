"""Tests for skos.brain.ops.embed — the mxbai embedder client (SB1.1).

The HTTP call is isolated behind the ``Embedder`` protocol so unit tests stub it;
no network is required here. ``MxbaiEmbedder`` is exercised only for its request
shape and response parsing via an injected transport.
"""

import pytest

from skos.brain.ops.embed import (
    EmbedError,
    Embedder,
    MxbaiEmbedder,
    embed_chunks,
)
from skos.brain.ops.models import OpsChunk


class StubEmbedder:
    """A deterministic in-memory Embedder for tests (records calls)."""

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t))] * self.dim for t in texts]


def test_stub_conforms_to_protocol():
    assert isinstance(StubEmbedder(), Embedder)


def test_embed_chunks_fills_embeddings():
    chunks = [
        OpsChunk(node_id="n", ord=0, content="alpha"),
        OpsChunk(node_id="n", ord=1, content="bravo bravo"),
    ]
    stub = StubEmbedder(dim=1024)
    out = embed_chunks(chunks, stub)
    assert len(out) == 2
    assert all(c.embedding is not None for c in out)
    assert len(out[0].embedding) == 1024
    # content -> vector mapping preserved (len('alpha')=5)
    assert out[0].embedding[0] == 5.0
    assert out[1].embedding[0] == 11.0
    # ord/node_id/content are carried through unchanged
    assert [c.ord for c in out] == [0, 1]
    assert out[1].content == "bravo bravo"


def test_embed_chunks_empty():
    assert embed_chunks([], StubEmbedder()) == []
    # no wasted call for an empty batch
    stub = StubEmbedder()
    embed_chunks([], stub)
    assert stub.calls == []


def test_embed_chunks_batches_in_one_call():
    chunks = [OpsChunk(node_id="n", ord=i, content=f"c{i}") for i in range(5)]
    stub = StubEmbedder()
    embed_chunks(chunks, stub)
    # one batched call, not five
    assert len(stub.calls) == 1
    assert stub.calls[0] == ["c0", "c1", "c2", "c3", "c4"]


# ---------------------------------------------------------------------------
# MxbaiEmbedder — request/response shape via an injected transport (no network)
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_mxbai_posts_expected_request_and_parses_embeddings():
    captured = {}

    def transport(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse({"embeddings": [[0.1] * 1024, [0.2] * 1024]})

    emb = MxbaiEmbedder(
        base_url="http://localhost:11434",
        model="mxbai-embed-large",
        transport=transport,
    )
    vecs = emb.embed(["hello", "world"])
    assert captured["url"] == "http://localhost:11434/api/embed"
    assert captured["json"]["model"] == "mxbai-embed-large"
    assert captured["json"]["input"] == ["hello", "world"]
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024


def test_mxbai_accepts_single_embedding_key():
    # Ollama returns {"embedding": [...]} for a single-string input in some versions.
    def transport(url, json, timeout):
        return FakeResponse({"embedding": [0.5] * 1024})

    emb = MxbaiEmbedder(transport=transport)
    vecs = emb.embed(["just one"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 1024


def test_mxbai_wrong_dimension_raises():
    def transport(url, json, timeout):
        return FakeResponse({"embeddings": [[0.1] * 768]})

    emb = MxbaiEmbedder(transport=transport, dim=1024)
    with pytest.raises(EmbedError, match="dimension"):
        emb.embed(["x"])


def test_mxbai_http_error_raises_embed_error():
    def transport(url, json, timeout):
        return FakeResponse({}, status=500)

    emb = MxbaiEmbedder(transport=transport)
    with pytest.raises(EmbedError):
        emb.embed(["x"])


def test_mxbai_empty_input_no_call():
    called = False

    def transport(url, json, timeout):
        nonlocal called
        called = True
        return FakeResponse({"embeddings": []})

    emb = MxbaiEmbedder(transport=transport)
    assert emb.embed([]) == []
    assert called is False


def test_default_transport_uses_requests_post_json_not_positional(monkeypatch):
    """Regression: the default transport must call requests.post with json=body +
    timeout=, not positionally (positional binds body->data=, timeout->json=,
    which Ollama 400s). CR-8.1 found the real-network path was broken this way.
    """
    import types

    from skos.brain.ops import embed as embed_mod

    calls = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.1] * 1024]}

    def fake_post(url, *args, **kwargs):
        calls["url"] = url
        calls["args"] = args
        calls["kwargs"] = kwargs
        return _Resp()

    fake_requests = types.SimpleNamespace(post=fake_post)
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    emb = embed_mod.MxbaiEmbedder(base_url="http://x")
    emb.embed(["hello"])

    # body must ride json=, never a positional data arg
    assert calls["args"] == (), f"body/timeout must be keyword, got positional {calls['args']}"
    assert "json" in calls["kwargs"], "must pass json=body"
    assert "timeout" in calls["kwargs"], "must pass timeout="
