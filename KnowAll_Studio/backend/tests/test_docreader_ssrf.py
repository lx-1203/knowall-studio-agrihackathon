"""SSRF 判定的行为规格。

逐条翻译自 docreader/tests/test_ssrf.py（断言值与用例名保持原样），另外补上
任务点名的几个地址，以及 app/api/documents.py 的接线断言。
"""
import ipaddress
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.documents import _validate_url_safe
from app.core.parsing.docreader.ssrf import (
    SSRFBlockedError,
    assert_url_allowed,
    is_private_address,
    is_ssrf_safe_url,
    reset_ssrf_whitelist_cache_for_test,
)


@pytest.fixture(autouse=True)
def _clean_ssrf_env(monkeypatch):
    monkeypatch.setenv("SSRF_WHITELIST", "")
    monkeypatch.setenv("SSRF_WHITELIST_EXTRA", "")
    monkeypatch.delenv("SSRF_DNS_WHITELIST_ONLY", raising=False)
    reset_ssrf_whitelist_cache_for_test()
    yield
    reset_ssrf_whitelist_cache_for_test()


def _whitelist_only(monkeypatch, whitelist: str, only: str) -> None:
    monkeypatch.setenv("SSRF_WHITELIST", whitelist)
    monkeypatch.setenv("SSRF_WHITELIST_EXTRA", "")
    monkeypatch.setenv("SSRF_DNS_WHITELIST_ONLY", only)
    reset_ssrf_whitelist_cache_for_test()


def test_whitelist_only_refuses_before_dns(monkeypatch):
    _whitelist_only(monkeypatch, "docs.internal", "true")
    safe, reason = is_ssrf_safe_url("https://outside.invalid/page")
    assert not safe
    assert "not in the SSRF whitelist" in reason
    assert "DNS resolution failed" not in reason


def test_whitelist_only_admits_whitelisted_host(monkeypatch):
    _whitelist_only(monkeypatch, "docs.internal", "true")
    safe, reason = is_ssrf_safe_url("https://docs.internal/page")
    assert safe, reason


def test_whitelist_only_off_leaves_validation_alone(monkeypatch):
    _whitelist_only(monkeypatch, "docs.internal", "false")
    safe, reason = is_ssrf_safe_url("http://127.0.0.1:8080/page")
    assert not safe
    assert "not in the SSRF whitelist" not in reason


def test_whitelist_only_unparsable_value_enables_it(monkeypatch):
    _whitelist_only(monkeypatch, "docs.internal", "yes")
    safe, reason = is_ssrf_safe_url("https://outside.invalid/page")
    assert not safe
    assert "not in the SSRF whitelist" in reason


def test_blocks_loopback_ip():
    safe, reason = is_ssrf_safe_url("http://127.0.0.1:8080/page")
    assert not safe
    assert reason


def test_blocks_restricted_hostname():
    safe, reason = is_ssrf_safe_url("http://host.docker.internal/secret")
    assert not safe
    assert "restricted" in reason


def test_blocks_metadata_host():
    safe, reason = is_ssrf_safe_url(
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    )
    assert not safe
    assert reason


def test_blocks_invalid_port():
    safe, reason = is_ssrf_safe_url("https://example.com:99999/path")
    assert not safe
    assert "invalid port" in reason


def test_blocks_ipv4_mapped_private_ipv6_resolution():
    with patch(
        "app.core.parsing.docreader.ssrf._resolve_host_ips",
        return_value=((ipaddress.ip_address("::ffff:127.0.0.1"),), None),
    ):
        safe, reason = is_ssrf_safe_url("https://example.invalid/path")
    assert not safe
    assert "restricted" in reason


def test_blocks_local_use_nat64():
    for address in (
        "64:ff9b:1::",
        "64:ff9b:1::a9fe:a9fe",
        "64:ff9b:1::808:808",
        "64:ff9b:1:ffff:ffff:ffff:ffff:ffff",
    ):
        with patch(
            "app.core.parsing.docreader.ssrf._resolve_host_ips",
            return_value=((ipaddress.ip_address(address),), None),
        ):
            safe, reason = is_ssrf_safe_url("https://example.invalid/path")
            assert not safe, address
            assert "restricted" in reason, address


def test_allows_public_https():
    safe, reason = is_ssrf_safe_url("https://example.com/article")
    assert safe, reason
    assert reason == ""


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://2130706433/",
        "http://[::1]/",
        "http://169.254.169.254/",
    ],
)
def test_rejects_internal_targets(url):
    safe, reason = is_ssrf_safe_url(url)
    assert not safe, url
    assert reason, url
    with pytest.raises(SSRFBlockedError):
        assert_url_allowed(url)


def test_required_public_url_is_allowed():
    assert_url_allowed("https://example.com/")


def test_assert_url_allowed_carries_a_code():
    with pytest.raises(SSRFBlockedError) as excinfo:
        assert_url_allowed("file:///etc/passwd")
    assert excinfo.value.code == "scheme"

    with pytest.raises(SSRFBlockedError) as excinfo:
        assert_url_allowed("http://2130706433/")
    assert excinfo.value.code == "restricted"


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "2130706433", "::1", "169.254.169.254", "0x7f000001", ""],
)
def test_is_private_address_true_for_internal_hosts(host):
    assert is_private_address(host) is True


def test_is_private_address_false_for_public_host():
    assert is_private_address("example.com") is False


def test_validate_url_safe_uses_docreader_policy():
    for url in ("http://2130706433/", "http://169.254.169.254/", "http://localhost/"):
        with pytest.raises(HTTPException) as excinfo:
            _validate_url_safe(url)
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail == "不允许访问内部网络地址"

    with pytest.raises(HTTPException) as excinfo:
        _validate_url_safe("file:///etc/passwd")
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "仅支持 HTTP/HTTPS URL"

    assert _validate_url_safe("https://example.com/article") is None
