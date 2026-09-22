"""GitHub API 薄封装：Contents API 读文件 / 列目录，Git Data API 单提交写多文件。"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

API_ROOT = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RepoFile:
    content: str
    sha: str


@dataclass(frozen=True)
class CommitResult:
    url: str
    sha: str


class ContentsAPI:
    def __init__(self, client: aiohttp.ClientSession, token: str, repo: str, branch: str):
        if not (token or "").strip():
            raise GitHubError("未配置 github_token。")
        if not REPO_RE.match((repo or "").strip()):
            raise GitHubError("repo 配置项格式应为 owner/name。")
        if not (branch or "").strip():
            raise GitHubError("未配置 branch。")
        self.client = client
        self.token = token.strip()
        self.repo = repo.strip().strip("/")
        self.branch = branch.strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrbot-plugin-friend-push",
        }

    def _error(self, status: int, message: str) -> GitHubError:
        if status in (401, 403) and "rate limit" not in message.lower():
            return GitHubError(
                f"GitHub token 无效或权限不足（{status}）。"
                "需要 fine-grained PAT 且对该仓库有 Contents: Write 权限。",
                status,
            )
        if status == 403:
            return GitHubError("触发了 GitHub API 限流，请稍后再试。", status)
        if status == 404:
            return GitHubError(
                "仓库、分支或文件路径不存在（404），请检查 repo / branch / config_path 配置项。",
                status,
            )
        if status == 409:
            return GitHubError(
                "分支在你读取之后已被他人改动（409）。本次未提交任何内容，请重新执行命令。",
                status,
            )
        if status == 422:
            return GitHubError(
                f"提交未创建（422，常见原因：分支在你读取之后已被他人改动）。"
                f"请重新执行命令。详情：{message[:200]}",
                status,
            )
        return GitHubError(f"GitHub API 返回 {status}：{message[:200]}", status)

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> tuple[int, dict | list]:
        url = f"{API_ROOT}{path}"
        try:
            async with self.client.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                status = resp.status
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = {"message": text}
                if status >= 400:
                    message = data.get("message", "") if isinstance(data, dict) else ""
                    raise self._error(status, str(message))
                return status, data
        except GitHubError:
            raise
        except TimeoutError as e:
            raise GitHubError("GitHub API 请求超时（30s）。") from e
        except aiohttp.ClientError as e:
            raise GitHubError(f"无法连接 GitHub：{e.__class__.__name__}") from e

    def _contents_path(self, path: str) -> str:
        return f"/repos/{self.repo}/contents/{quote(path.strip('/'), safe='/')}"

    async def get_file(self, path: str) -> RepoFile:
        _, data = await self._call(
            "GET", self._contents_path(path), params={"ref": self.branch}
        )
        if not isinstance(data, dict) or "content" not in data:
            raise GitHubError(f"{path} 不是可直接读取的文件。")
        try:
            raw = base64.b64decode(data["content"])
            content = raw.decode("utf-8")
        except Exception as e:
            raise GitHubError(f"解码 {path} 失败：{e.__class__.__name__}") from e
        return RepoFile(content=content, sha=data["sha"])

    async def commit_many(
        self, files: list[tuple[str, bytes]], *, message: str, base_sha: str
    ) -> CommitResult:
        """把多个文件写进同一个提交（Git Data API）。

        base_sha 是预览阶段读到的提交 SHA；若此后分支头已移动，
        更新 ref 会失败，本次不会创建任何提交，不会覆盖他人改动。
        """
        _, base = await self._call("GET", f"/repos/{self.repo}/git/commits/{base_sha}")
        entries = []
        for path, data in files:
            _, blob = await self._call(
                "POST",
                f"/repos/{self.repo}/git/blobs",
                body={
                    "content": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64",
                },
            )
            entries.append(
                {
                    "path": path.strip("/"),
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }
            )
        _, tree = await self._call(
            "POST",
            f"/repos/{self.repo}/git/trees",
            body={"base_tree": base["tree"]["sha"], "tree": entries},
        )
        _, commit = await self._call(
            "POST",
            f"/repos/{self.repo}/git/commits",
            body={"message": message, "tree": tree["sha"], "parents": [base_sha]},
        )
        await self._call(
            "PATCH",
            f"/repos/{self.repo}/git/refs/heads/{quote(self.branch, safe='')}",
            body={"sha": commit["sha"], "force": False},
        )
        return CommitResult(
            url=str(commit.get("html_url", "")), sha=str(commit.get("sha", ""))
        )

    async def head_sha(self) -> str:
        _, data = await self._call(
            "GET", f"/repos/{self.repo}/commits/{quote(self.branch, safe='/')}"
        )
        if not isinstance(data, dict) or "sha" not in data:
            raise GitHubError("无法读取分支头提交。")
        return str(data["sha"])

    async def list_dir(self, path: str) -> set[str]:
        try:
            _, data = await self._call(
                "GET", self._contents_path(path), params={"ref": self.branch}
            )
        except GitHubError as e:
            if e.status == 404:
                return set()
            raise
        if not isinstance(data, list):
            raise GitHubError(f"{path} 不是目录。")
        return {
            str(item.get("name"))
            for item in data
            if isinstance(item, dict) and item.get("name")
        }

