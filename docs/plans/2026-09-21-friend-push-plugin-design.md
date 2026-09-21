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
├── metadata.yaml         # 插件元信息 + 配置项 schema
├── main.py               # 命令注册 / 参数解析 / 确认状态机 / 白名单
├── friends_io.py         # 锚点定位插入、TS 对象渲染、查重、unified diff
├── avatar.py             # 下载 imgurl → Pillow → webp bytes、slug 推导
├── github_api.py         # 薄封装 GET/PUT Contents API、SHA 处理
├── requirements.txt      # Pillow
└── README.md             # PAT 权限说明 + 命令示例
```

拆分理由：`friends_io.py` 和 `avatar.py` 是不依赖 AstrBot 的纯逻辑，可以直接 pytest；
AstrBot 的框架耦合全部关在 `main.py` 里。

网络请求复用 AstrBot 自带的异步 HTTP 客户端，不引入 `requests`。

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

## 11. 测试

pytest 只测纯函数，不打真实网络、不需要 token：

- `friends_io`：锚点插入（空数组 / 末元素无尾逗号 / CRLF / 标题含 emoji 与引号 /
  中文不转义 / 括号出现在字符串里）、查重归一化、diff 生成
- `avatar`：slug 推导与撞名加后缀、四种缩放模式的尺寸计算、不放大分支
- `main` 的参数解析：竖线切分、`key=value` 后缀、非法输入

GitHub API 层用 mock。真机由站长在本地 AstrBot 加载验证，建议先把 `repo` 配置项指向
一个 fork 试跑。

## 12. 已知风险

本机未安装 AstrBot，官方 `dev/star/plugin-new.html` 页面未给出装饰器签名与配置读取的确切
代码。实现第一步必须先拉官方 helloworld 插件模板核对 `register_command`、配置读取、
`reply` 的实际 API，再写 `main.py`，不凭记忆猜框架接口。

## 13. 安全

- token 只从配置项读，绝不回显，日志 mask。
- 建议 fine-grained PAT 限定单仓库 + 仅 `contents: write`，不给 classic token。
- 白名单为空时拒绝所有请求，而不是放开。
- commit message 固定前缀 `feat(friends): add <title> via astrbot`，便于事后筛出机器人提交。
