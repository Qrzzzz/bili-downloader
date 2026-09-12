from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit, urlunsplit


BV_RE = re.compile(r"^(BV[0-9A-Za-z]{8,})$", re.IGNORECASE)
AV_RE = re.compile(r"^(av\d+)$", re.IGNORECASE)
BVID_PATH_RE = re.compile(r"^/video/(BV[0-9A-Za-z]{8,})/?$", re.IGNORECASE)
AVID_PATH_RE = re.compile(r"^/video/(?:av)?(\d+)/?$", re.IGNORECASE)
# Match complete URL tokens, including untrusted hosts, so a Bilibili URL inside
# another URL's path/query cannot be mistaken for a standalone shared link.
SHARED_URL_RE = re.compile(
    r"(?<![0-9A-Za-z_/:?&=%@.+\\-])https?://[^\s<>\"'`\[\](){}，。；：！？、【】（）《》〈〉「」『』“”‘’]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolvedVideoUrl:
    canonical_url: str
    requested_page: int
    bvid: str = ""
    aid: str = ""

    @property
    def api_params(self) -> dict[str, str]:
        if self.bvid:
            return {"bvid": self.bvid}
        if self.aid:
            return {"aid": self.aid}
        return {}


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _official_https_url(url: str, *, allow_short: bool) -> tuple[SplitResult, str]:
    if not isinstance(url, str) or not url or any(ord(character) < 0x20 for character in url):
        raise ValueError("Bilibili 链接格式无效。")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Bilibili 链接必须使用 HTTPS。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Bilibili 链接不能包含用户信息。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Bilibili 链接端口无效。") from exc
    if port not in {None, 443}:
        raise ValueError("Bilibili 链接使用了不受支持的端口。")

    host = parsed.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Bilibili 链接不能使用 IP 地址。")

    is_video_host = _host_matches(host, "bilibili.com")
    is_short_host = allow_short and host == "b23.tv"
    if not is_video_host and not is_short_host:
        raise ValueError("链接目标不是允许的 Bilibili 官方主机。")
    return parsed, host


def validate_redirect_hop(url: str) -> tuple[SplitResult, str]:
    """Validate an official HTTPS hop before any b23 request is sent."""

    return _official_https_url(url, allow_short=True)


def canonicalize_video_url(url: str) -> ResolvedVideoUrl:
    parsed, _host = _official_https_url(url, allow_short=False)
    bvid_match = BVID_PATH_RE.search(parsed.path)
    aid_match = AVID_PATH_RE.search(parsed.path)
    if not bvid_match and not aid_match:
        raise ValueError("链接不是有效的 Bilibili 视频页面。")

    query = parse_qs(parsed.query, keep_blank_values=True)
    page_values = query.get("p", [])
    if len(page_values) > 1:
        raise ValueError("链接包含多个 p 参数，无法确定目标分 P。")
    try:
        requested_page = int(page_values[0]) if page_values else 1
    except (TypeError, ValueError) as exc:
        raise ValueError("链接中的 p 参数必须是正整数。") from exc
    if requested_page < 1:
        raise ValueError("链接中的 p 参数必须是正整数。")

    bvid = bvid_match.group(1) if bvid_match else ""
    aid = aid_match.group(1) if aid_match else ""
    identifier = bvid or f"av{aid}"
    canonical = f"https://www.bilibili.com/video/{identifier}"
    if requested_page != 1:
        canonical += "?" + urlencode({"p": requested_page})
    return ResolvedVideoUrl(canonical, requested_page, bvid=bvid, aid=aid)


def normalize_video_input(raw: str) -> str:
    """Accept one video ID, URL, or share text containing one distinct URL."""

    value = raw.strip()
    if not value:
        raise ValueError("请输入 Bilibili 视频链接、分享文本或 BV/av 号。")

    bv = BV_RE.fullmatch(value)
    if bv:
        return f"https://www.bilibili.com/video/{bv.group(1)}"
    av = AV_RE.fullmatch(value)
    if av:
        return f"https://www.bilibili.com/video/{av.group(1)}"

    urls: set[str] = set()
    first_error: ValueError | None = None
    for match in SHARED_URL_RE.finditer(value):
        candidate = match.group().rstrip(".,;:!?")
        # Common Markdown escapes in copied share query parameters. Do not
        # decode percent escapes or search for an ID inside a rejected URL.
        candidate = re.sub(r"\\([_&])", r"\1", candidate)
        try:
            urls.add(_normalize_input_url(candidate))
        except ValueError as exc:
            if first_error is None:
                first_error = exc
        if len(urls) > 1:
            raise ValueError("文本中包含多个不同的视频链接，请只保留一个后再解析。")
    if urls:
        return urls.pop()
    if first_error is not None:
        raise first_error
    raise ValueError("未找到有效的 Bilibili 视频链接，请粘贴分享文本、完整链接或 BV/av 号。")


def _normalize_input_url(value: str) -> str:
    parsed, host = _official_https_url(value, allow_short=True)
    if host == "b23.tv":
        if not parsed.path or parsed.path == "/":
            raise ValueError("b23.tv 短链接缺少视频标识。")
        # Short-link query parameters are not required for resolution and may be
        # sensitive tracking data. Never carry them into logs or error text.
        return urlunsplit(("https", "b23.tv", parsed.path, "", ""))
    return canonicalize_video_url(value).canonical_url


def extract_video_inputs(raw: str) -> list[dict[str, str]]:
    """Offline batch extraction, preserving invalid rows without following them."""
    if len(raw) > 24000 or any(ord(c) < 32 and c not in "\r\n\t" for c in raw):
        raise ValueError("批量输入过长或包含无效控制字符。")
    candidates = [re.sub(r"\\([_&])", r"\1", m.group().rstrip(".,;:!?")) for m in SHARED_URL_RE.finditer(raw)]
    candidates.extend(line.strip() for line in raw.splitlines() if BV_RE.fullmatch(line.strip()) or AV_RE.fullmatch(line.strip()))
    if not candidates:
        candidates = [raw.strip()]
    rows, seen = [], set()
    for value in candidates:
        try:
            url = normalize_video_input(value)
            if url in seen:
                continue
            seen.add(url)
            rows.append({"input": url, "message": ""})
        except ValueError as exc:
            rows.append({"input": "无效链接", "message": str(exc)})
        if len(rows) > 50:
            raise ValueError("一次最多添加 50 个视频，请分批粘贴。")
    return rows
