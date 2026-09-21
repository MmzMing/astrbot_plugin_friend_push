"""头像下载与 webp 转换：与 AstrBot、GitHub 均无耦合，便于单测。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from dataclasses import dataclass

import aiohttp
from PIL import Image, ImageOps

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_SLUG_LEN = 32

# 这些前缀 label 不承载站点身份，推导 slug 时跳过
GENERIC_LABELS = frozenset(
    {
        "www",
        "www2",
        "blog",
        "m",
        "dev",
        "docs",
        "doc",
        "en",
        "jp",
        "cn",
        "home",
        "app",
        "web",
        "site",
        "pages",
    }
)

RESAMPLER = Image.Resampling.LANCZOS


class AvatarError(ValueError):
    """图片不可用，调用方应降级为不写 image 字段。"""


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
    host = _host_of(url)
    for label in host.split("."):
        if not label or label in GENERIC_LABELS:
            continue
        slug = re.sub(r"[^a-z0-9_-]", "", label)
        if slug:
            return slug[:MAX_SLUG_LEN]
    cleaned = re.sub(r"[^a-z0-9_-]", "", host)[:MAX_SLUG_LEN]
    return cleaned or hashlib.sha1(host.encode("utf-8")).hexdigest()[:8]


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
        raise AvatarError(f"非法图片尺寸 {width}x{height}。")
    if mode == "none":
        return width, height
    if mode not in ("width", "height", "longest"):
        raise AvatarError(f"未知的缩放模式 {mode}。")
    if px <= 0:
        raise AvatarError("目标像素必须为正整数。")

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
class ConvertedAvatar:
    data: bytes
    size: tuple[int, int]


def to_webp(
    raw: bytes,
    mode: str,
    px: int,
    quality: int,
    upscale: bool = False,
) -> ConvertedAvatar:
    if not raw:
        raise AvatarError("图片数据为空。")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:  # Pillow 的异常类型太多，统一转成插件自己的错误
        raise AvatarError(f"无法解码图片：{e.__class__.__name__}") from e

    im = ImageOps.exif_transpose(im)
    im = im.convert("RGBA" if "A" in im.getbands() else "RGB")

    target = compute_target_size(im.width, im.height, mode, px, upscale)
    if target != (im.width, im.height):
        im = im.resize(target, RESAMPLER)

    buf = io.BytesIO()
    im.save(buf, format="WEBP", quality=int(quality), method=6)
    return ConvertedAvatar(buf.getvalue(), target)


def validate_content_type(content_type: str, declared_length: int) -> None:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if not ct.startswith("image/"):
        raise AvatarError(f"头像地址返回的不是图片（{ct or '未知类型'}）。")
    if declared_length and declared_length > MAX_DOWNLOAD_BYTES:
        raise AvatarError(
            f"头像过大（{declared_length // 1024 // 1024}MB），上限 8MB。"
        )


async def fetch_image(client: aiohttp.ClientSession, url: str, timeout_s: float = 15.0) -> bytes:
    """用调用方传入的 ClientSession 下载头像。"""
    headers = {"User-Agent": "astrbot-plugin-friend-push/1.0"}
    try:
        async with asyncio.timeout(timeout_s):
            async with client.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_s)
            ) as resp:
                if resp.status != 200:
                    raise AvatarError(f"下载头像失败：HTTP {resp.status}。")
                validate_content_type(
                    resp.headers.get("Content-Type", ""),
                    int(resp.headers.get("Content-Length") or 0),
                )
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buf += chunk
                    if len(buf) > MAX_DOWNLOAD_BYTES:
                        raise AvatarError("头像超过 8MB 上限。")
                return bytes(buf)
    except AvatarError:
        raise
    except TimeoutError as e:
        raise AvatarError(f"下载头像超时（{timeout_s:.0f}s）。") from e
    except Exception as e:
        raise AvatarError(f"下载头像出错：{e.__class__.__name__}") from e
