"""出网地址钉扎（移植自 docreader/utils/ssrf_proxy.py 的判定核心）。

上游那份是一个 asyncio 本地正向代理，服务 Playwright 浏览器的每一跳连接。
本项目用 http.client 而不是浏览器，代理服务器那部分没有落点，所以这里只保留它真正
有价值的那一步：把 URL 解析成"已通过校验的数字 IP + 端口"再交给调用方直连。

为什么必须钉扎成数字 IP：先校验域名、再由 socket 重新解析一次，中间存在
DNS 重绑定的窗口（第二次解析返回内网地址）。这里解析一次、校验全部结果、
返回其中一个数字地址，调用方直接连它就不会再解析第二次。

上游的 reset_ssrf_whitelist_cache_for_test 等测试钩子留在 ssrf.py 里。
"""
from __future__ import annotations

from typing import Tuple
from urllib.parse import urlsplit

from app.core.parsing.docreader.ssrf import (
    SSRFBlockedError,
    _check_url,
    _normalize_url,
    _reason_code,
)


def checked_address(url: str) -> Tuple[str, int]:
    """返回 (数字 IP, 端口)；不允许访问时抛 ValueError。

    调用方必须直连返回的 IP，而不是再用原主机名建连。
    """
    safe, reason, ips = _check_url(url, require_address=True)
    if not safe:
        raise SSRFBlockedError(reason, _reason_code(reason))
    parsed = urlsplit(_normalize_url(url.strip()))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return str(ips[0]), port
