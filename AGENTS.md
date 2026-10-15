# AGENTS.md

## 项目目标

用 Python 开发独立的 L4D2 QQ 官方机器人，通过 QQ 官方 WebSocket 网关接收事件、OpenAPI 回复消息。用户已确认默认使用 QQ 原生 Markdown 卡片，动态连接按钮挂在同一条消息底部；PNG 图片作为可选模式和离线预览。项目不依赖 AstrBot、OneBot 或非官方 QQ 协议。

功能参考：https://github.com/LaoYutang/l4d2-with-astrbot 。先阅读 `README.md`、`docs/architecture.md`、`docs/roadmap.md`，区分已实现功能与规划功能。用户最后确认的技术栈为 Python；保留 Dockerfile 和 Git tag 触发 GitHub Actions 构建镜像的要求。

## 技术约定

- Python 3.12+，`src/l4d2_qq_bot` 布局，使用 uv 和 `uv.lock` 管理依赖。
- 网络使用 asyncio / aiohttp；A2S 使用 python-a2s 的异步接口；图片使用 Pillow。
- 配置使用 Pydantic，QQ 凭据通过环境变量 / `.env` 提供，业务配置放在 `config.json`。
- 不引入 AstrBot。复用上游时提取 API 客户端及领域逻辑，不保留事件装饰器、全局 logger 或插件生命周期。
- 中文文档和面向用户的提示；标识符与类型注解使用英文；采用标准库 logging。
- 保持模块边界；配置页使用现有 aiohttp 与静态资源，不引入数据库或浏览器渲染服务。

## 目录职责

- `__main__.py` / `cli.py`：命令行与启动。
- `config.py` / `config_store.py`：配置模型、校验、环境变量与原子保存；`web.py` / `static/` 为密码配置页。
- `domain.py`：与 QQ / AstrBot 无关的数据模型。
- `commands.py`：纯命令解析与服务器匹配。
- `services/`：A2S 查询、并发控制、业务编排。
- `integrations/`：地图名称、黑盒语音、创意工坊等外部 API。
- `qq/`：访问凭证、REST、WebSocket 状态机；不放 L4D2 业务逻辑。
- `cards/`：统一卡片视图、原生 Markdown 和 PNG 渲染；不访问网络。
- `app.py`：事件队列、去重和命令路由。
- `tests/`：不访问真实 QQ、Steam 或游戏服务器的自动化验证。

## 接入规则

- 使用官方文档核实接口，不假设旧 SDK 或上游默认域名仍然有效。当前默认域名为 `https://api.bot.qq.com`。
- WS 实现必须保留 Hello、Identify、Heartbeat / ACK、READY、Resume、Reconnect、Invalid Session；业务处理不得阻塞网关读取和心跳。
- “获取群ID”是未配置群唯一允许的引导指令，只返回群 OpenID，不执行其他业务。
- 配置页密码来自 L4D2_WEB_PASSWORD，以原值存 localStorage，每次通过 X-Config-Password 请求头鉴权；不要增加 JWT 或 Cookie。配置 API 不返回 QQ 应用 ID、密钥或页面密码。
- 同时支持 `GROUP_AT_MESSAGE_CREATE` 与 `GROUP_MESSAGE_CREATE`，后者需 QQ 端开启“接收所有消息”；均使用 `GROUP_AND_C2C_EVENT (1<<25)`。收到消息后按指令匹配，未匹配聊天不入队、不回复，跨两种事件以群和消息 ID 去重。QQ群的 `group_openid` 和群成员 `member_openid` 都是字符串；不得当成群号 / QQ 号。未配置群不执行命令。
- 查询结果、服务器列表与帮助默认使用 `msg_type=2`，同一请求中发送 `markdown.content` 和 `keyboard.content`，不得拆成图片消息和另一条按钮消息来代替原生卡片。ID、未配置、离线、错误及管理操作提示使用 `msg_type=0` 普通文本，不加大、不加粗。用户已在真实群确认能收到原生卡片和连接按钮。
- 按钮链接统一由 `connect_base_url + '/' + address` 得到，已移除单服连接 URL 配置及覆盖逻辑，不依赖共享的“当前服务器”。基础地址由维护者自定义；无链接配置时可用明确标注的填入指令按钮。
- Markdown 复用统一视图的脱敏、离线状态和未知玩家状态，转义外部文本，限制长度；平台拒绝按钮 / Markdown 时明确说明，不能声称按钮已成功发送。
- 可选 PNG 模式经富媒体上传取得 `file_info`，再以 `msg_type=7` 被动回复，并补充可复制连接指令。所有模式保留原始 `msg_id`，同一消息的 `msg_seq` 必须递增。
- 卡片上传和发送失败需可观测，不把发送失败伪装成查询成功；重试不能重复触发有副作用的命令。
- 官方旧 SDK 有 WS 下线公告，而现行文档仍描述 WS。遵循用户的 WS 选择，真实应用可用性通过联调确认；保留传输层接口，不擅自改为 Webhook。

## 查询与权限

- 每次网络操作必须设置超时；综合查询有并发上限，单台离线不影响其他结果。
- 区分服务器离线、玩家列表获取失败和零玩家；超时不应显示为无人在线。
- 服务器名 / 别名忽略空白并大小写归一；只选择唯一匹配，不能把模糊匹配用于 RCON。
- 配置 `name` 是查询快捷名；展示名称采用 A2S 返回的真实 `server_name`，不得用快捷名覆盖。服务器名中的圆括号直接保留，不生成 QQ 会当作公式的 `\(` / `\)`。
- 最新要求以原版正文为准，不额外显示“已脱敏”、快捷名说明、产品说明或查询时间；保留连接能力及脱敏行为。单服连接按钮叫“进入服务器”；服务器地址列表的按钮使用配置快捷名（name），正文不重复列出有按钮的服务器名，无链接配置时显示带服务器名的 connect 指令。
- 配置中的可信服务器允许私网；临时 `connect` 默认只允许公网目标，DNS 解析后检查并固定实际查询地址，防止访问本机或云元数据。
- safe mode 只对游戏玩家名脱敏，覆盖图片和补充文本，短名字也不能原样泄露。黑盒语音昵称显示原名，不受 safe_mode 影响，仍需清理控制字符并限制长度。
- RCON、重启、地图安装仅对本群配置的管理员 OpenID 开放。服务器密码 / 面板凭据仅使用直接配置的 rcon_password / panel_token，已删除对应环境变量字段与读取逻辑；单服连接 URL 字段也已删除。旧文件中的三项废弃字段读取时忽略、保存后清除。QQ ID / 密钥与配置页密码仅从环境变量读取。
- 实现重启 / 安装时加入明确的操作确认、会话隔离和审计；未经授权不得向真实服务器发送管理指令。

## 凭据与资源

- 不提交 `.env`、`config.json`、Token、密码、真实 OpenID 或私有服务器信息。
- 不记录 Authorization、完整网关载荷或包含凭据的 URL / 响应体。配置错误报告不输出 secret 输入值。
- 在关闭时取消并等待后台任务、关闭 HTTP / WS 会话；队列和缓存必须有大小限制。
- 用户提供的名字、地图名等需处理控制字符和超长文本，渲染有尺寸 / 行数限制。
- 中文字体使用系统字体；Docker 安装 Noto CJK。不要复制 Windows 商业字体到仓库或镜像。
- 复用上游代码需保留归属及 AGPL 许可证，记录来源文件和版本；不能仅删除 import 就声称原创。

## 常用命令

```bash
uv sync --frozen --group dev
uv run l4d2-qq-bot validate --config config.example.json
uv run l4d2-qq-bot preview --output artifacts/server-card.png
uv run l4d2-qq-bot discover --duration 120
uv run l4d2-qq-bot run --config config.json
uv run l4d2-qq-bot configure --config config.json
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker build -t l4d2-qq-bot:local .
```

已提供 validate / preview / demo / query / discover / run。discover 仅监听并把 OpenID 写入本地忽略文件，不回复消息。run 始终使用真实查询，已移除 --demo-data；fixture 只在本地 demo / preview 和自动化测试使用。地图名称、黑盒语音与工坊必须调用真实接口，未配置 / 失败不可伪装成成功或零人数。修改网络协议、查询和权限时补充离线测试。简单文案变更无需机械添加测试。提交前执行相关检查；若环境不能运行 Docker 或真实 QQ 联调，明确说明，不把未运行的检查标成通过。

## 发布规则

- PR / 分支 CI 只做检查与构建验证；推送 `v*.*.*` tag 才发布镜像到 GHCR。
- tag 工作流先通过 lint / tests，再构建 `linux/amd64` 和 `linux/arm64`；正式版本更新 `latest`，预发布版本不得覆盖 `latest`。
- 使用仓库 `GITHUB_TOKEN` 与最小权限，不要求用户额外创建 Docker Hub 凭据。
- Docker 非 root 运行，中文字体和 CA 证书内置，凭据不写进镜像层。
- 生成工作流不等于发布。只有用户要求发布时才创建 / 推送 tag；不要自动提交、推送或创建发布。
- 行为或范围变化同步更新 README、配置示例和 roadmap；保持进度标记真实。
