# -*- coding: utf-8 -*-
"""抓取脚本的 URL 安全校验：仅允许访问白名单站点，防止 SSRF/非预期外联。"""
import urllib.parse

WIKI_ALLOWED_SCHEMES = {"http", "https"}
WIKI_ALLOWED_HOSTS = {"wiki.biligame.com"}


def ensure_wiki_url(url: str) -> str:
    """校验 URL 是否为合法的 B 站原神 Wiki 地址，非法时抛 ValueError。"""
    parsed = urllib.parse.urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in WIKI_ALLOWED_SCHEMES:
        raise ValueError(f"不允许的 URL 协议: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host not in WIKI_ALLOWED_HOSTS:
        raise ValueError(f"不允许的 URL 主机: {host!r}")
    return url
