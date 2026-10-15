# L4D2 QQ Bot

独立运行的 Left 4 Dead 2 QQ 官方机器人，通过 **WebSocket 接收事件、OpenAPI 发送 QQ 原生 Markdown 卡片与动态连接按钮**。目标是完整承接 [l4d2-with-astrbot](https://github.com/LaoYutang/l4d2-with-astrbot) 的业务能力，移除 AstrBot 依赖。

已接入 QQ 官方 WS 与群消息收发，支持服务器查询、总览、地址列表。默认单服查询只回复一条原生卡片，底部挂载本次服务器对应的连接按钮；用户已在真实群确认卡片和按钮可见。可以用 `run` 查询真实服务器，也可以用 `run --demo-data` 在真实 QQ 群中验收模拟数据。语音、工坊和管理操作仍按 roadmap 迁移。

- [开发约定](AGENTS.md)
- [整体规划与功能对照](docs/roadmap.md)
- [架构与 API 约定](docs/architecture.md)
- [本次示例的验收步骤](docs/verification.md)
- [Docker 与 tag 发布](docs/deployment.md)

## QQ 群内测试

```bash
uv sync --frozen --group dev
cp .env.example .env
cp config.example.json config.json
```

Windows 使用 `Copy-Item`；已有私人配置不要覆盖。把 AppID / AppSecret 填入本地 `.env`，服务器地址填入 `config.json`。这些文件被 Git 和 Docker 忽略，不会打进镜像；鉴权时凭据只发给配置的官方 Token 接口，日志不输出凭据或完整消息。

首次不知道群 OpenID 时：

```bash
uv run l4d2-qq-bot discover --duration 120
```

看到“QQ WS 已登录”后，在测试群 @机器人发送“帮助”。此模式不会回复，群 / 成员 OpenID 写入 `.cache/qq-discovery.json`。将需要测试的群 OpenID 填进 `groups[].group_openid`；不能用普通群号替代。只配置本次要启用的群。

```bash
# QQ 真实收发，卡片标记为模拟数据；适合先验收外观。
uv run l4d2-qq-bot run --config config.json --demo-data
# QQ 真实收发，使用配置服务器 / connect 目标的真实 A2S 数据。
uv run l4d2-qq-bot run --config config.json
```

在群内 @机器人发送 `帮助`、`查询1服`、`综合查询`、`服务器列表` 或 `connect IP:Port`。地址列表每页 6 台，`服务器列表 2` 看下一页。默认不发等待提示，避免占用被动回复次数。未配置群、未匹配的服务器名不会回复。修改 `.env` / 配置后重启；不要同时运行两份实例。

默认 `.env` 为 `L4D2_REPLY_FORMAT=markdown`、`L4D2_CONNECT_BUTTONS=true`。卡片正文和按钮在同一个发送请求中提交，不需要图片托管或 Markdown 模板 ID。自定义按钮仍受应用权限限制；若被平台拒绝，会在群回复与日志中明确提示。

动态连接地址在 `config.json` 自定义：

```json
{
  "connect_base_url": "https://steam-connect.laoyutang.cn"
}
```

例如 1服的 address 为 `106.54.214.111:37015`，其按钮跳转 `https://steam-connect.laoyutang.cn/106.54.214.111:37015`；查询 2服时自动使用 2服的地址。每服的 `connect_url` 可覆盖这一规则。基础地址留空时显示填入连接指令按钮，不假装可以直接跳转游戏；Steam 的实际唤起由连接网页和客户端处理。

需要 PNG 回复时可设置 `L4D2_REPLY_FORMAT=image`，这一模式会发送图片和补充连接文字。

## 本地离线预览

安装 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)，然后运行：

```bash
uv sync --frozen --group dev
uv run l4d2-qq-bot validate --config config.example.json
uv run l4d2-qq-bot demo --output artifacts/demo
```

打开 `artifacts/demo/index.html`，对照四张卡片和 `report.json` 验收。示例不会连接 QQ 或游戏服务器，所有示例数据均为模拟数据。`preview --output artifacts/server-card.png` 可以只生成单服卡片。

真实服务器的本地查询，不需要 QQ 凭据：

```bash
uv run l4d2-qq-bot query 你的服务器地址:27015 --output artifacts/live-card.png
```

省略端口时使用 27015。临时查询默认只接受公网地址；查询自己管理的局域网服务器时可显式加 `--allow-private`。A2S 的延迟是运行机器到游戏服务器的往返耗时。

PNG 渲染使用系统中文字体。Windows 自动寻找微软雅黑，Linux 可安装 `fonts-noto-cjk`。也可以用 `L4D2_CARD_FONT_PATH` 指定 TTF / TTC / OTF；字体缺失时会明确报错，避免生成乱码。Docker 已安装 Noto CJK。

## 配置

```bash
# Linux / macOS；Windows 可使用 Copy-Item。
cp .env.example .env
cp config.example.json config.json
```

业务配置使用 `groups[].group_openid` 隔离群组。管理员使用 `admin_openids`；它们是 QQ 官方平台的字符串 OpenID，不能填普通群号 / QQ 号。示例中的占位 OpenID 只用于本地验证。

凭据通过 `.env` / 环境变量提供；`config.json` 和 `.env` 已加入忽略规则。`rcon_password_env`、`panel_token_env` 引用环境变量名，相关管理能力目前仅完成配置设计，尚不可执行。

## 目标指令

| 指令 | 目标结果 | 当前状态 |
| --- | --- | --- |
| `查询 合作服` / `查询1服` | 原生卡片、状态、地图、玩家、动态连接按钮 | 已接入，真实群已确认卡片与按钮显示 |
| `connect IP:Port` | 临时查询 | 已接入；默认仅公网，可信配置服允许私网 |
| `综合查询` | 多服务器概览、离线状态 | 已接入；最多 4 张总览卡片 |
| `服务器列表` / `服务器地址` / `连接指令` | 原生卡片内可复制连接指令 | 已接入，支持页码 |
| `语音 1服` | 黑盒频道名单、在线人数 | API 复用方案已规划 |
| `设置 1服 status` / `重启 1服` | 管理员操作、结果卡片 | 已规划 |
| Steam 创意工坊链接 | 单品 / 合集、大小、可用下载地址 | 已规划 |
| QQ 闪传链接 | 选服、选文件、面板下载与安装 | 已规划 |

Python + asyncio + aiohttp + python-a2s + Pillow；不需要安装 AstrBot。完整验收项和实现先后顺序见 roadmap。

## 检查与镜像

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker build -t l4d2-qq-bot:local .
docker run --rm l4d2-qq-bot:local validate --config config.example.json
docker compose config --quiet
docker compose up -d --build
```

GitHub Actions 对 PR / 分支执行检查；推送 `v0.1.0` 这样的 tag 时，先检查再构建 amd64 / arm64 镜像并发布到 `ghcr.io/<owner>/<repo>`。预发布 tag 不更新 `latest`。初始化不会自动推送 tag 或发布镜像。

上游为 AGPL-3.0；本项目按 AGPL-3.0-only 初始化，来源与复用边界见 [NOTICE](NOTICE.md)。
