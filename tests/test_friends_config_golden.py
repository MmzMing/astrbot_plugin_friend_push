"""对着真实文件跑一遍：仓库里的 friendsConfig.ts 原样作为 fixture。"""

from pathlib import Path

import pytest

from friends_io import (
    FriendLink,
    _array_span,
    _last_entry_end,
    extract_siteurls,
    find_duplicate,
    insert_entry,
    make_diff,
)

SRC = Path(__file__).parent / "fixtures" / "friendsConfig.ts"


@pytest.fixture
def real_src() -> str:
    return SRC.read_text(encoding="utf-8")


@pytest.fixture
def new_link() -> FriendLink:
    return FriendLink(
        title="测试站点 Test",
        imgurl="https://test.example.com/avatar.jpg",
        desc="一个带 emoji 🍊 与\"引号\"的描述",
        siteurl="https://test.example.com/",
        image="/assets/images/friends/test.webp",
        tags=("Blog",),
        weight=5,
    )


def test_real_file_has_anchor(real_src):
    open_idx, close_idx = _array_span(real_src)
    assert real_src[open_idx] == "["
    assert real_src[close_idx] == "]"
    assert len(extract_siteurls(real_src)) > 40


def test_insert_only_touches_the_insertion_point(real_src, new_link):
    open_idx, close_idx = _array_span(real_src)
    cut = _last_entry_end(real_src, open_idx, close_idx) + 1
    out = insert_entry(real_src, new_link)
    assert out.startswith(real_src[:cut])
    assert out.endswith(real_src[cut:])
    assert len(out) > len(real_src)


def test_diff_is_pure_addition(real_src, new_link):
    out = insert_entry(real_src, new_link)
    body = [ln for ln in make_diff(real_src, out).splitlines() if ln[:1] in "+-"]
    removed = [ln for ln in body if ln.startswith("-") and not ln.startswith("---")]
    assert removed == []


def test_entry_count_grows_by_one(real_src, new_link):
    out = insert_entry(real_src, new_link)
    assert len(extract_siteurls(out)) == len(extract_siteurls(real_src)) + 1


def test_existing_entries_are_all_detected(real_src):
    for idx, siteurl in enumerate(extract_siteurls(real_src)[:5]):
        assert find_duplicate(real_src, siteurl) == idx


def test_new_entry_detected_as_duplicate_after_insert(real_src, new_link):
    out = insert_entry(real_src, new_link)
    assert find_duplicate(out, "test.example.com") == len(extract_siteurls(out)) - 1
    assert find_duplicate(real_src, "test.example.com") is None


def test_escaped_title_survives_roundtrip(real_src, new_link):
    out = insert_entry(real_src, new_link)
    assert '\t\ttitle: "测试站点 Test",' in out.replace("\r\n", "\n")
    assert "🍊" in out
    assert '\\"引号\\"' in out
