"""Tests for `skmodels rank` / `skmodels suggest` (card P3.4).

Thin CLI over the gateway's GET /admin/models/rank (design doc 7.1/6.2). No
ranking logic here, formatting + request construction only, so these tests
mock the HTTP call and assert:
  - request construction: role vs require= query params
  - output formatting: ranked chain + per-dimension breakdown with basis tags
  - graceful failure when the gateway is unreachable / errors
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from skos.models import cli as models_cli


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *a, **kw):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _rank_payload(role: str | None = "sk-tools") -> dict:
    payload = {
        "chain": [
            {
                "id": "local-llama-70b",
                "score": 0.912,
                "tier": "local",
                "breakdown": {
                    "tool_use": {"value": True, "basis": "card"},
                    "success_rate": {"value": 0.98, "basis": "empirical"},
                },
            },
            {
                "id": "openrouter/some-remote",
                "score": 0.71,
                "tier": "free-remote",
                "excluded_reason": None,
                "breakdown": {
                    "tool_use": {"value": True, "basis": "card"},
                },
            },
        ],
    }
    if role:
        payload["role"] = role
    return payload


def _query(url: str) -> dict:
    return parse_qs(urlsplit(url).query)


def test_rank_requests_role_query_and_prints_chain(monkeypatch, capsys):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_rank_payload("sk-tools"))

    monkeypatch.setattr(models_cli.urllib.request, "urlopen", fake_urlopen)

    rc = models_cli.main(["rank", "sk-tools"])

    assert rc == 0
    assert "/admin/models/rank" in captured["url"]
    qs = _query(captured["url"])
    assert qs["role"] == ["sk-tools"]
    assert "require" not in qs

    out = capsys.readouterr().out
    assert "role: sk-tools" in out
    assert "local-llama-70b" in out
    assert "openrouter/some-remote" in out
    assert "score=0.912" in out
    assert "tier=local" in out
    assert "tool_use=True (basis=card)" in out
    assert "success_rate=0.98 (basis=empirical)" in out


def test_suggest_builds_require_from_need_ctx_tier(monkeypatch, capsys):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_rank_payload(role=None))

    monkeypatch.setattr(models_cli.urllib.request, "urlopen", fake_urlopen)

    rc = models_cli.main([
        "suggest", "--need", "tools", "--ctx", "64k",
        "--tier", "local,free-remote",
    ])

    assert rc == 0
    qs = _query(captured["url"])
    assert "role" not in qs
    require = qs["require"][0]
    parts = require.split(",")
    assert "tool_use" in parts
    assert "min_ctx=64000" in parts
    assert "tier=local|free-remote" in parts

    out = capsys.readouterr().out
    assert "require: tool_use,min_ctx=64000,tier=local|free-remote" in out
    assert "local-llama-70b" in out


def test_suggest_repeatable_and_comma_needs(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResponse(_rank_payload(role=None))

    monkeypatch.setattr(models_cli.urllib.request, "urlopen", fake_urlopen)

    rc = models_cli.main([
        "suggest", "--need", "tools,vision", "--need", "sovereign",
    ])

    assert rc == 0
    require = _query(captured["url"])["require"][0]
    parts = require.split(",")
    assert "tool_use" in parts
    assert "vision" in parts
    assert "sovereign" in parts


def test_suggest_with_no_flags_is_a_clear_usage_error(capsys):
    rc = models_cli.main(["suggest"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "need" in err.lower() or "ctx" in err.lower() or "tier" in err.lower()


def test_rank_reports_clear_error_when_gateway_unreachable(monkeypatch, capsys):
    def fake_urlopen(req, timeout=None):
        raise URLError("Connection refused")

    monkeypatch.setattr(models_cli.urllib.request, "urlopen", fake_urlopen)

    rc = models_cli.main(["rank", "sk-tools"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "gateway" in err.lower()
    assert "unreachable" in err.lower()


def test_suggest_reports_clear_error_on_http_error(monkeypatch, capsys):
    def fake_urlopen(req, timeout=None):
        raise HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(models_cli.urllib.request, "urlopen", fake_urlopen)

    rc = models_cli.main(["suggest", "--need", "tools"])

    assert rc != 0
    err = capsys.readouterr().err
    assert "503" in err


def test_gateway_base_strips_v1_suffix(monkeypatch):
    monkeypatch.setenv("SKGATEWAY_URL", "http://example.internal:18780/v1")
    assert models_cli._gateway_base() == "http://example.internal:18780"


def test_gateway_base_default(monkeypatch):
    monkeypatch.delenv("SKGATEWAY_URL", raising=False)
    assert models_cli._gateway_base() == "http://localhost:18780"
