# 本次验收示例

## 2026-10-15 免 @ 指令入口

- 同时接收官方 `GROUP_AT_MESSAGE_CREATE` 与 `GROUP_MESSAGE_CREATE`，沿用 `GROUP_AND_C2C_EVENT (1<<25)`；QQ 端开启“接收所有消息”后，直接发送 `查询1服` 即可触发。
- 未匹配聊天在入队前丢弃；未配置群、机器人自身消息继续忽略。相同群和消息 ID 跨两种事件只回复一次，保留原消息 ID 和递增 msg_seq。
- 离线验证覆盖 HTTP/WS → 未 @ 的查询 → 原生卡片 / PNG 回复、跨事件重复投递、聊天不占队列、群隔离及错误参数提示。
- 本轮 110 项 pytest 测试通过，Ruff 检查与格式检查通过。
- 更新后的 linux/amd64 Docker 镜像构建成功；断网、只读容器中的配置校验和免 @ 事件解析检查通过，UID=10001，镜像不含私人配置。
- 真实 QQ WS 实例已更新并登录。用户在手机 QQ 找到并开启消息接收开关后确认免 @ 测试成功；入口与权限设置说明已补入 README。

## 2026-10-15 真实查询与界面更新

- 用户填写的两台服务器均通过真实 A2S 查询，成功读取真名、地图和人数；用户已在群内确认“真名和实时数据都正常”。真实地址与群 OpenID 仅保留在忽略的本地配置。
- 配置 name 作为查询快捷名，卡片主标题使用真实 server_name。圆括号不再转义为 QQ 公式分隔符；玩家仍脱敏，移除“已脱敏”文案、连接指令和查询时间，按钮改为“进入服务器”。
- 用户已在真实群再次验证 `查询1服` 与 `工坊 442534920`，确认卡片界面调整和真实工坊信息“两项都正常”。
- run 已移除 --demo-data，群内查询只走真实 A2S / 外部接口。demo 与 preview 保留明确标记的离线示例。
- 100 项自动化测试通过，包含真实本地 UDP 的 INFO / PLAYER 挑战、HTTP 地图查询、黑盒分页与字段缺失、工坊单品 / 合集、QQ HTTP/WS、并发目标隔离、界面与权限拒绝。自动化不访问真实 QQ、Steam 或游戏服务器。Ruff 检查、wheel / sdist 构建均通过。
- Steam 官方 API 线上验证：单品 442534920 正确返回元信息；合集 121115793 正确识别为合集，当前 8 项，验证时按上限取 3 项。没有下载文件、提交订阅或安装任务。
- 当前真实查询版本的 linux/amd64 Docker 镜像构建成功；断网、只读根文件系统下通过配置校验、7 项 PNG 演示自检，以及真实查询入口和括号显示检查。容器 UID=10001，镜像未包含 .env 或 config.json；Compose 静默校验通过，未启动第二份 QQ 实例。
- 地图真名与黑盒语音客户端已接入，尚未用本项目的真实地图 API / 黑盒凭据联调；未配置时不伪造名称或零人数。工坊支持默认官方 API 及原项目兼容 JSON API。
- 远端 GitHub CI、arm64 与 GHCR 发布仍需实际运行；本次未触发管理指令或发布。

复现真实群查询：

```bash
uv run l4d2-qq-bot run --config config.json
```

@机器人发送 `查询1服`、`综合查询`、`语音1服`、Steam 工坊链接，或 `工坊 ID 页码`。后两类接口的配置说明见 README。单服卡片正常时只显示一个“进入服务器”按钮；地址仍可通过独立“连接指令”命令获取。

## 2026-10-15 M1 首轮原生卡片历史记录

- 71 项 pytest 测试通过，覆盖真实本机 HTTP/WS 协议、PNG 模式与原生模式、重复事件、恢复重连、心跳 ACK 丢失、自定义按钮 / Markdown 权限拒绝。
- 原生单服查询只有一条 `msg_type=2` 消息，包含 markdown 与 keyboard；不调用图片上传接口。
- 并发查询 1服、2服时，各自正文地址和按钮 URL 相符，不共享可变目标。
- 真实 QQ：鉴权、WS 登录、群事件、PNG 上传与回复均成功。切换原生模式后，日志确认“原生 Markdown 卡片已发送（按钮：有）”，用户确认“收到原生卡片，底部有连接按钮”。
- 当时使用标记的模拟数据验收卡片。该群聊模拟入口现已移除，当前状态见本文真实查询记录。
- 连接基础域名按用户要求配置为 `https://steam-connect.laoyutang.cn`，按钮自动拼接匹配的服务器地址；也支持每服独立 connect_url。
- Ruff 检查与格式检查通过；更新后的 wheel / sdist 构建成功。
- 更新后的 linux/amd64 Docker 镜像构建成功；在断网、只读根文件系统、UID=10001 的容器中通过配置校验、7 项 PNG 演示自检和原生 Markdown / 按钮构造验证。镜像确认不包含 .env 或 config.json；Compose 静默校验通过，没有启动第二份 QQ 实例。
- 当时尚未验证游戏服务器数据；后续已完成真实 A2S 群测。实际点击后唤起 Steam、长期运行、多 Python 版本远端 CI、arm64 与 GHCR 发布仍待验收。

### 复现原生交互

```bash
# .env：L4D2_REPLY_FORMAT=markdown，L4D2_CONNECT_BUTTONS=true
# config.json：真实测试群 OpenID，服务器地址，connect_base_url
uv run l4d2-qq-bot run --config config.json
```

群中 @机器人分别发送 `查询1服`、`查询2服`，应看到每次只有一条原生卡片、底部连接按钮；核对各自目标为“基础地址/对应 IP:端口”。当前 run 始终查询真实服务器；PNG 离线示例为独立入口。

## 2026-10-15 M0 初始离线验证记录

- Windows / Python 3.12.14：Ruff 检查、格式检查和 52 项 pytest 测试通过。
- 按本文命令运行 demo：7 项自检通过，生成 4 张卡片、HTML 与 JSON 报告；已检查实际图片。
- Python wheel / sdist 构建成功。
- GitHub Actions 两个工作流通过 actionlint 静态检查，未创建或推送 tag。
- Docker linux/amd64 镜像构建成功；在 `--network none` 下通过配置校验和完整 demo，确认 UID=10001。容器图片另存于 `artifacts/docker-demo/`。
- 当时尚未执行 QQ 真实收发；后续群测结果见本文 M1 记录。

## 运行

```bash
uv sync --frozen --group dev
uv run l4d2-qq-bot validate --config config.example.json
uv run l4d2-qq-bot demo --event examples/group_event.json --output artifacts/demo
```

在浏览器或编辑器中打开 `artifacts/demo/index.html`。同目录包含四张可直接分享的 PNG 和 `report.json`。所有数据为模拟数据，地址使用文档保留网段，不可实际连接。

## 应看到什么

| 文件 | 内容 | 检查点 |
| --- | --- | --- |
| server-card.png | 合作服，死亡中心，3/8 人，42ms | 中文、名字脱敏、在线时长；无连接指令和时间页脚 |
| overview-card.png | 3 台配置中 2 台在线，共 7/24 人 | 总计不含离线服务器，配置顺序保留 |
| offline-card.png | 对抗服离线 / 查询超时 | 不显示为零玩家在线 |
| players-unavailable-card.png | 三方图服在线，4/16 人 | 明确提示 PLAYER 失败，保留 INFO 人数 |

输入事件是 `GROUP_AT_MESSAGE_CREATE`，指令 `/查询 1 服` 匹配本群 `合作服` 的别名 `1服`。验证使用 d.id 作为消息 ID、未配置群不响应，并在输出卡片上标记“模拟数据”。

`report.json` 中应有 `mode=offline_fixture`、`network_used=false`、`qq_live_verified=false`，checks 全部为 true。它验证本地流程，**不验证**真实 QQ WS、平台上传权限或真实游戏服务器的可用性。

## 需要先验收的方向

1. 深色 / 求生绿配色、信息层级和图片尺寸是否合适。
2. 单服玩家行是否需要增加分数，综合卡片每页显示几台服务器。
3. 默认脱敏是否符合群用途。交互已按用户意见改为“原生卡片 + 同条消息底部按钮”。
4. roadmap 中的黑盒语音、工坊、RCON、闪传面板是否均需首个正式版本覆盖。

## 真实查询与后续联调

```bash
uv run l4d2-qq-bot query 真实公网地址:27015 --output artifacts/live-card.png
```

本地查询支持域名 / IPv4、显式括号 IPv6 地址解析；IPv6 UDP 行为需真实环境另行确认。网络失败会生成离线卡片并返回退出码 1；地址 / 配置错误返回 2。初始化不会选择第三方公共服务器擅自进行探测。

真实 QQ 收发、A2S、原生卡片按钮已通过群测；后续验收连接页 / Steam 唤起、长期断线恢复和平台限流。
