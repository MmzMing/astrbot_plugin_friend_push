import io

import pytest
from PIL import Image

from avatar import (
    AvatarError,
    compute_target_size,
    slug_from_url,
    to_webp,
    unique_slug,
    validate_content_type,
)


def img(w, h, mode="RGB", color=None):
    im = Image.new(mode, (w, h))
    return im


def png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://blog.fqzlr.top/", "fqzlr"),
        ("https://2x.nz/", "2x"),
        ("https://karina.xin/", "karina"),
        ("https://www.nanzhiy.cn/", "nanzhiy"),
        ("https://ragnote.top/", "ragnote"),
        ("https://home.132614.xyz/", "132614"),
        ("http://Blog.Amamo.TOP:8080/x", "amamo"),
        ("https://dev.cdn.example.com/", "cdn"),
    ],
)
def test_slug_from_url(url, expected):
    assert slug_from_url(url) == expected


def test_slug_from_url_falls_back_on_punycode_only():
    assert slug_from_url("https://xn--fiqs8s.example/")
    assert slug_from_url("not a url at all")


def test_slug_is_filename_safe():
    s = slug_from_url("https://a b/../etc/passwd:80/")
    assert "/" not in s and "\\" not in s and ".." not in s and " " not in s


def test_unique_slug_appends_number():
    existing = {"fqzlr", "fqzlr2"}
    assert unique_slug("fqzlr", existing) == "fqzlr3"
    assert unique_slug("new", existing) == "new"


def test_width_mode_keeps_aspect():
    assert compute_target_size(1000, 500, "width", 900) == (900, 450)


def test_width_mode_does_not_upscale_by_default():
    assert compute_target_size(800, 400, "width", 900) == (800, 400)
    assert compute_target_size(800, 400, "width", 900, upscale=True) == (900, 450)


def test_height_mode():
    assert compute_target_size(400, 1000, "height", 900) == (360, 900)
    assert compute_target_size(400, 1600, "height", 800) == (200, 800)


def test_longest_mode():
    assert compute_target_size(2000, 1000, "longest", 900) == (900, 450)
    assert compute_target_size(1000, 2000, "longest", 900) == (450, 900)
    assert compute_target_size(300, 200, "longest", 900) == (300, 200)


def test_none_mode():
    assert compute_target_size(3000, 2000, "none", 900) == (3000, 2000)


def test_target_never_below_one_pixel():
    w, h = compute_target_size(1000, 1, "width", 1)
    assert w >= 1 and h >= 1


def test_unknown_mode_raises():
    with pytest.raises(AvatarError):
        compute_target_size(10, 10, "diagonal", 100)


def test_to_webp_preserves_alpha():
    res = to_webp(png_bytes(img(2000, 1000, "RGBA")), "width", 900, 82)
    im = Image.open(io.BytesIO(res.data))
    assert im.format == "WEBP"
    assert res.size == (900, 450)
    assert im.size == (900, 450)
    assert "A" in im.mode


def to_bytes(im, fmt):
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


def test_to_webp_accepts_common_real_world_modes():
    cases = {
        "P": png_bytes(img(100, 50, "P")),
        "L": png_bytes(img(100, 50, "L")),
        "RGBA": png_bytes(img(100, 50, "RGBA")),
        "CMYK": to_bytes(img(100, 50, "CMYK"), "TIFF"),
        "JPEG": to_bytes(img(100, 50, "RGB"), "JPEG"),
    }
    for mode, data in cases.items():
        res = to_webp(data, "none", 900, 82)
        assert Image.open(io.BytesIO(res.data)).format == "WEBP", mode


def test_to_webp_rejects_garbage():
    with pytest.raises(AvatarError):
        to_webp(b"definitely not an image", "width", 900, 82)


def test_to_webp_zero_byte_input():
    with pytest.raises(AvatarError):
        to_webp(b"", "width", 900, 82)


def test_quality_affects_size():
    data = png_bytes(Image.effect_noise((1200, 1200), 100).convert("RGB"))
    small = to_webp(data, "none", 900, 20)
    big = to_webp(data, "none", 900, 95)
    assert len(small.data) < len(big.data)


def test_validate_content_type():
    validate_content_type("image/png", 100)
    validate_content_type("image/jpeg", 100)
    with pytest.raises(AvatarError):
        validate_content_type("text/html", 100)
    with pytest.raises(AvatarError):
        validate_content_type("", 100)
    with pytest.raises(AvatarError):
        validate_content_type("image/png", 999_999_999)
