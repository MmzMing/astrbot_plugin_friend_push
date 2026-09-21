import pytest

from friends_io import (
    AnchorError,
    FriendLink,
    find_duplicate,
    image_paths,
    insert_entry,
    make_diff,
    normalize_siteurl,
    render_entry,
)


def test_image_paths_drops_public_prefix_from_url():
    repo, url = image_paths("public/assets/images/friends", "fqzlr")
    assert repo == "public/assets/images/friends/fqzlr.webp"
    assert url == "/assets/images/friends/fqzlr.webp"


def test_image_paths_keeps_non_public_prefix():
    assert image_paths("/src/assets/friends/", "x")[1] == "/src/assets/friends/x.webp"


def link(**kw):
    base = {
        "title": "New Blog",
        "imgurl": "https://new.example.com/avatar.png",
        "desc": "hello",
        "siteurl": "https://new.example.com/",
        "weight": 5,
    }
    base.update(kw)
    return FriendLink(**base)


SINGLE = """import type { FriendLink } from "../types/config";

export const friendsConfig: FriendLink[] = [
\t{
\t\ttitle: "Old",
\t\timgurl: "https://old.example.com/a.png",
\t\tdesc: "old entry",
\t\tsiteurl: "https://old.example.com/",
\t\ttags: ["Blog"],
\t\tweight: 5,
\t\tenabled: true,
\t},
];

// 获取启用的友链并进行排序
export const getEnabledFriends = (): FriendLink[] => {
\tconst friends = friendsConfig.filter((friend) => friend.enabled);
\treturn friends.sort((a, b) => b.weight - a.weight);
};
"""


def test_render_entry_field_order():
    text = render_entry(link(image="/assets/images/friends/new.webp", tags=("Blog",)))
    lines = [ln.strip().rstrip(",") for ln in text.splitlines()]
    keys = [ln.split(":")[0] for ln in lines if ":" in ln and not ln.startswith("//")]
    assert keys == [
        "title",
        "imgurl",
        "desc",
        "siteurl",
        "image",
        "tags",
        "weight",
        "enabled",
    ]


def test_render_entry_omits_absent_optional_fields():
    text = render_entry(link())
    assert "image:" not in text
    assert "tags:" not in text


def test_render_entry_includes_siteurl():
    text = render_entry(link(siteurl="https://x.example.com/y/"))
    assert '\t\tsiteurl: "https://x.example.com/y/",' in text


def test_render_entry_uses_tabs_and_wrapped_braces():
    text = render_entry(link())
    lines = text.splitlines()
    assert lines[0] == "\t{"
    assert lines[-1] == "\t},".rstrip(",")
    assert all(ln.startswith("\t") for ln in lines)


def test_render_entry_keeps_emoji_and_escapes_quotes():
    text = render_entry(link(title='他说的"好"🍊', desc="back\\slash"))
    assert "🍊" in text
    assert '\\"好\\"' in text
    assert "back\\\\slash" in text


def test_render_entry_boolean_and_int_literals():
    text = render_entry(link(enabled=False, weight=9))
    assert "weight: 9," in text
    assert "enabled: false," in text


def test_insert_preserves_every_other_byte():
    out = insert_entry(SINGLE, link())
    assert out.startswith(SINGLE.split("];")[0].rsplit("\t},", 1)[0])
    assert SINGLE.split("];")[1] in out
    # 未新增的部分必须逐字节存在
    assert 'title: "Old"' in out
    assert "getEnabledFriends" in out


def test_insert_appends_after_last_entry_with_trailing_comma():
    out = insert_entry(SINGLE, link(title="Zzz"))
    assert out.index('title: "Old"') < out.index('title: "Zzz"')
    assert out.count("\t},\n") == 2
    assert "],\n]" not in out


def test_insert_into_empty_array():
    src = "export const friendsConfig: FriendLink[] = [];\n"
    out = insert_entry(src, link())
    assert "[\t{" in out
    assert ",," not in out
    assert out.endswith("];\n")


def test_insert_into_multiline_empty_array():
    src = "export const friendsConfig: FriendLink[] = [\n];\n"
    out = insert_entry(src, link())
    assert out.count("[") == 2  # 类型注解里的 [] 不算：FriendLink[] 与数组本体
    assert "},\n," not in out


def test_insert_when_last_entry_lacks_trailing_comma():
    src = (
        "export const friendsConfig: FriendLink[] = [\n"
        '\t{\n\t\ttitle: "A",\n\t\timgurl: "u",\n\t\tdesc: "d",\n'
        '\t\tsiteurl: "https://a/",\n\t\tweight: 5,\n\t\tenabled: true\n\t}\n'
        "];\n"
    )
    out = insert_entry(src, link())
    assert "\t},\n" in out
    assert "\t}\n" in out  # 新条目成为新的末元素，仍无尾逗号也可接受
    assert ",," not in out


def test_insert_with_crlf_keeps_crlf():
    src = SINGLE.replace("\n", "\r\n")
    out = insert_entry(src, link())
    assert "\r\n\t\ttitle:" in out
    assert "\n" not in out.replace("\r\n", "")  # 不引入裸 LF
    assert out.count("\r\n") > src.count("\r\n")


def test_brackets_inside_strings_are_ignored():
    src = (
        "export const friendsConfig: FriendLink[] = [\n"
        '\t{\n\t\ttitle: "weird ] } {[",\n\t\timgurl: "u",\n\t\tdesc: "d",\n'
        '\t\tsiteurl: "https://a/",\n\t\tweight: 5,\n\t\tenabled: true,\n\t},\n'
        "];\n"
    )
    out = insert_entry(src, link())
    assert out.count("\t{") == 2
    assert "]" in out


def test_brackets_inside_comments_are_ignored():
    src = (
        "export const friendsConfig: FriendLink[] = [\n"
        "\t// 待整理 [占位] {注意}\n"
        '\t{\n\t\ttitle: "A",\n\t\timgurl: "u",\n\t\tdesc: "d",\n'
        '\t\tsiteurl: "https://a/",\n\t\tweight: 5,\n\t\tenabled: true,\n\t},\n'
        "];\n"
    )
    out = insert_entry(src, link())
    assert "// 待整理 [占位] {注意}" in out
    assert out.count("\t{") == 2


def test_missing_anchor_raises():
    with pytest.raises(AnchorError):
        insert_entry("export const nothing = 1;\n", link())


def test_normalize_siteurl_variants():
    a = normalize_siteurl("HTTPS://Blog.Example.COM/")
    for b in [
        "https://blog.example.com",
        "http://blog.example.com/",
        "https://blog.example.com/index.html",
        "blog.example.com/",
    ]:
        assert a == normalize_siteurl(b)


def test_normalize_siteurl_keeps_distinct_paths():
    assert normalize_siteurl("https://a.com/x") != normalize_siteurl("https://a.com/y")


def test_find_duplicate_reports_index():
    assert find_duplicate(SINGLE, "https://old.example.com") == 0
    assert find_duplicate(SINGLE, "https://nope.example.com") is None


def test_find_duplicate_on_empty_array():
    assert find_duplicate("export const friendsConfig: FriendLink[] = [];", "https://a/") is None


def test_find_duplicate_after_insert():
    out = insert_entry(SINGLE, link(siteurl="https://new.example.com/"))
    assert find_duplicate(out, "new.example.com") == 1


def test_make_diff_shows_added_entry_only():
    out = insert_entry(SINGLE, link())
    diff = make_diff(SINGLE, out)
    assert '+\t\ttitle: "New Blog",' in diff.replace("\r\n", "\n")
    assert '-\t\ttitle: "Old",' not in diff
    assert diff.count("+") >= 8
