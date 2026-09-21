import asyncio
import io
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent / "stubs"))

import main
from github_api import GitHubError, RepoFile

SRC = (Path(__file__).parent / "fixtures" / "friendsConfig.ts").read_text("utf-8")

ARGS = (
    "新站|一句简介|https://newsite.example.com/|https://newsite.example.com/avatar.png"
)


def png_bytes(w=1200, h=600):
    im = Image.new("RGBA", (w, h), (10, 20, 30, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class FakeEvent:
    def __init__(self, sender="10001", session="sess-1"):
        self.sender = sender
        self.session = session

    def get_sender_id(self):
        return self.sender

    def get_session_id(self):
        return self.session

    def plain_result(self, text):
        return text


class FakeAPI:
    def __init__(self, names=("fqzlr.webp",), list_fails=False, file_put_fails=None):
        self.client = None
        self.names = set(names)
        self.list_fails = list_fails
        self.file_put_fails = file_put_fails
        self.puts = []

    async def get_file(self, path):
        return RepoFile(content=SRC, sha="SRC_SHA")

    async def list_dir(self, path):
        if self.list_fails:
            raise GitHubError("目录不存在", 404)
        return set(self.names)

    async def put_bytes(self, path, data, *, message, sha):
        self.puts.append(("bytes", path, len(data), message, sha))
        return _Commit(f"img:{path}")

    async def put_file(self, path, content, *, message, sha):
        if self.file_put_fails:
            raise self.file_put_fails
        self.puts.append(("file", path, len(content), message, sha))
        return _Commit(f"file:{path}")


class _Commit:
    def __init__(self, url):
        self.url = url
        self.sha = url


CONFIG = {
    "github_token": "ghp_x",
    "repo": "MmzMing/my-blog",
    "branch": "master",
    "config_path": "src/config/friendsConfig.ts",
    "image_dir": "public/assets/images/friends",
    "allowed_qqids": "10001,10002",
    "default_weight": 5,
    "default_tags": "Blog",
    "image_enabled": True,
    "image_resize_mode": "width",
    "image_target_px": 900,
    "image_upscale": False,
    "image_webp_quality": 82,
}


def make_star(monkeypatch, api=None, config=None):
    star = main.FriendPushStar(context=None, config=dict(CONFIG if config is None else config))
    monkeypatch.setattr(
        main.FriendPushStar, "_api", lambda self: api or FakeAPI(), raising=False
    )
    monkeypatch.setattr(main, "fetch_image", lambda client, url, timeout_s=15.0: _ok_fetch(url))
    return star


async def _ok_fetch(url):
    return png_bytes()


def run(coro):
    return asyncio.run(coro)


def test_silently_ignores_senders_off_the_whitelist(monkeypatch):
    star = make_star(monkeypatch)
    assert run(star.friend(FakeEvent(sender="99999"), ARGS)) is None


def test_denies_everyone_when_whitelist_is_empty(monkeypatch):
    cfg = dict(CONFIG, allowed_qqids="")
    star = make_star(monkeypatch, config=cfg)
    assert run(star.friend(FakeEvent(), ARGS)) is None


def test_prepare_stores_pending_with_diff_and_image(monkeypatch):
    api = FakeAPI()
    star = make_star(monkeypatch, api)
    reply = run(star.friend(FakeEvent(), ARGS))
    assert "即将新增友链：新站" in reply
    assert "+\t\ttitle: \"新站\"," in reply
    assert "头像：900x450" in reply
    pending = star._pending["sess-1"]
    assert pending.image_path == "public/assets/images/friends/newsite.webp"
    assert pending.submission.link.image == "/assets/images/friends/newsite.webp"
    assert pending.src_sha == "SRC_SHA"
    assert pending.new_src.count("\t{") == SRC.count("\t{") + 1


def test_prepare_rejects_already_listed_site(monkeypatch):
    star = make_star(monkeypatch)
    args = "旧站|简介|https://blog.fqzlr.top/|https://x/a.png"
    reply = run(star.friend(FakeEvent(), args))
    assert "已存在于友链列表第 2 条" in reply
    assert star._pending == {}


def test_prepare_reports_usage_on_bad_args(monkeypatch):
    star = make_star(monkeypatch)
    reply = run(star.friend(FakeEvent(), "只有两个|参数"))
    assert "用法：" in reply and "标题|描述|站点URL|头像URL" in reply


def test_avatar_failure_degrades_to_no_image(monkeypatch):
    from avatar import AvatarError

    async def boom(client, url, timeout_s=15.0):
        raise AvatarError("下载头像失败：HTTP 404。")

    star = make_star(monkeypatch)
    monkeypatch.setattr(main, "fetch_image", boom)
    reply = run(star.friend(FakeEvent(), ARGS))
    assert "下载头像失败：HTTP 404" in reply
    pending = star._pending["sess-1"]
    assert pending.image_data is None
    assert pending.image_path is None
    assert "image:" not in pending.new_src.split("新站")[-1][:400]
    assert "+\t\ttitle:" in pending.diff


def test_slug_collision_gets_numeric_suffix(monkeypatch):
    star = make_star(monkeypatch, api=FakeAPI(names={"newsite.webp"}))
    run(star.friend(FakeEvent(), ARGS))
    assert star._pending["sess-1"].image_path.endswith("/newsite2.webp")


def test_noimg_skips_fetch(monkeypatch):
    star = make_star(monkeypatch)
    called = []

    async def spy(client, url, timeout_s=15.0):
        called.append(url)
        return png_bytes()

    monkeypatch.setattr(main, "fetch_image", spy)
    run(star.friend(FakeEvent(), ARGS + "|noimg"))
    assert called == []
    assert star._pending["sess-1"].image_data is None


def test_confirm_uploads_avatar_before_config(monkeypatch):
    api = FakeAPI()
    star = make_star(monkeypatch, api)
    run(star.friend(FakeEvent(), ARGS))
    reply = run(star.friend(FakeEvent(), "确认"))
    assert "已提交" in reply
    kinds = [p[0] for p in api.puts]
    assert kinds == ["bytes", "file"]
    put = api.puts[1]
    assert put[1] == "src/config/friendsConfig.ts"
    assert put[4] == "SRC_SHA"
    assert put[3] == "feat(friends): add 新站 via astrbot"
    assert "新站" in api.puts[0][3]


def test_confirm_reports_partial_failure_on_conflict(monkeypatch):
    api = FakeAPI(file_put_fails=GitHubError("文件在你读取之后已被他人改动（409）。", 409))
    star = make_star(monkeypatch, api)
    run(star.friend(FakeEvent(), ARGS))
    reply = run(star.friend(FakeEvent(), "确认"))
    assert "提交失败" in reply
    assert "已完成的部分：头像" in reply
    assert star._pending == {}


def test_confirm_without_pending(monkeypatch):
    star = make_star(monkeypatch)
    assert "没有待确认的提交" in run(star.friend(FakeEvent(), "确认"))


def test_expired_pending_is_refused(monkeypatch):
    star = make_star(monkeypatch)
    run(star.friend(FakeEvent(), ARGS))
    star._pending["sess-1"].expires_at = time.monotonic() - 1
    assert "超过 30 分钟" in run(star.friend(FakeEvent(), "确认"))
    assert star._pending == {}


def test_cancel_drops_pending(monkeypatch):
    star = make_star(monkeypatch)
    run(star.friend(FakeEvent(), ARGS))
    assert "已取消" in run(star.friend(FakeEvent(), "取消"))
    assert star._pending == {}


def test_config_errors_are_reported_not_raised(monkeypatch):
    star = main.FriendPushStar(context=None, config=dict(CONFIG, github_token=""))
    monkeypatch.setattr(main, "fetch_image", lambda c, u, timeout_s=15.0: _ok_fetch(u))
    reply = run(star.friend(FakeEvent(), ARGS))
    assert "配置有误" in reply


def test_second_preview_replaces_the_first(monkeypatch):
    star = make_star(monkeypatch)
    run(star.friend(FakeEvent(), ARGS))
    run(star.friend(FakeEvent(), "另一站|简介|https://other.example.com|https://o/a.png"))
    assert star._pending["sess-1"].submission.link.title == "另一站"
