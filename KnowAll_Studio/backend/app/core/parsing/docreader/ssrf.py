"""SSRF 出网判定（移植自 docreader/utils/ssrf.py）。

比项目原来的"主机名黑名单 + 禁止 .local"强在哪：
  * 十进制/八进制/十六进制 IP 字面量（http://2130706433/）会被正则识别并拒绝；
  * 域名先解析成 IP 再逐个判定（含 IPv4 映射、6to4、Teredo、NAT64 里的内网载荷），
    所以"公网域名指向内网"会被拒绝；网页抓取器再将校验结果钉扎到 TCP 连接，
    避免校验后第二次解析导致 DNS 重绑定；
  * 覆盖云元数据地址、K8s service、保留网段（100.64/10、198.18/15、240/4 …）；
  * 拦截 22/3306/6379/9200 等一批不该被文档导入碰的端口。

本模块只做判定，不发起请求。调用方应在真正出网前（以及每一跳重定向后）调用
assert_url_allowed()；is_ssrf_safe_url() 保留上游的 (safe, reason) 形态便于迁移。
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from functools import lru_cache
from typing import FrozenSet, Optional, Tuple, Union
from urllib.parse import urlparse

RESTRICTED_HOSTNAMES: FrozenSet[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata.google.internal",
        "metadata.tencentyun.com",
        "metadata.aws.internal",
        "host.docker.internal",
        "gateway.docker.internal",
        "kubernetes.docker.internal",
        "kubernetes",
        "kubernetes.default",
        "kubernetes.default.svc",
        "kubernetes.default.svc.cluster.local",
    }
)

RESTRICTED_SUFFIXES: Tuple[str, ...] = (
    ".local",
    ".localhost",
    ".internal",
    ".corp",
    ".lan",
    ".home",
    ".localdomain",
    ".svc.cluster.local",
    ".pod.cluster.local",
)

EXTRA_RESTRICTED_CIDRS: Tuple[Union[ipaddress.IPv4Network, ipaddress.IPv6Network], ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "100.64.0.0/10",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "0.0.0.0/8",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "172.17.0.0/16",
        "172.18.0.0/16",
        "172.19.0.0/16",
        "172.20.0.0/16",
    )
)

BLOCKED_PORTS: FrozenSet[str] = frozenset(
    {
        "22",
        "23",
        "25",
        "445",
        "3389",
        "5432",
        "3306",
        "6379",
        "27017",
        "9200",
        "2379",
        "2380",
        "8500",
        "4001",
    }
)

_IP_LIKE_PATTERNS = (
    re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
    re.compile(r"^\d{8,10}$"),
    re.compile(r"^0[0-7]+\."),
    re.compile(r"(?i)^0x[0-9a-f]+\."),
    re.compile(r"(?i)^0x[0-9a-f]{6,8}$"),
    re.compile(r"(?i)^[0-9a-f:]+::[0-9a-f:]*$"),
    re.compile(r"(?i)^[0-9a-f]{1,4}(:[0-9a-f]{1,4}){7}$"),
)


class SSRFBlockedError(ValueError):
    """目标地址被出网策略拒绝。code 给调用方做错误文案映射用。"""

    def __init__(self, reason: str, code: str = "restricted") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _normalize_url(raw_url: str) -> str:
    if "://" not in raw_url:
        return f"https://{raw_url}"
    return raw_url


@lru_cache(maxsize=1)
def _load_whitelist() -> Tuple[
    FrozenSet[str],
    Tuple[str, ...],
    Tuple[Union[ipaddress.IPv4Network, ipaddress.IPv6Network], ...],
]:
    entries: list = []
    for env_key in ("SSRF_WHITELIST", "SSRF_WHITELIST_EXTRA"):
        raw = os.environ.get(env_key, "")
        if raw.strip():
            entries.extend(part.strip() for part in raw.split(",") if part.strip())

    exact_hosts: set = set()
    suffix_hosts: list = []
    cidr_nets: list = []
    for entry in entries:
        lowered = entry.lower()
        if lowered.startswith("*."):
            suffix_hosts.append(lowered[1:])
            continue
        if "/" in lowered:
            try:
                cidr_nets.append(ipaddress.ip_network(lowered, strict=False))
            except ValueError:
                continue
            continue
        exact_hosts.add(lowered)
    return frozenset(exact_hosts), tuple(suffix_hosts), tuple(cidr_nets)


def _whitelist_only_enabled() -> bool:
    """SSRF_DNS_WHITELIST_ONLY 打开时白名单就是全部出网策略：不在名单里的域名
    在解析 DNS 之前就被拒绝。非空但无法识别的取值一律按"开启"处理——白名单锁定
    静默失效是这里唯一不能接受的失败模式。
    """
    raw = os.environ.get("SSRF_DNS_WHITELIST_ONLY", "").strip()
    if not raw:
        return False
    return raw.lower() not in {"0", "f", "false"}


def _is_whitelisted(hostname: str) -> bool:
    lowered = hostname.lower()
    exact_hosts, suffix_hosts, cidr_nets = _load_whitelist()
    if lowered in exact_hosts:
        return True
    for suffix in suffix_hosts:
        if lowered.endswith(suffix) or lowered == suffix.lstrip("."):
            return True
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return any(ip in net for net in cidr_nets)


def _is_ip_like_hostname(hostname: str) -> bool:
    return any(pattern.search(hostname) for pattern in _IP_LIKE_PATTERNS)


def _is_restricted_ip(
    ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address],
) -> Optional[str]:
    if ip.is_private:
        return "private IP address"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_unspecified:
        return "unspecified address"
    if isinstance(ip, ipaddress.IPv4Address):
        for net in EXTRA_RESTRICTED_CIDRS:
            if isinstance(net, ipaddress.IPv4Network) and ip in net:
                return f"restricted range {net}"
    if isinstance(ip, ipaddress.IPv6Address):
        embedded_ipv4 = ip.ipv4_mapped
        if embedded_ipv4 is not None:
            reason = _is_restricted_ip(embedded_ipv4)
            if reason:
                return f"IPv4-mapped {reason}"
        if ip.sixtofour is not None:
            reason = _is_restricted_ip(ip.sixtofour)
            if reason:
                return f"6to4-embedded {reason}"
        if ip.teredo is not None:
            server_ip, client_ip = ip.teredo
            for label, embedded_ip in (("server", server_ip), ("client", client_ip)):
                reason = _is_restricted_ip(embedded_ip)
                if reason:
                    return f"Teredo {label} embeds {reason}"
        # NAT64 的众所周知前缀（64:ff9b::/96）和已废弃的 IPv4 兼容形式（::a.b.c.d）
        # 同样夹带一个普通 IPv4 地址，但 ipaddress 没有对应访问器，is_private 和
        # .sixtofour 都看不见——少了这两段，::169.254.169.254 会被当成正常公网 IPv6。
        packed = ip.packed
        # RFC 8215 的本地 NAT64 前缀 IPv4 布局由部署方自定，整段 /48 拒绝。
        if packed[:6] == b"\x00\x64\xff\x9b\x00\x01":
            return "local-use NAT64 translation address"
        if packed[0:4] == b"\x00\x64\xff\x9b" and packed[4:12] == bytes(8):
            reason = _is_restricted_ip(ipaddress.IPv4Address(packed[12:16]))
            if reason:
                return f"NAT64-embedded {reason}"
        if packed[0:12] == bytes(12):
            # :: 和 ::1 已在上面返回，::ffff:a.b.c.d 由 ipv4_mapped 分支处理。
            reason = _is_restricted_ip(ipaddress.IPv4Address(packed[12:16]))
            if reason:
                return f"IPv4-compatible {reason}"
        # 站点本地地址 fec0::/10
        if (ip.packed[0] == 0xFE) and (ip.packed[1] & 0xC0) == 0xC0:
            return "site-local IPv6 address"
    return None


def _resolve_host_ips(
    hostname: str,
) -> Tuple[Tuple[Union[ipaddress.IPv4Address, ipaddress.IPv6Address], ...], Optional[str]]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return (), f"DNS resolution failed for hostname {hostname}: {exc}"
    ips: list = []
    seen: set = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            ips.append(ipaddress.ip_address(ip_str))
        except ValueError:
            continue
    if not ips:
        return (), f"DNS resolution failed for hostname {hostname}: no addresses"
    return tuple(ips), None


def _check_url(raw_url: str, *, require_address: bool = False):
    """Validate once and retain the DNS answer for a pinned connection."""
    if not raw_url or not raw_url.strip():
        return False, "URL is empty", ()

    normalized = _normalize_url(raw_url.strip())
    try:
        parsed = urlparse(normalized)
        hostname = (parsed.hostname or "").strip()
    except ValueError as exc:
        return False, f"invalid URL: {exc}", ()
    try:
        port = parsed.port
    except ValueError as exc:
        return False, f"invalid port: {exc}", ()
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False, f"invalid scheme: {scheme or '(none)'} (only http/https allowed)", ()

    if not hostname:
        return False, "URL has no hostname", ()

    if parsed.username is not None or parsed.password is not None:
        return False, "URL credentials are not allowed", ()

    hostname_lower = hostname.lower()
    if _is_whitelisted(hostname_lower):
        if require_address:
            resolved_ips, resolve_err = _resolve_host_ips(hostname_lower)
            if resolve_err:
                return False, resolve_err, ()
            return True, "", resolved_ips
        return True, "", ()

    # 白名单模式到此为止：下面要解析 DNS，而名单外的域名不该有机会触发解析。
    if _whitelist_only_enabled():
        return (
            False,
            f"host is not in the SSRF whitelist: {hostname_lower} "
            "(SSRF_DNS_WHITELIST_ONLY is on; add it to SSRF_WHITELIST to allow it)",
            (),
        )

    if hostname_lower in RESTRICTED_HOSTNAMES:
        return False, f"hostname {hostname_lower} is restricted", ()

    for suffix in RESTRICTED_SUFFIXES:
        if hostname_lower.endswith(suffix):
            return False, f"hostname suffix {suffix} is restricted", ()

    try:
        ipaddress.ip_address(hostname_lower)
        return False, "direct IP address access is not allowed, use domain name or add to SSRF_WHITELIST", ()
    except ValueError:
        pass

    if _is_ip_like_hostname(hostname_lower):
        return False, "IP-like hostname format is not allowed", ()

    resolved_ips, resolve_err = _resolve_host_ips(hostname_lower)
    if resolve_err:
        return False, resolve_err, ()

    for resolved_ip in resolved_ips:
        reason = _is_restricted_ip(resolved_ip)
        if reason:
            return (
                False,
                f"hostname {hostname_lower} resolves to restricted IP {resolved_ip}: {reason}",
                (),
            )

    if port is not None and str(port) in BLOCKED_PORTS:
        return False, f"port {port} is blocked for security reasons", ()

    return True, "", resolved_ips


def is_ssrf_safe_url(raw_url: str) -> Tuple[bool, str]:
    """返回 (是否安全, 原因)。安全时原因为空串。"""
    safe, reason, _ = _check_url(raw_url)
    return safe, reason


def _reason_code(reason: str) -> str:
    if reason.startswith("invalid scheme"):
        return "scheme"
    if reason.startswith("URL has no hostname") or reason.startswith("URL is empty"):
        return "no_hostname"
    if reason.startswith("DNS resolution failed"):
        return "dns"
    if reason.startswith("invalid URL"):
        return "invalid_url"
    return "restricted"


def assert_url_allowed(url: str) -> None:
    """URL 不安全时抛 SSRFBlockedError，安全时静默返回。"""
    safe, reason = is_ssrf_safe_url(url)
    if not safe:
        raise SSRFBlockedError(reason, _reason_code(reason))


def is_private_address(host: str) -> bool:
    """主机名或 IP 字面量是否指向内网/受限地址。

    无法解析或无法判断时按"是"返回（fail closed）：调用方拿它做放行判断，
    返回 False 必须意味着"确认可出网"。
    """
    value = (host or "").strip().strip("[]").lower()
    if not value:
        return True
    if value in RESTRICTED_HOSTNAMES:
        return True
    if any(value.endswith(suffix) for suffix in RESTRICTED_SUFFIXES):
        return True
    try:
        return _is_restricted_ip(ipaddress.ip_address(value)) is not None
    except ValueError:
        pass
    if _is_ip_like_hostname(value):
        return True
    ips, resolve_err = _resolve_host_ips(value)
    if resolve_err:
        return True
    return any(_is_restricted_ip(ip) for ip in ips)


def reset_ssrf_whitelist_cache_for_test() -> None:
    """清空白名单缓存（仅供单测调用）。"""
    _load_whitelist.cache_clear()
