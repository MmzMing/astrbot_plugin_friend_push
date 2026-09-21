import pytest

from friends_io import ParseError, parse_submission

GOOD = "Firefly Docs|Firefly主题模板文档|https://docs-firefly.cuteleaf.cn|https://x/logo.png"


def test_four_positional_args():
    s = parse_submission(GOOD)
    assert s.link.title == "Firefly Docs"
    assert s.link.desc == "Firefly主题模板文档"
    assert s.link.siteurl == "https://docs-firefly.cuteleaf.cn"
    assert s.link.imgurl == "https://x/logo.png"
    assert s.link.enabled is True


def test_defaults_applied():
    s = parse_submission(GOOD, default_weight=7, default_tags=("Blog", "星图"))
    assert s.link.weight == 7
    assert s.link.tags == ("Blog", "星图")


def test_optional_keyvalue_overrides():
    s = parse_submission(
        GOOD + "|tags=文档,收藏博客|weight=9|slug=firefly|px=1200|resize=longest"
    )
    assert s.link.tags == ("文档", "收藏博客")
    assert s.link.weight == 9
    assert s.slug == "firefly"
    assert s.px == 1200
    assert s.resize_mode == "longest"
    assert s.no_image is False


def test_fullwidth_comma_in_tags():
    s = parse_submission(GOOD + "|tags=文档，博客")
    assert s.link.tags == ("文档", "博客")


def test_noimg_flag():
    assert parse_submission(GOOD + "|noimg").no_image is True


def test_suffix_order_irrelevant_and_spaces_tolerated():
    s = parse_submission("  " + GOOD.replace("|", " | ") + " | weight = 3 | NOIMG ")
    assert s.link.weight == 3
    assert s.no_image is True


def test_too_few_args_raises():
    with pytest.raises(ParseError) as e:
        parse_submission("标题|描述|https://a.com")
    assert "标题|描述|站点URL|头像URL" in str(e.value)


@pytest.mark.parametrize(
    "text",
    [
        "标题|描述|https://a.com|",
        "标题||https://a.com|https://b",
        "|描述|https://a.com|https://b",
        "  |描述|https://a.com|https://b",
    ],
)
def test_empty_positional_raises(text):
    with pytest.raises(ParseError):
        parse_submission(text)


@pytest.mark.parametrize(
    "bad",
    [
        "标题|描述|https://a.com|https://x/logo.png|weight=abc",
        "标题|描述|https://a.com|https://x/logo.png|weight=-1",
        "标题|描述|https://a.com|https://x/logo.png|px=0",
        "标题|描述|https://a.com|https://x/logo.png|resize=diagonal",
        "标题|描述|https://a.com|https://x/logo.png|slug=../../etc",
        "标题|描述|https://a.com|https://x/logo.png|tags=",
    ],
)
def test_invalid_option_raises(bad):
    with pytest.raises(ParseError):
        parse_submission(bad)


@pytest.mark.parametrize(
    "bad_siteurl",
    ["a.com", "https://", "https://a", "javascript:alert(1)", "标题"],
)
def test_siteurl_must_be_http_url_with_host(bad_siteurl):
    with pytest.raises(ParseError):
        parse_submission(f"标题|描述|{bad_siteurl}|https://x/logo.png")


def test_imgurl_must_be_http_url():
    with pytest.raises(ParseError):
        parse_submission("标题|描述|https://a.com|//x/logo.png")


def test_unknown_key_raises():
    with pytest.raises(ParseError):
        parse_submission(GOOD + "|colo红=1")


def test_bare_token_that_is_not_a_flag_raises():
    with pytest.raises(ParseError) as e:
        parse_submission(GOOD + "|whatever")
    assert "noimg" in str(e.value)


def test_subdomain_siteurl_is_accepted():
    s = parse_submission("标题|描述|https://blog.fqzlr.top/|https://x/a.jpg")
    assert s.link.siteurl == "https://blog.fqzlr.top/"
