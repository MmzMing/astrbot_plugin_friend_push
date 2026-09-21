"""友链封面图（friendsConfig 的 `image` 字段）处理。

`image` 是仓库里的本地 webp，图来自用户在 `/友链` 那条消息里附的图片；
`imgurl` 是站点头像外链，原样写进配置，不经过这个模块。
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass

from PIL import Image, ImageOps

MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_SLUG_LEN = 32

# 这些后缀本身不承载站点身份，取主域名时要整体当成后缀
TWO_PART_SUFFIXES = frozenset(
    {
        # 代码托管 / 部署平台
        "github.io",
        "gitlab.io",
        "readthedocs.io",
        "vercel.app",
        "netlify.app",
        "workers.dev",
        "pages.dev",
        "appspot.com",
        "web.app",
        "firebaseapp.com",
        "now.sh",
        "railway.app",
        "glitch.me",
        "4everland.io",
        # 免费域名服务
        "is-a.dev",
        "ccwu.cc",
        "hkvc.cc",
        "eu.org",
        "us.to",
        "uk.to",
        "de.to",
        "eu.to",
        "dkdns.me",
        "dkdns.cn",
        "dkdns.de",
        "dkdns.eu",
        # 各国二段公共后缀
        "com.cn",
        "net.cn",
        "org.cn",
        "gov.cn",
        "edu.cn",
        "ac.cn",
        "com.hk",
        "org.hk",
        "idv.hk",
        "com.tw",
        "org.tw",
        "idv.tw",
        "co.jp",
        "ne.jp",
        "or.jp",
        "co.kr",
        "or.kr",
        "com.sg",
        "com.my",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "net.nz",
        "org.nz",
        "com.br",
        "com.ar",
        "co.za",
        "co.uk",
        "org.uk",
        "me.uk",
        "ltd.uk",
        "plc.uk",
        "com.ru",
        "co.in",
        "com.tr",
        "com.ua",
    }
)

# 中国省级行政区代号，同样是二段后缀（blog.shancheng.cq.cn 的主体是 shancheng）
CN_PROVINCE_CODES = (
    "bj tj sh cq he sx nm ln jl hlj js zj ah fj jx sd ha hb hn gd gx hi "
    "sc gz yun xz sn gs qh nx xj"
)
CN_PROVINCE_SUFFIXES = frozenset(f"{code}.cn" for code in CN_PROVINCE_CODES.split())

RESAMPLER = Image.Resampling.LANCZOS


class CoverError(ValueError):
    """封面图不可用；调用方应降级为不写 image 字段。"""


def _host_of(url: str) -> str:
    s = url.strip()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s, flags=re.IGNORECASE)
    s = re.split(r"[/?#]", s, maxsplit=1)[0]
    s = s.rsplit("@", 1)[-1]
    if s.startswith("["):
        s = s[1:].split("]", 1)[0]
    else:
        s = s.split(":", 1)[0]
    return s.lower()


def slug_from_url(url: str) -> str:
    """取站点主域名标签：tc.lqay.cn → lqay，xfcnl.github.io → xfcnl。"""
    host = _host_of(url)
    labels = [label for label in host.split(".") if label]
    if not labels:
        return hashlib.sha1(host.encode("utf-8")).hexdigest()[:8]

    at = -2 if len(labels) >= 2 else -1
    if len(labels) >= 3:
        tail = ".".join(labels[-2:])
        if tail in TWO_PART_SUFFIXES or tail in CN_PROVINCE_SUFFIXES:
            at = -3

    slug = re.sub(r"[^a-z0-9_-]", "", labels[at])
    return slug[:MAX_SLUG_LEN] or hashlib.sha1(host.encode("utf-8")).hexdigest()[:8]


def unique_slug(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def compute_target_size(
    width: int,
    height: int,
    mode: str,
    px: int,
    upscale: bool = False,
) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise CoverError(f"非法图片尺寸 {width}x{height}。")
    if mode == "none":
        return width, height
    if mode not in ("width", "height", "longest"):
        raise CoverError(f"未知的缩放模式 {mode}。")
    if px <= 0:
        raise CoverError("目标像素必须为正整数。")

    if mode == "width":
        reference, other = width, height
    elif mode == "height":
        reference, other = height, width
    else:
        reference, other = max(width, height), min(width, height)

    if reference <= px and not upscale:
        return width, height

    scaled_other = max(1, round(other * px / reference))
    if mode == "width":
        return px, scaled_other
    if mode == "height":
        return scaled_other, px
    if width >= height:
        return px, scaled_other
    return scaled_other, px


@dataclass(frozen=True)
class ConvertedCover:
    data: bytes
    size: tuple[int, int]


def load_image_file(path: str) -> bytes:
    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise CoverError(f"读不到图片文件：{e.__class__.__name__}") from e
    if size > MAX_COVER_BYTES:
        raise CoverError(f"封面图 {size // 1024 // 1024}MB，超过 8MB 上限。")
    with open(path, "rb") as f:
        return f.read()


def to_webp(
    raw: bytes,
    mode: str,
    px: int,
    quality: int,
    upscale: bool = False,
) -> ConvertedCover:
    if not raw:
        raise CoverError("图片数据为空。")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:  # Pillow 的异常类型太多，统一转成插件自己的错误
        raise CoverError(f"无法解码图片：{e.__class__.__name__}") from e

    im = ImageOps.exif_transpose(im)
    im = im.convert("RGBA" if "A" in im.getbands() else "RGB")

    target = compute_target_size(im.width, im.height, mode, px, upscale)
    if target != (im.width, im.height):
        im = im.resize(target, RESAMPLER)

    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=int(quality), method=6)
    return ConvertedCover(buf.getvalue(), target)
