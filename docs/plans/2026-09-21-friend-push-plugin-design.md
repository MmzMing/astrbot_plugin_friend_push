# astrbot_plugin_friend_push 设计文档

日期：2026-09-21
状态：已批准

## 1. 目标与范围

在 AstrBot 里用一条聊天命令，把新的友链写进 `MmzMing/my-blog` 仓库的
`src/config/friendsConfig.ts`，并把头像转成 webp 一并提交到
`public/assets/images/friends/`。

范围限定：

- 使用者只有站长本人，不做申请人自助提交、不做审核队列。
- 只做**新增**一条友链。删除、禁用、改权重、改标签不在本插件范围内，去 GitHub 手动改。
- 只支持 QQ 平台触发。

非目标：批量导入、友链有效性巡检、站点信息抓取补全。

## 2. 上游事实（已核实）

`src/types/config.ts`：

```ts
export type FriendLink = {
	title: string;   // 友链标题
	imgurl: string;  // 头像图片URL
	desc: string;    // 友链描述
	siteurl: string; // 友链地址
	image?: string;  // 封面图片路径（可选，不填则卡片显示无图形态）
	tags?: string[]; // 标签数组（可选）
	weight: number;  // 权重，越大越靠前
	enabled: boolean;
};
```

`src/config/friendsConfig.ts` 里 `export const friendsConfig: FriendLink[] = [` 目前有 46 条，
Tab 缩进，字段书写顺序固定为 `title, imgurl, desc, [image], [tags], weight, enabled`，
数组以 `];` 结束，后面还有 `getEnabledFriends` 函数——**插入点必须在这段之前**。

`public/assets/images/friends/` 下已有 45 个 `.webp`，命名基本取自站点域名 slug
（`2x.webp`、`karina.webp`、`astrvow.webp`）。

仓库里 `friendsPageConfig.applyLink` 已指向 `.github/ISSUE_TEMPLATE/friend-link.yml`，
即现存一条人工申请通道；本插件不改动它，也不与之冲突。

## 3. 目录结构

```
astrbot_plugin_friend_push/
├── metadata.yaml         # 插件元信息（name/author/desc/version）
├── _conf_schema.json     # 配置项 schema，AstrBot 据此渲染 WebUI 配置表单
├── main.py               # 命令注册 / 参数解析 / 确认状态机 / 白名单
├── friends_io.py         # 锚点定位插入、TS 对象渲染、查重、unified diff
├── avatar.py             # 下载 imgurl → Pillow → webp bytes、slug 推导
├── github_api.py         # 薄封装 GET/PUT Contents API、SHA 处理
├── requirements.txt      # 仅 Pillow（见第 14 节，AstrBot 本体已带）
└── README.md             # PAT 权限说明 + 命令示例
```

拆分理由：`friends_io.py` 和 `avatar.py` 是不依赖 AstrBot 的纯逻辑；
AstrBot 的框架耦合全部关在 `main.py` 里。

网络请求用 `aiohttp`（AstrBot 本体依赖，无需新增）。

## 4. 配置项

| 键 | 默认值 | 说明 |
|---|---|---|
| `github_token` | 空 | fine-grained PAT，仅 `my-blog` 单仓库 `contents: write` |
| `repo` | `MmzMing/my-blog` | |
| `branch` | `master` | |
| `config_path` | `src/config/friendsConfig.ts` | |
| `image_dir` | `public/assets/images/friends` | |
| `allowed_qqids` | 空 | 逗号分隔 QQ 号；**为空时拒绝所有人**（安全默认） |
| `default_weight` | `5` | |
| `default_tags` | `Blog` | 逗号分隔 |
| `image_enabled` | `true` | 关掉则完全不写 `image` 字段 |
| `image_resize_mode` | `width` | `width` / `height` / `longest` / `none` |
| `image_target_px` | `900` | 一个数服务所有模式 |
| `image_upscale` | `false` | 原图小于目标尺寸时不放大 |
| `image_webp_quality` | `82` | |

只有一个 `image_target_px` 而不是 width/height/longest 三个数字，是为了消除
"模式选 width 却改了 height 那个数"这类自相矛盾的配错。

## 5. 命令语法

```
/友链 <标题>|<描述>|<站点URL>|<头像URL>[|tags=Blog,星图][|weight=5][|slug=xxx][|px=1200][|resize=none][|noimg]
```

- 前四个位置参数必填，按固定顺序。
- `key=value` 后缀顺序任意，只影响本次提交，不改配置默认值。
- `/友链 确认` 执行提交；`/友链 取消` 丢弃 pending。
- 参数里出现裸 `|` 不支持（标题/描述里的竖线在解析前先按 `key=` 前缀切分，避免误切）。

## 6. 数据流

1. 白名单校验，最先做，避免无谓 API 调用。不在名单内直接静默忽略。
2. 解析并校验参数：URL 可解析、`weight`/`px` 是正整数。
3. `GET /repos/{repo}/contents/{config_path}?ref={branch}` → base64 内容 + `sha`。
4. 查重：`siteurl` 归一化（协议/大小写/尾斜杠）后已存在 → 拒绝并提示现有条目下标。
5. 图片处理（`image_enabled` 且非 `noimg`）：下载 → 校验 → 缩放 → 编码 webp →
   算 slug（域名推导，撞名加数字后缀）。
6. `friends_io.py` 渲染新对象（Tab 缩进、字段顺序与现有一致、中文不转义）→
   锚点插入 → 得到新全文。
7. 回复 unified diff + 一句话摘要，会话进 pending。
8. 收到 `确认` → PUT 图片（base64）→ PUT ts 文件（带步骤 3 的 `sha`）。
9. 回复两条 commit 的 HTML URL。

第 8 步顺序是先图后文件：文件是最终生效的那一笔，图先成功才能让 `image` 字段指向一个
真实存在的对象。反过来若图失败而文件先落地，仓库里会留下指向不存在文件的 `image`。

## 7. 锚点插入算法

不做整文件重新序列化——那会重排全文件、丢掉所有中文注释、产出上百行 diff。

1. 定位 `export const friendsConfig` 之后的第一个 `[`。
2. 从该位置做括号配对扫描，跳过字符串字面量（含转义），找到配对的 `]`。
3. 在 `]` 之前回退定位最后一个 `}`，在其后插入 `,\n` + 新对象文本。
4. 原文件其余字节逐字节不变。

空数组 `[]` 是显式分支：直接插入元素本身，不加前导逗号。

## 8. 图片处理链

```
download(15s 超时, 8MB 上限, content-type 必须 image/*)
  → Image.open → ImageOps.exif_transpose   # 修正手机图 EXIF 旋转
  → mode 转换: CMYK/P → RGB, 保留 RGBA alpha
  → 按 image_resize_mode 算目标尺寸
      width:   h = round(orig_h * px / orig_w)   # 原图已窄于 px 且不 upscale 则跳过
      height:  w = round(orig_w * px / orig_h)
      longest: 长边缩到 px
      none:    原尺寸
  → resize(..., LANCZOS)
  → save(format="WEBP", quality=image_webp_quality, method=6)
```

失败（下载超时、非图片、Pillow 打不开、编码失败）时**不追问**：以不含 `image` 字段的
形式继续进 diff，并在摘要里写明降级原因。友链本身仍提交成功。

## 9. 错误处理

| 情况 | 行为 |
|---|---|
| 401 / 403 | 明确提示 token 无效或权限不足，不重试，回复与日志全程 mask token |
| 404 | 提示 `repo` / `config_path` / `branch` 配置有误 |
| 409 SHA 冲突 | **不覆盖、不自动重试**，提示"文件已被改动，请重新执行命令" |
| 参数缺失/非法 | 回复命令语法示例，不进入 API 调用 |
| siteurl 重复 | 拒绝，提示现有条目位置 |
| 图片处理失败 | 降级为不写 `image`，继续提交 |
| pending 过期(30min) | 提示重新执行 |
| 同会话并发两个提交 | 全局串行锁 + 单会话仅允许一个 pending |

409 不自动重试是刻意的：SHA 冲突意味着有人在网页端同时改了文件，自动重读再写会静默丢掉
别人的改动。

## 10. 待确认状态

内存字典 `{session_key: PendingOp}`，30 分钟过期，进程重启即失效。
不落盘到 AstrBot `data/` 目录：为"重启不丢一次未确认的提交"引入序列化和清理逻辑，
对站长自用工具是过度设计；重发一条命令即可。

## 11. 验证

开发期曾用 108 个 pytest 用例覆盖下列行为，后按站长要求从仓库移除，插件目录只保留
运行时代码。改动这几处逻辑时，请把清单里的场景重新人工验证一遍：

- `friends_io`：锚点插入（空数组 / 末元素无尾逗号 / CRLF / 标题含 emoji 与引号 /
  中文不转义 / 括号出现在字符串或注释里）、其余字节不变、diff 只含新增行
- `avatar`：slug 推导与撞名加后缀、四种缩放模式的尺寸计算、不放大分支、
  P/CMYK/L/RGBA/JPEG 输入、非法图片报错
- `main`：参数解析（竖线切分、`key=value` 后缀、非法输入）、白名单、预览、
  查重拒绝、头像降级、确认时先图后文件、409 不重复提交、取消与超时
- `github_api`：base64 读写、401/403/404/409/422 分类、错误信息不含 token

`src/config/friendsConfig.ts` 曾被原样下载为 fixture 做过真实文件回归，且插入结果用
Node 24 直接执行验证过 TS 语法。目标仓库结构调整后，这两项需要重做。

真机验证由站长在本地 AstrBot 加载完成，建议先把 `repo` 配置项指向一个 fork 试跑。

## 12. 已知风险

原风险"本机未安装 AstrBot，官方文档未给出装饰器签名"已通过直接阅读
`astrbot==4.28.1` 包源码消除，结论见第 14 节。

残留风险：`event.get_sender_id()` 在 QQ 官方机器人（`qq_official`）通道下返回的是
平台侧 openid 而非 QQ 号，白名单需要按实际通道的 ID 填写。NapCat/OneBot（`aiocqhttp`）
通道返回的就是 QQ 号。

## 13. 安全

- token 只从配置项读，绝不回显，日志 mask。
- 建议 fine-grained PAT 限定单仓库 + 仅 `contents: write`，不给 classic token。
- 白名单为空时拒绝所有请求，而不是放开。
- commit message 固定前缀 `feat(friends): add <title> via astrbot`，便于事后筛出机器人提交。

## 14. AstrBot 4.28.1 API 核实结果

来源：`pip download astrbot==4.28.1` 后直接读包内源码，不是文档转述。

| 事项 | 结论 | 出处 |
|---|---|---|
| 插件类 | 继承 `astrbot.api.star.Star` 即被自动识别注册；`@register(...)` 装饰器已标记 DEPRECATED，不用 | `core/star/base.py:__init_subclass__`、`core/star/register/star.py` |
| 实例化 | `metadata.star_cls_type(context=self.context, config=plugin_config)`，`TypeError` 时退化为只传 `context` | `core/star/star_manager.py:1227` |
| 读配置 | `self.config` 是 `AstrBotConfig`（`dict` 子类），键即 schema 顶层键；缺键时按 schema 的 `default` 自动补齐并回写 | `core/config/astrbot_config.py` |
| 配置 schema 文件 | 文件名必须是 **`_conf_schema.json`**（JSON，非 YAML），字段 `type` / `description` / `default` | `star_manager.py:212,1158-1170` |
| 支持的 `type` | `int float bool string text list file object template_list dict` | `core/config/default.py:DEFAULT_VALUE_MAP` |
| 命令注册 | `from astrbot.api.event import filter` → `@filter.command("友链")`，对应 `register_command(command_name, sub_command, alias, **kwargs)` | `api/event/filter/__init__.py`、`register/star_handler.py` |
| 参数传递 | `CommandFilter.filter` 先 `re.sub(r"\s+", " ", msg)` 归一空白、去掉指令名，再按空格切分并按 handler 签名转换；`GreedyStr` 注解收集剩余全部文本 | `core/star/filter/command.py:186-215` |
| 唤醒约束 | 受 `wake_prefix` 制约，且 `event.is_at_or_wake_command` 必须为真，即用户需发 `/友链 ...` | `command.py:186`、`waking_check/stage.py` |
| 回复 | `return event.plain_result(text)`，或 `await event.send(MessageChain()...)` | `core/platform/astr_message_event.py:404` |
| 发送者 ID | `event.get_sender_id()`、`event.get_group_id()`、`event.is_admin()` | `astr_message_event.py:195-268` |
| 现成权限过滤器 | `@filter.permission_type(PermissionType.ADMIN)` 走 AstrBot 全局 admins 配置——本插件不用，改用自有 QQ 白名单以符合第 4 节设计 | `core/star/filter/permission.py` |
| 持久化 KV | `self.put_kv_data / get_kv_data`（mixin 已具备）——按第 10 节决定不使用 | `core/utils/plugin_kv_store.py` |
| 数据目录 | `StarTools.get_data_dir(plugin_name)` | `core/star/star_tools.py:244` |
| 依赖 | AstrBot 自带 `aiohttp>=3.11.18`、`pillow>=11.2.1`、`httpx`，因此本插件零新增运行时依赖 | wheel `METADATA` |

实现环境限制：`pip install astrbot` 因其传递依赖 `aiocqhttp` 需要现场编译而无法在本机
完成，所以 `main.py` 与 AstrBot 框架的接线（唤醒、`GreedyStr` 实参、白名单 ID 取值）
只能在真机联调时确认。

