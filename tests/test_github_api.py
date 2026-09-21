import asyncio
import base64
import json

import pytest

from github_api import ContentsAPI, GitHubError

TOKEN = "ghp_SUPERSECRETVALUE123"


class FakeResp:
    def __init__(self, status, body, text=None):
        self.status = status
        self._body = body
        self._text = text if text is not None else json.dumps(body)

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "json": json}
        )
        return self.resp


def run(coro):
    return asyncio.run(coro)


def api(resp):
    client = FakeClient(resp)
    return ContentsAPI(client, TOKEN, "MmzMing/my-blog", "master"), client


def test_rejects_missing_token():
    with pytest.raises(GitHubError):
        ContentsAPI(FakeClient(None), "", "MmzMing/my-blog", "master")


def test_rejects_bad_repo_format():
    with pytest.raises(GitHubError):
        ContentsAPI(FakeClient(None), TOKEN, "my-blog", "master")


def test_get_file_decodes_utf8_and_keeps_sha():
    raw = "export const a = 1; // 中文\n"
    body = {
        "content": base64.b64encode(raw.encode("utf-8")).decode(),
        "sha": "abc123",
    }
    api_, client = api(FakeResp(200, body))
    f = run(api_.get_file("src/config/friendsConfig.ts"))
    assert f.content == raw
    assert f.sha == "abc123"
    call = client.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/repos/MmzMing/my-blog/contents/src/config/friendsConfig.ts")
    assert call["params"] == {"ref": "master"}
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_put_file_sends_base64_and_sha():
    resp = FakeResp(201, {"commit": {"html_url": "https://c/1", "sha": "deadbeef"}})
    api_, client = api(resp)
    out = run(
        api_.put_file(
            "src/config/friendsConfig.ts",
            "新内容",
            message="feat(friends): add x",
            sha="abc123",
        )
    )
    assert out.sha == "deadbeef"
    assert out.url == "https://c/1"
    call = client.calls[0]
    assert call["method"] == "PUT"
    assert base64.b64decode(call["json"]["content"]).decode("utf-8") == "新内容"
    assert call["json"]["sha"] == "abc123"
    assert call["json"]["branch"] == "master"
    assert call["json"]["message"] == "feat(friends): add x"


def test_put_bytes_without_sha_omits_key():
    api_, client = api(FakeResp(201, {"commit": {"html_url": "u", "sha": "s"}}))
    run(api_.put_bytes("public/a.webp", b"\x00\x01", message="m", sha=None))
    assert "sha" not in client.calls[0]["json"]


def test_401_message_does_not_leak_token():
    api_, _ = api(FakeResp(401, {"message": "Bad credentials"}))
    with pytest.raises(GitHubError) as e:
        run(api_.get_file("a.ts"))
    assert TOKEN not in str(e.value)
    assert "token" in str(e.value).lower()


def test_403_rate_limit_is_distinguished():
    api_, _ = api(FakeResp(403, {"message": "API rate limit exceeded for user."}))
    with pytest.raises(GitHubError) as e:
        run(api_.get_file("a.ts"))
    assert "限流" in str(e.value)


def test_404_points_at_config():
    api_, _ = api(FakeResp(404, {"message": "Not Found"}))
    with pytest.raises(GitHubError) as e:
        run(api_.get_file("a.ts"))
    assert "配置" in str(e.value)


def test_409_is_conflict_and_carries_status():
    api_, _ = api(FakeResp(409, {"message": "latest commit could not be merged"}))
    with pytest.raises(GitHubError) as e:
        run(
            api_.put_file(
                "a.ts", "x", message="m", sha="old"
            )
        )
    assert e.value.status == 409
    assert "重新" in str(e.value)


def test_non_json_error_body_still_raises_readable():
    api_, _ = api(FakeResp(500, None, text="<html>500</html>"))
    with pytest.raises(GitHubError) as e:
        run(api_.get_file("a.ts"))
    assert "500" in str(e.value)


def test_list_dir_returns_names():
    api_, _ = api(FakeResp(200, [{"name": "a.webp"}, {"name": "b.webp"}]))
    assert run(api_.list_dir("public/assets/images/friends")) == {"a.webp", "b.webp"}


def test_list_dir_missing_dir_is_empty_set():
    api_, _ = api(FakeResp(404, {"message": "Not Found"}))
    assert run(api_.list_dir("public/assets/images/friends")) == set()


def test_list_dir_on_single_file_raises():
    api_, _ = api(FakeResp(200, {"name": "friendsConfig.ts", "type": "file"}))
    with pytest.raises(GitHubError):
        run(api_.list_dir("src/config/friendsConfig.ts"))
