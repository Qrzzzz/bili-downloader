from __future__ import annotations

import pytest


VIDEO = "https://www.bilibili.com/video/BV1kkbC6eEgm"
OTHER = "https://www.bilibili.com/video/BV1Synthetic99"
SHARE = f"【华强卖瓜-大厂版】 {VIDEO}/?share_source=copy_web&vd_source=synthetic"


@pytest.mark.parametrize(("value", "expected"), [
    (SHARE, VIDEO),
    (f"【华强卖瓜-大厂版】\r\n{VIDEO}/?share_source=copy_web", VIDEO),
    (f"【华强卖瓜-大厂版】 [{VIDEO}/?share\\_source=copy\\_web&vd\\_source=synthetic]"
     f"({VIDEO}/?share_source=copy_web\\&vd_source=synthetic)", VIDEO),
    (f"[观看视频]({VIDEO}?share_source=copy_web\\&p=2)", VIDEO + "?p=2"),
    (f"分享链接：<{VIDEO}?p=3&share_source=test>。", VIDEO + "?p=3"),
    (f"推荐（{VIDEO}），看完告诉我。", VIDEO),
    (f"推荐{VIDEO}。", VIDEO),
    (f"Watch \"{VIDEO}?p=2\"!", VIDEO + "?p=2"),
    (f"Watch {VIDEO}?p=2.", VIDEO + "?p=2"),
    ("【分享】 https://b23.tv/Ab12Cd?share_source=copy&token=synthetic。", "https://b23.tv/Ab12Cd"),
    ("【分享】\nhttps://b23.tv/Ab12Cd", "https://b23.tv/Ab12Cd"),
    ("分享 https://m.bilibili.com/video/av12345/?p=2", "https://www.bilibili.com/video/av12345?p=2"),
    (f"{VIDEO}/?share_source=one {VIDEO}?share_source=two&p=1#reply", VIDEO),
    (f"参考 https://example.com/article 视频 {VIDEO}", VIDEO),
    (" BV1kkbC6eEgm ", VIDEO),
    ("av12345", "https://www.bilibili.com/video/av12345"),
    (VIDEO + "/?p=2&share_source=test#reply", VIDEO + "?p=2"),
])
def test_share_text_and_existing_inputs(value, expected):
    from app.utils import normalize_bilibili_url

    assert normalize_bilibili_url(value) == expected


@pytest.mark.parametrize("value", [
    "", "只有视频标题", "标题中的 BV1kkbC6eEgm 不是链接", "av12345extra",
    "http://www.bilibili.com/video/BV1kkbC6eEgm",
    "https://www.bilibili.com.evil.example/video/BV1kkbC6eEgm",
    "https://b23.tv.evil.example/Ab12Cd",
    "https://user:secret@www.bilibili.com/video/BV1kkbC6eEgm",
    "https://www.bilibili.com:444/video/BV1kkbC6eEgm",
    "https://127.0.0.1/video/BV1kkbC6eEgm",
    "https://www.bilibili.com/", "https://b23.tv/",
    "https://www.bilibili.com/video/BV1kkbC6eEgm/extra",
    VIDEO + "?p=0", VIDEO + "?p=no", VIDEO + "?p=2&p=3",
    VIDEO + "\x00suffix",
    "https://evil.example/?next=" + VIDEO,
    "https://evil.example/" + VIDEO,
    "https://evil.example/?next=" + VIDEO.replace(":", "%3A").replace("/", "%2F"),
])
def test_invalid_share_text_never_reaches_video_parser(value, monkeypatch):
    from app.services import parse_service
    from app.utils import AppError, ErrorKind

    def unexpected(*args):
        pytest.fail("Rejected share text reached the upstream video parser")

    monkeypatch.setattr(parse_service, "parse_video_info", unexpected)
    with pytest.raises(AppError) as error:
        parse_service.parse_video("分享：" + value if value else value, None, None, None)
    assert error.value.kind == ErrorKind.INVALID_URL


@pytest.mark.parametrize("value", [
    f"{VIDEO} {OTHER}", f"{VIDEO}?p=1\n{VIDEO}?p=2",
    f"[视频]({VIDEO})\n[另一个]({OTHER})",
    f"{VIDEO} https://b23.tv/Ab12Cd",
])
def test_multiple_distinct_links_require_one_target(value):
    from app.video_urls import normalize_video_input

    with pytest.raises(ValueError, match="多个不同的视频链接"):
        normalize_video_input(value)


def test_parse_service_forwards_only_canonical_link_and_original_context(monkeypatch):
    from app.services import parse_service

    context = [object(), object(), object(), "expected-generation"]
    calls = []
    result = object()

    def parse(*args):
        calls.append(args)
        return result

    monkeypatch.setattr(parse_service, "parse_video_info", parse)
    assert parse_service.parse_video(SHARE, *context) is result
    config, mode, log, generation = context
    assert calls == [(VIDEO, config, log, mode, generation)]
