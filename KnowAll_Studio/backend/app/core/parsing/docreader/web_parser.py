"""Fetch a public web page with per-hop SSRF checks and a pinned DNS answer."""

from __future__ import annotations

import http.client
import socket
from email.message import Message
from urllib.parse import urljoin, urlsplit

from app.core.parsing.docreader.document import Document
from app.core.parsing.docreader.mhtml_parser import html_to_markdown
from app.core.parsing.docreader.ssrf import _normalize_url
from app.core.parsing.docreader.ssrf_proxy import checked_address

_MAX_REDIRECTS = 5
_MAX_HTML_BYTES = 5 * 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, port: int, ip: str):
        super().__init__(hostname, port, timeout=30)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, ip: str):
        super().__init__(hostname, port, timeout=30)
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._ip, self.port), self.timeout, self.source_address
        )
        try:
            # TLS still verifies the original hostname, while the TCP connection uses
            # only the numeric address that passed the SSRF check.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _request_once(url: str, address: tuple[str, int]) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    ip, port = address
    hostname = parsed.hostname or ""
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(hostname, port, ip)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        connection.request("GET", path, headers={"Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(_MAX_HTML_BYTES + 1)
        if len(body) > _MAX_HTML_BYTES:
            raise ValueError("Web page exceeds the 5 MiB import limit")
        return response.status, headers, body
    finally:
        connection.close()


def fetch_web_page(url: str) -> Document:
    """Follow at most five redirects; validate and pin every target before connecting."""
    current_url = _normalize_url(url.strip())
    for redirect_count in range(_MAX_REDIRECTS + 1):
        address = checked_address(current_url)
        status, headers, body = _request_once(current_url, address)
        if status in _REDIRECTS:
            if redirect_count == _MAX_REDIRECTS:
                raise ValueError("Web page redirects too many times")
            location = headers.get("location", "").strip()
            if not location:
                raise ValueError("Web page redirect has no Location header")
            current_url = urljoin(current_url, location)
            continue

        if status < 200 or status >= 300:
            raise ValueError(f"Web page returned HTTP {status}")
        media_type = headers.get("content-type", "text/html").split(";", 1)[0].strip().lower()
        if media_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError(f"Web page returned unsupported content type: {media_type}")
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("Compressed web responses are not supported")

        content_type = Message()
        content_type["content-type"] = headers.get("content-type", "text/html")
        charset = content_type.get_content_charset() or "utf-8"
        try:
            html = body.decode(charset, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        markdown = html_to_markdown(html, strip_internal_links=True)
        if not markdown.strip():
            raise ValueError("Web page contained no readable content")
        return Document(
            content=markdown,
            metadata={"source_format": "web", "source_url": current_url, "file_size": len(body)},
        )

    raise ValueError("Web page redirects too many times")
