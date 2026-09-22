"""AstrBot 友链提交插件：把聊天里的一条命令落到博客仓库。"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

from .cover import (
    CoverError,
    load_image_file,
    slug_from_url,
    to_webp,
    unique_slug,
)
from .friends_io import (
    USAGE,
    AnchorError,
    ParseError,
    Submission,
    find_duplicate,
    image_paths,
    insert_entry,
    make_diff,
    parse_submission,
)
from .github_api import ContentsAPI, GitHubError

PENDING_TTL_SECONDS = 30 * 60
MAX_DIFF_LINES = 40
CONFIRM_WORDS = {"确认", "提交", "yes", "ok"}
CANCEL_WORDS = {"取消", "cancel"}
SESSION_GONE = "没有待确认的提交，请重新发送 /友链 ..."
NO_COVER_HINT = "本条消息没有附带图片，将不写 image 封面字段。"


@dataclasses.dataclass
class PendingOp:
    submission: Submission
    new_src: str
    base_sha: str
    config_path: str
    diff: str
    notes: tuple[str, ...] = ()
    cover_path: str | None = None
    cover_data: bytes | None = None
    expires_at: float = 0.0


class FriendPushStar(Star):
    """在聊天里提交博客友链。

    用法：/友链 标题|描述|站点URL|头像URL[|tags=Blog][|weight=5][|slug=xxx][|px=900][|resize=width]
    封面图随这条命令一起发（同一条消息带图）。先看 diff，再发送 /友链 确认 才会写入仓库。
    """

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        # AstrBot 的 Star 基类只存 context，配置得插件自己接下来
        self.config = config or {}
        self._pending: dict[str, PendingOp] = {}
        self._client: aiohttp.ClientSession | None = None
        self._gate = asyncio.Lock()

    # ---------- 配置与权限 ----------

    def _c(self, key: str, fallback):
        raw = (self.config or {}).get(key, fallback)
        if raw is None or raw == "":
            return fallback
        return raw

    def _tags_default(self) -> tuple[str, ...]:
        return tuple(
            t.strip()
            for t in re.split(r"[,，]", str(self._c("default_tags", "Blog")))
            if t.strip()
        )

    def _allowed_ids(self) -> set[str]:
        return {
            s.strip()
            for s in re.split(r"[,，\s]+", str(self._c("allowed_qqids", "")))
            if s.strip()
        }

    def _authorized(self, event: AstrMessageEvent) -> bool:
        allowed = self._allowed_ids()
        sender = event.get_sender_id()
        if not allowed:
            logger.warning("friend_push: allowed_qqids 为空，已拒绝所有请求。")
            return False
        return bool(sender) and sender in allowed

    def _http(self) -> aiohttp.ClientSession:
        if self._client is None or self._client.closed:
            self._client = aiohttp.ClientSession()
        return self._client

    def _api(self) -> ContentsAPI:
        return ContentsAPI(
            self._http(),
            str(self._c("github_token", "")),
            str(self._c("repo", "")),
            str(self._c("branch", "master")),
        )

    # ---------- 命令入口 ----------

    @filter.command("友链", alias={"友链提交", "friendpush"})
    async def friend(
        self, event: AstrMessageEvent, args: GreedyStr
    ) -> MessageEventResult | None:
        if not self._authorized(event):
            return None
        head = (args or "").strip().split(" ", 1)[0]
        async with self._gate:
            if head in CONFIRM_WORDS:
                return await self._confirm(event)
            if head in CANCEL_WORDS:
                self._pending.pop(event.get_session_id(), None)
                return event.plain_result("已取消，未提交任何内容。")
            return await self._prepare(event, args)

    # ---------- 预览 ----------

    async def _prepare(
        self, event: AstrMessageEvent, args: str
    ) -> MessageEventResult:
        try:
            sub = parse_submission(
                args,
                default_weight=int(self._c("default_weight", 5)),
                default_tags=self._tags_default(),
            )
        except ParseError as e:
            return event.plain_result(f"{e}\n\n用法：{USAGE}")

        try:
            api = self._api()
        except GitHubError as e:
            return event.plain_result(f"配置有误：{e}")

        config_path = str(self._c("config_path", "src/config/friendsConfig.ts"))
        try:
            current = await api.get_file(config_path)
            base_sha = await api.head_sha()
        except GitHubError as e:
            return event.plain_result(f"读取 {config_path} 失败：{e}")

        dup = find_duplicate(current.content, sub.link.siteurl)
        if dup is not None:
            return event.plain_result(
                f"该站点已存在于友链列表第 {dup + 1} 条，未做任何修改。"
            )

        notes: list[str] = []
        link = sub.link
        cover_path: str | None = None
        cover_data: bytes | None = None
        cover = self._cover_of(event)
        if cover is None:
            notes.append(NO_COVER_HINT)
        elif not bool(self._c("cover_enabled", True)):
            notes.append("cover_enabled 已关闭，不写 image 封面字段。")
        else:
            link, cover_path, cover_data, extra = await self._build_cover(
                api,
                cover,
                sub.link,
                sub.slug,
                cover_dir=str(self._c("cover_dir", "public/assets/images/friends")),
                resize_mode=str(
                    sub.resize_mode or self._c("cover_resize_mode", "width")
                ),
                px=int(sub.px or self._c("cover_target_px", 900)),
                quality=int(self._c("cover_webp_quality", 82)),
                upscale=bool(self._c("cover_upscale", False)),
            )
            notes.extend(extra)

        try:
            new_src = insert_entry(current.content, link)
        except AnchorError as e:
            return event.plain_result(f"无法安全改写文件：{e}")

        diff = make_diff(current.content, new_src, config_path)
        self._pending[event.get_session_id()] = PendingOp(
            submission=dataclasses.replace(sub, link=link),
            new_src=new_src,
            base_sha=base_sha,
            config_path=config_path,
            diff=_clip(diff),
            notes=tuple(notes),
            cover_path=cover_path,
            cover_data=cover_data,
            expires_at=time.monotonic() + PENDING_TTL_SECONDS,
        )

        head = (
            f"即将新增友链：{link.title}\n"
            f"站点：{link.siteurl}\n"
            f"头像：{link.imgurl}\n"
            f"weight={link.weight} tags={','.join(link.tags)}"
        )
        if notes:
            head += "\n" + "\n".join(notes)
        return event.plain_result(
            f"{head}\n\n{diff}\n\n确认提交请发送：/友链 确认（30 分钟内有效）"
        )

    @staticmethod
    def _cover_of(event: AstrMessageEvent) -> Image | None:
        """封面图只认同一条消息里附带的图片。"""
        for comp in event.get_messages():
            if isinstance(comp, Image):
                return comp
        return None

    async def _build_cover(
        self,
        api: ContentsAPI,
        comp: Image,
        link,
        slug_hint: str | None,
        *,
        cover_dir: str,
        resize_mode: str,
        px: int,
        quality: int,
        upscale: bool,
    ):
        """封面图不可用时降级为不写 image，绝不因此中断提交。"""
        try:
            names = await api.list_dir(cover_dir)
        except GitHubError as e:
            return link, None, None, [f"读取封面图目录失败：{e}；本次不写 image 字段。"]
        taken = {n[: -len(".webp")] for n in names if n.endswith(".webp")}
        slug = unique_slug(slug_hint or slug_from_url(link.siteurl), taken)
        repo_path, url_path = image_paths(cover_dir, slug)
        try:
            path = await comp.convert_to_file_path()
        except Exception as e:  # noqa: BLE001 - 框架的 MediaResolver 抛的类型不固定
            return link, None, None, [
                f"封面图读取失败：{e.__class__.__name__}；本次不写 image 字段。"
            ]
        try:
            conv = to_webp(load_image_file(path), resize_mode, px, quality, upscale)
        except CoverError as e:
            return link, None, None, [f"封面图处理失败：{e}；本次不写 image 字段。"]
        note = (
            f"封面：{conv.size[0]}x{conv.size[1]} webp "
            f"{max(1, len(conv.data) // 1024)}KB → {url_path}"
        )
        return dataclasses.replace(link, image=url_path), repo_path, conv.data, [note]

    # ---------- 提交 ----------

    async def _confirm(self, event: AstrMessageEvent) -> MessageEventResult:
        session_id = event.get_session_id()
        pending = self._pending.get(session_id)
        if pending is None:
            return event.plain_result(SESSION_GONE)
        if pending.expires_at < time.monotonic():
            self._pending.pop(session_id, None)
            return event.plain_result("上一次预览已超过 30 分钟，请重新发送 /友链 ...")

        # 先摘掉，避免半途失败后被重复确认
        self._pending.pop(session_id, None)
        try:
            api = self._api()
        except GitHubError as e:
            return event.plain_result(f"配置有误：{e}")

        title = pending.submission.link.title
        files: list[tuple[str, bytes]] = [
            (pending.config_path, pending.new_src.encode("utf-8"))
        ]
        if pending.cover_data is not None:
            files.insert(0, (str(pending.cover_path), pending.cover_data))
        try:
            commit = await api.commit_many(
                files,
                message=f"🤝 更新友链（Astrbot）: {title}",
                base_sha=pending.base_sha,
            )
        except GitHubError as e:
            return event.plain_result(f"提交失败：{e}")

        return event.plain_result(
            f"✅ 已提交到 {self._c('repo', '')}（{len(files)} 个文件，1 条提交）\n{commit.url}"
        )

    async def terminate(self) -> None:
        self._pending.clear()
        if self._client is not None and not self._client.closed:
            await self._client.close()
            self._client = None


def _clip(diff: str) -> str:
    lines = diff.splitlines()
    if len(lines) <= MAX_DIFF_LINES:
        return diff
    return "\n".join(lines[:MAX_DIFF_LINES]) + f"\n...（diff 已截断，共 {len(lines)} 行）"
