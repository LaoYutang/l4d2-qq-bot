# L4D2 QQ Bot

独立运行的 L4D2 QQ 官方机器人。通过 WebSocket 接收群消息，使用 QQ 原生 Markdown 与动态连接按钮回复；PNG 为可选模式，不依赖 AstrBot。

已对照 [l4d2-with-astrbot](https://github.com/LaoYutang/l4d2-with-astrbot) 的查询、地图、语音、工坊、RCON、重启、闪传和配置页功能迁移。完整核对记录及保留差异见 [功能对照](docs/parity.md)。

## Docker 启动

```bash
cp .env.example .env
# 在 .env 中填写 L4D2_APP_ID、L4D2_CLIENT_SECRET、L4D2_WEB_PASSWORD。
docker compose up -d --build
```

打开 `http://服务器地址:8080/`，用 `L4D2_WEB_PASSWORD` 登录。密码原值存入浏览器 localStorage，以 `X-Config-Password` 请求头鉴权，不签发 JWT。退出登录会清除浏览器中的密码。QQ AppID、AppSecret 和配置界面密码只从环境变量读取，不出现在业务配置接口中。

首次启动可以没有 config.json 和群配置。在群中发送 `获取群ID`，复制返回的群 OpenID，在配置页添加群组及服务器，再保存即可立即生效。配置卷路径为 `/app/data/config.json`，重建容器后保留。

群主在 QQ 端开启“接收所有消息”后可直接发指令；未开启时需 @机器人。普通群号不能替代官方群 OpenID。管理员可在已配置的群中发送 `获取用户ID`，复制自己的成员 OpenID 到配置页管理员列表。

## 配置界面

沿用原版群组和服务器的增删、排序、管理员列表及密码显示切换。支持：

- 全局连接基础 URL、地图名称 API、玩家名脱敏、黑盒机器人 ID / Token。
- 每群的名称、OpenID、管理员 OpenID、黑盒房间。
- 每服的查询快捷名、别名、地址、独立连接 URL、语音频道、RCON 密码、面板 URL / 凭据。
- A2S 和外部接口的超时、并发，以及工坊解析 API / 合集上限。

保存时校验重复群、服务器名称、别名和地址，通过版本号检查覆盖冲突，再原子替换配置文件。保存后新命令立即使用新配置，进行中的查询保持原快照，旧地图缓存不会串到新接口。配置变更会取消尚待确认的管理操作。

服务器密码和面板凭据可直接在界面填写；旧的 `rcon_password_env`、`panel_token_env` 引用仍兼容，非空时优先读取对应环境变量。黑盒凭据优先使用界面配置，旧 `L4D2_HEYBOX_BOT_ID/TOKEN` 为兼容回退。QQ 凭据始终在环境变量中。

## 群指令

| 指令 | 功能 |
| --- | --- |
| `获取群ID` | 回复当前群 OpenID；尚未配置的群也可使用 |
| `获取用户ID` | 回复当前成员 OpenID，方便配置管理员 |
| `查询1服` / `查询 合作服` | 服务器真名、地图、人数、延迟、玩家和时长，附进入服务器按钮 |
| `connect IP:端口` | 临时查询，默认端口 27015，默认只接受公网 |
| `综合查询` | 按配置顺序显示服务器和人数统计、地图、语音人数 |
| `服务器列表` / `服务器地址` / `连接指令` | 连接按钮或 `connect` 指令；显式加页码时每页 6 台 |
| `语音1服` | 黑盒语音频道人数和名单，标题不带 `===` |
| Steam 工坊链接 / `工坊 ID` | 原版单品 / 合集解析正文；`工坊 ID 页码` 可仅看一项 |
| `设置1服 status` / `重启1服` | 本群管理员操作，30 秒内回复“确认”执行 |
| QQ 闪传链接 | 本群管理员选服务器、选文件或“全部”，向面板提交下载任务 |
| `帮助` | 指令列表 |

查询正文采用原版字段、时长和结果文案，去掉新版的产品说明、快捷名说明及统计页脚；不发送等待提示。查询结果、服务器列表和帮助默认使用 QQ 原生 Markdown 卡片，以现有正文排版标题、字段和分区。ID、未配置、离线、错误及管理操作提示使用普通文本，不加大、不加粗。原生正文和连接按钮仍在同一条 QQ 请求中发送；长正文按卡片分页，按钮附在最后一页。平台明确拒绝卡片时降级并说明原因。玩家列表不可用与真正零玩家分开显示。未配置群除获取群 ID 外不执行命令；未匹配聊天不入队、不回复，跨全量 / @ 事件去重。

综合查询标题为“L4D2 服务器概览”，不加 `===` 或横线分隔；服务器在线统计与总人数放在同一个引用块中。服务器名使用加粗正文作为较小的分区标题，每服的人数、地图直接显示为普通正文，地图名放宽显示长度。

单服原生卡片的地图、人数和延迟放在同一个引用块中。开启按钮时，正文不再重复显示“点击直连”链接或连接指令；按钮被平台拒绝时补充可复制的连接指令。

服务器地址列表标题为“服务器列表”，不显示 `===` 和“点击下方……”说明。原生模式开启按钮时，配置了连接基础地址或每服独立连接地址的条目直接使用配置快捷名作为按钮文字，例如“1服”“2服”，正文不重复列出服务器名；未配置连接地址的条目显示带服务器名的 `connect` 指令。关闭按钮时直接显示指令，按钮被平台拒绝时补充对应服务器的连接指令。

RCON 精确匹配本群服务器，不使用模糊匹配。重启使用 `_restart`，确认按群和成员隔离。闪传选择 30 秒后过期，只接受 `.vpk/.zip/.rar/.7z`；选择文件即确认向所选服务器提交任务。提交失败或超时不自动重试，避免重复执行；实际安装由面板完成。

## 地图与扩展接口

地图按原版请求 `GET {map_name_url}/{map_code}`，读取 UTF-8 纯文本，超时 2 秒。成功缓存 1 小时，同图并发合并，失败不缓存并回退实际地图代码。单服、综合、临时 connect 和本地 query 都使用同一客户端与编排逻辑。

黑盒频道人数每次实时查询，房间昵称缓存 10 分钟。语音失败不改变游戏服务器状态，无法取得人数时不显示为零。工坊默认使用 Steam 官方详情 / 合集接口，也可在配置页填写原版兼容解析 API（`POST [数字 ID]`）；没有下载地址时保留真实元信息。

外部接口和 QQ 使用独立 HTTP 会话。所有调用有超时、响应上限与并发限制，缓存和会话有容量限制，凭据不会写入日志。

## 本地运行与检查

Python 3.12+、[uv](https://docs.astral.sh/uv/)：

支持直接读取电脑上的系统 / 进程环境变量，环境变量优先于 `.env`。例如在同一个 PowerShell 窗口中设置后启动：

```powershell
$env:L4D2_APP_ID="你的AppID"
$env:L4D2_CLIENT_SECRET="你的AppSecret"
$env:L4D2_WEB_PASSWORD="change-this-password"
uv run l4d2-qq-bot run
```

如果已在系统设置中添加这三个变量，重开终端后直接运行最后一条命令即可。`.env` 与 Docker 都是可选方式。

```bash
uv sync --frozen --group dev
# 可选：也可以使用 .env 提供环境变量。
cp .env.example .env
uv run l4d2-qq-bot run
# 仅配置页面，不连接 QQ：
uv run l4d2-qq-bot configure --config config.json
# 配置校验 / 离线预览 / 真实本地查询：
uv run l4d2-qq-bot validate --config config.example.json
uv run l4d2-qq-bot demo --output artifacts/demo
uv run l4d2-qq-bot query 你的服务器地址:27015 --config config.json
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

`configure` 只启动配置页；由 `run` 加载其保存结果。未设置 `L4D2_WEB_PASSWORD` 时 `run` 不开放配置接口，`configure` 拒绝启动。Docker Compose 要求三个环境变量均已填写。`discover` 仍可只监听并把 OpenID 写入本地忽略文件，不回复消息。

`L4D2_REPLY_FORMAT=markdown` 为默认；`image` 使用系统中文字体与 Pillow。Docker 内置 Noto CJK。离线 demo / preview 使用明确标记的模拟数据，run 始终使用真实查询。

- [Docker 与升级](docs/deployment.md)
- [架构和接口](docs/architecture.md)
- [功能核对与验证记录](docs/parity.md)
- [后续规划](docs/roadmap.md)

PR / 分支 CI 检查与构建；只有维护者推送 `v*.*.*` tag 才发布 GHCR 多架构镜像。上游和本项目均遵守 AGPL-3.0，来源见 [NOTICE](NOTICE.md)。
