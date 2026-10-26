"""Offline transport checks: inherited and Windows proxies must not win."""

import asyncio
import os
import urllib.request
from unittest.mock import patch

import httpx
import pytest
import requests

from app.network import configure_direct_network


@pytest.fixture
def proxy_environment(monkeypatch):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.lower().endswith("_proxy")
    }
    environment.update({
        "HTTP_PROXY": "http://127.0.0.1:20808",
        "HTTPS_PROXY": "http://127.0.0.1:20808",
        "ALL_PROXY": "http://127.0.0.1:20808",
        "ftp_proxy": "http://127.0.0.1:20808",
        "OPENAI_PROXY": "http://127.0.0.1:20808",
        "NO_PROXY": "localhost",
    })
    monkeypatch.setattr(os, "environ", environment)
    return environment


def test_httpx_sync_async_and_sdk_choose_direct(proxy_environment):
    from openai import AsyncOpenAI, OpenAI

    url = httpx.URL("https://api.deepseek.com/v1/chat/completions")
    with httpx.Client() as client:
        assert type(client._transport_for_url(url)._pool).__name__ == "HTTPProxy"

    configure_direct_network()
    assert urllib.request.getproxies() == {"no": "*"}
    assert proxy_environment["NO_PROXY"] == proxy_environment["no_proxy"] == "*"
    assert not any(name.lower() in {
        "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "openai_proxy"
    } for name in proxy_environment)
    with httpx.Client() as client, OpenAI(api_key="offline-test") as sdk:
        for transport in (client._transport_for_url(url), sdk._client._transport_for_url(url)):
            assert type(transport._pool).__name__ == "ConnectionPool"

    async def check_async():
        async with httpx.AsyncClient() as client, AsyncOpenAI(api_key="offline-test") as sdk:
            for transport in (client._transport_for_url(url), sdk._client._transport_for_url(url)):
                assert type(transport._pool).__name__ == "AsyncConnectionPool"

    asyncio.run(check_async())


def test_requests_and_urllib_ignore_system_proxy(proxy_environment):
    configure_direct_network()
    if hasattr(urllib.request, "getproxies_registry"):
        with patch.object(urllib.request, "getproxies_registry", return_value={"https": "http://127.0.0.1:20808"}) as registry:
            assert urllib.request.getproxies() == {"no": "*"}
            registry.assert_not_called()
    with requests.Session() as session:
        for url in ("https://dashscope.aliyuncs.com", "http://localhost:11434"):
            settings = session.merge_environment_settings(url, {}, False, None, None)
            assert settings["proxies"] == {}
    assert urllib.request.ProxyHandler().proxies == {"no": "*"}


def test_policy_reapplies_after_environment_reload(proxy_environment):
    configure_direct_network()
    proxy_environment.update(HTTPS_PROXY="http://127.0.0.1:20808", NO_PROXY="localhost")
    configure_direct_network()
    assert "HTTPS_PROXY" not in proxy_environment
    assert urllib.request.getproxies() == {"no": "*"}
