"""Process-local direct networking policy for Agent clients."""

import os


def configure_direct_network() -> None:
    """Apply after dotenv loading, before constructing HTTP clients."""
    for name in tuple(os.environ):
        if name.lower() in {
            "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "openai_proxy"
        }:
            os.environ.pop(name, None)
    # Wildcard also bypasses Windows registry proxies used by urllib/HTTPX.
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
