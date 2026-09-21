# astrbot_plugin_friend_push

在 AstrBot 聊天里一条命令把友链写进博客仓库 `src/config/friendsConfig.ts`：
插件下载头像、转成 webp、追加一条友链配置，先在聊天里回一段 diff，你确认后
才通过 GitHub Contents API 提交。

设计文档见 `docs/plans/2026-09-21-friend-push-plugin-design.md`。

## 安装

1. 把本目录放进 AstrBot 的 `data/plugins/` 下（目录名保持合法 Python 标识符）。
2. 生成一个 fine-grained PAT：<https://github.com/settings/tokens> →
   Fine-grained token → 只勾选目标仓库 → Permissions 里只给 **Contents: Read and write**。
   不要用 classic token，也不要给整个账号的写权限。
3. 在 AstrBot WebUI 的插件配置里填 `github_token` 与 `allowed_qqids`，重载插件。

## 配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `github_token` | 空 | fine-grained PAT，仅需目标仓库 Contents 写权限 |
| `repo` | `MmzMing/my-blog` | `owner/name` |
| `branch` | `master` | 提交目标分支 |
| `config_path` | `src/config/friendsConfig.ts` | 友链配置文件 |
| `cover_dir` | `public/assets/images/friends` | 封面图目录，对应 `image` 字段；URL 会自动去掉 `public/` 前缀 |
| `allowed_qqids` | 空 | 允许使用命令的发送者 ID，逗号分隔；**留空拒绝所有人** |
| `default_weight` | `5` | 新友链默认权重 |
| `default_tags` | `Blog` | 默认标签，逗号分隔 |
| `cover_enabled` | `true` | 关闭后不处理消息里的图片，也不写 `image` 字段 |
| `cover_resize_mode` | `width` | `width` / `height` / `longest` / `none` |
| `cover_target_px` | `900` | 目标像素，作用于上面选定的那条边 |
| `cover_upscale` | `false` | 原图小于目标尺寸时是否放大 |
| `cover_webp_quality` | `82` | webp 编码质量 |

## 命令

```
/友链 <标题>|<描述>|<站点URL>|<头像URL>[|tags=Blog,星图][|weight=5][|slug=xxx][|px=1200][|resize=none]
```

```
/友链 番茄主理人|坐而言不如起而行.|https://blog.fqzlr.top/|https://q1.qlogo.cn/g?b=qq&nk=20447289&s=640
/友链 确认
```

- 前四个参数必填、按顺序；`key=value` 后缀顺序任意，只对当次生效。
- **封面图随这条命令一起发**：同一条消息里带一张图片 + 命令文本（PC QQ 可以在输入框里贴图再打字）。没有附图就不写 `image` 字段，卡片走无图形态。
- 竖线本身不能出现在字段值里。
- `/友链 确认` 提交，`/友链 取消` 放弃。预览 30 分钟内有效。
- 命令受 AstrBot 唤醒前缀制约，默认前缀是 `/`。
- 别名：`/友链提交`、`/friendpush`。

## 两个字段的分工

| 配置字段 | 含义 | 插件怎么处理 |
|---|---|---|
| `imgurl` | 站点头像 | 命令第 4 个参数，**原样写入，不下载不转码** |
| `image` | 卡片封面图 | 命令消息里附的图 → 转 webp → 上传 `cover_dir/<slug>.webp` → 写 `/assets/images/friends/<slug>.webp` |

`slug` 取站点主域名标签：`tc.lqay.cn` → `lqay`、`blog.fqzlr.top` → `fqzlr`、
`xfcnl.github.io` → `xfcnl`（`github.io`、`com.cn`、`cq.cn` 这类两段后缀整体看）。
撞名自动加数字后缀；域名结构特殊时用 `slug=` 手工指定。

## 行为要点

- **只追加，不重排**：在数组末元素之后插入，原文件其余字节逐字节保留，中文注释不动。
- **先封面后配置**：封面提交成功才提交配置，避免出现指向不存在文件的 `image` 字段。
- **SHA 冲突就停**：读取后若有人在网页端改了文件，GitHub 返回 409，插件放弃提交而不是覆盖对方改动。
- **封面失败不阻断**：读图或转码失败时降级为不写 `image` 字段，并在预览里说明原因。
- **站点查重**：`siteurl` 归一化（协议、大小写、`www.`、尾斜杠、`index.html`）后重复则拒绝。

## 首次验证

本目录只包含插件运行时代码，不带测试。第一次装载建议先把 `repo` 指向一个 fork 仓库，
跑一遍完整链路：

```
/友链 测试站|一句简介|https://example.com/|https://example.com/avatar.png
/友链 确认
```

重点看四件事：命令能否被唤醒、`allowed_qqids` 填的 ID 与实际通道是否一致（日志里会打印
被拒绝的请求）、预览的 diff 是否只有新增行、fork 仓库里生成的封面 webp 尺寸是否符合
`cover_resize_mode` 的预期。
