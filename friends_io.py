"""friendsConfig.ts 的定点读写：锚点插入、查重、diff。

刻意不做整文件反序列化再输出——那会重排格式并丢掉中文注释。这里只在最后一个
数组元素之后插入新元素，其余字节逐字节保留。
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

ANCHOR = re.compile(
    r"export\s+const\s+friendsConfig\s*:\s*FriendLink\[\]\s*=\s*(\[)", re.ASCII
)

SITEURL_IN_ARRAY = re.compile(r'siteurl\s*:\s*"((?:\\.|[^"\\])*)"')


class AnchorError(ValueError):
    """无法在文件里定位友链数组，宁可不改也不要猜。"""


@dataclass(frozen=True)
class FriendLink:
    title: str
    imgurl: str
    desc: str
    siteurl: str
    weight: int = 5
    enabled: bool = True
    image: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_entry(link: FriendLink, newline: str = "\n") -> str:
    lines = ["\t{"]
    lines.append(f"\t\ttitle: {_js(link.title)},")
    lines.append(f"\t\timgurl: {_js(link.imgurl)},")
    lines.append(f"\t\tdesc: {_js(link.desc)},")
    lines.append(f"\t\tsiteurl: {_js(link.siteurl)},")
    if link.image:
        lines.append(f"\t\timage: {_js(link.image)},")
    if link.tags:
        joined = ", ".join(_js(t) for t in link.tags)
        lines.append(f"\t\ttags: [{joined}],")
    lines.append(f"\t\tweight: {_js(int(link.weight))},")
    lines.append(f"\t\tenabled: {_js(bool(link.enabled))},")
    lines.append("\t}")
    return newline.join(lines)


def _array_span(src: str) -> tuple[int, int]:
    """返回友链数组字面量的 (开括号下标, 对应闭括号下标)。"""
    m = ANCHOR.search(src)
    if not m:
        raise AnchorError("未找到 `export const friendsConfig: FriendLink[] = [` 锚点。")
    open_idx = m.start(1)

    stack: list[str] = []
    in_str: str | None = None
    escaped = False
    i = open_idx
    while i < len(src):
        c = src[i]
        if in_str is not None:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == in_str:
                in_str = None
            i += 1
            continue
        if src.startswith("//", i):
            nl = src.find("\n", i)
            i = len(src) if nl < 0 else nl + 1
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = len(src) if end < 0 else end + 2
            continue
        if c in "\"'`":
            in_str = c
        elif c in "[{":
            stack.append(c)
        elif c in "]}":
            if not stack:
                raise AnchorError("友链数组括号不配对，文件可能已损坏。")
            stack.pop()
            if not stack:
                return open_idx, i
        i += 1
    raise AnchorError("友链数组没有闭合。")


def _last_entry_end(src: str, open_idx: int, close_idx: int) -> int | None:
    """末元素 `}` 的下标；数组为空时返回 None。"""
    depth = 0
    for i in range(close_idx - 1, open_idx, -1):
        c = src[i]
        if c == "}":
            depth += 1
            if depth == 1:
                return i
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
    return None


def insert_entry(src: str, link: FriendLink) -> str:
    newline = "\r\n" if "\r\n" in src else "\n"
    entry = render_entry(link, newline)
    open_idx, close_idx = _array_span(src)
    last_end = _last_entry_end(src, open_idx, close_idx)
    if last_end is None:
        return src[: open_idx + 1] + entry + src[open_idx + 1 :]
    cut = last_end + 1
    return src[:cut] + f",{newline}" + entry + src[cut:]


def normalize_siteurl(url: str) -> str:
    s = url.strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("?", 1)[0].split("#", 1)[0]
    if not s:
        return ""
    host, _, path = s.partition("/")
    host = re.sub(r"^www\.", "", host)
    path = re.sub(r"/index\.x?html?$", "", "/" + path if path else "")
    path = re.sub(r"/+$", "", path)
    return f"{host}{path}"


def extract_siteurls(src: str) -> list[str]:
    open_idx, close_idx = _array_span(src)
    body = src[open_idx:close_idx]
    return [
        json.loads(f'"{m.group(1)}"') for m in SITEURL_IN_ARRAY.finditer(body)
    ]


def find_duplicate(src: str, siteurl: str) -> int | None:
    target = normalize_siteurl(siteurl)
    if not target:
        return None
    for idx, existing in enumerate(extract_siteurls(src)):
        if normalize_siteurl(existing) == target:
            return idx
    return None


def make_diff(src: str, new_src: str, path: str = "friendsConfig.ts") -> str:
    diff = difflib.unified_diff(
        src.splitlines(),
        new_src.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
        n=3,
    )
    return "\n".join(diff)


def image_paths(image_dir: str, slug: str) -> tuple[str, str]:
    """返回 (仓库内路径, 站点 URL 路径)。Astro 的 public/ 前缀不出现在 URL 里。"""
    d = (image_dir or "").strip("/")
    return f"{d}/{slug}.webp", f"/{re.sub(r'^public/', '', d)}/{slug}.webp"


USAGE = "/友链 标题|描述|站点URL|头像URL[|tags=Blog][|weight=5][|slug=xxx][|px=900][|resize=width][|noimg]"

RESIZE_MODES = ("width", "height", "longest", "none")
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ParseError(ValueError):
    """命令行不合规，尚未产生任何网络请求。"""


@dataclass(frozen=True)
class Submission:
    link: FriendLink
    slug: str | None = None
    resize_mode: str | None = None
    px: int | None = None
    no_image: bool = False


def _require_http_url(value: str, label: str, *, need_dot: bool = False) -> str:
    m = re.match(r"^(https?)://([^/?#]+)", value, re.IGNORECASE)
    if not m:
        raise ParseError(f"{label}需是 http(s) 链接，当前为 `{value}`。")
    if need_dot and "." not in m.group(2).split(":")[0]:
        raise ParseError(f"{label}的域名不完整，当前为 `{value}`。")
    return value


def _require_int(raw: str, label: str, low: int, high: int) -> int:
    try:
        val = int(raw)
    except ValueError:
        raise ParseError(f"{label}必须是整数，当前为 `{raw}`。") from None
    if not low <= val <= high:
        raise ParseError(f"{label}需在 {low}-{high} 之间。")
    return val


def parse_submission(
    text: str,
    *,
    default_weight: int = 5,
    default_tags: tuple[str, ...] = ("Blog",),
) -> Submission:
    """解析 `/友链` 后面的参数串。竖线本身不可出现在字段值里。"""
    parts = [p.strip() for p in (text or "").split("|")]
    if len(parts) < 4:
        raise ParseError(f"参数不足。用法：{USAGE}")

    title, desc, siteurl, imgurl = parts[:4]
    for label, val in zip(("标题", "描述", "站点URL", "头像URL"), parts[:4]):
        if not val:
            raise ParseError(f"缺少{label}。用法：{USAGE}")
    _require_http_url(siteurl, "站点URL", need_dot=True)
    _require_http_url(imgurl, "头像URL")

    weight: int = default_weight
    tags: tuple[str, ...] = default_tags
    slug: str | None = None
    mode: str | None = None
    px: int | None = None
    no_image = False

    for extra in parts[4:]:
        if not extra:
            continue
        key, sep, raw = extra.partition("=")
        key = key.strip().lower()
        raw = raw.strip()
        if not sep:
            if key == "noimg":
                no_image = True
                continue
            raise ParseError(
                f"无法识别的参数 `{extra}`。可用：tags= / weight= / slug= / px= / resize= / noimg"
            )
        if key == "tags":
            parsed = tuple(t.strip() for t in re.split(r"[,，]", raw) if t.strip())
            if not parsed:
                raise ParseError("tags 不能为空。")
            tags = parsed
        elif key == "weight":
            weight = _require_int(raw, "weight", 0, 999)
        elif key == "px":
            px = _require_int(raw, "px", 1, 8192)
        elif key == "slug":
            if not SLUG_RE.match(raw):
                raise ParseError("slug 只能包含字母、数字、下划线和连字符，最长 32 字符。")
            slug = raw
        elif key == "resize":
            if raw not in RESIZE_MODES:
                raise ParseError(f"resize 只能是 {'/'.join(RESIZE_MODES)}。")
            mode = raw
        else:
            raise ParseError(f"未知的参数名 `{key}`。")

    link = FriendLink(
        title=title,
        imgurl=imgurl,
        desc=desc,
        siteurl=siteurl,
        weight=weight,
        enabled=True,
        tags=tags,
    )
    return Submission(link=link, slug=slug, resize_mode=mode, px=px, no_image=no_image)
