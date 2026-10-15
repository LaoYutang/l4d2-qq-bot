# 本次验收示例

## 2026-10-15 M1 原生卡片群测

- 71 项 pytest 测试通过，覆盖真实本机 HTTP/WS 协议、PNG 模式与原生模式、重复事件、恢复重连、心跳 ACK 丢失、自定义按钮 / Markdown 权限拒绝。
- 原生单服查询只有一条 `msg_type=2` 消息，包含 markdown 与 keyboard；不调用图片上传接口。
- 并发查询 1服、2服时，各自正文地址和按钮 URL 相符，不共享可变目标。
- 真实 QQ：鉴权、WS 登录、群事件、PNG 上传与回复均成功。切换原生模式后，日志确认“原生 Markdown 卡片已发送（按钮：有）”，用户确认“收到原生卡片，底部有连接按钮”。
- 测试群与应用凭据仅保存在忽略的本地文件。当前运行模式为 `run --config config.json --demo-data`，游戏人数、地图、玩家均为模拟，卡片明确标记。
- 连接基础域名按用户要求配置为 `https://steam-connect.laoyutang.cn`，按钮自动拼接匹配的服务器地址；也支持每服独立 connect_url。
- Ruff 检查与格式检查通过；更新后的 wheel / sdist 构建成功。
- 更新后的 linux/amd64 Docker 镜像构建成功；在断网、只读根文件系统、UID=10001 的容器中通过配置校验、7 项 PNG 演示自检和原生 Markdown / 按钮构造验证。镜像确认不包含 .env 或 config.json；Compose 静默校验通过，没有启动第二份 QQ 实例。
- 尚未验证真实游戏服务器数据、实际点击连接页后唤起 Steam、长期运行、Python 3.13 / 3.14 的远端 CI、arm64 与 GHCR 发布。

### 复现原生交互

```bash
# .env：L4D2_REPLY_FORMAT=markdown，L4D2_CONNECT_BUTTONS=true
# config.json：真实测试群 OpenID，服务器地址，connect_base_url
uv run l4d2-qq-bot run --config config.json --demo-data
```

群中 @机器人分别发送 `查询1服`、`查询2服`，应看到每次只有一条原生卡片、底部连接按钮；核对各自目标为“基础地址/对应 IP:端口”。使用自己的真实服务器地址后，重启时移除 `--demo-data` 才会查询真实数据。PNG 的离线示例与原生 QQ 群测是两种独立验收入口。

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
| server-card.png | 合作服，死亡中心，3/8 人，42ms | 中文、名字脱敏、在线时长、连接信息 |
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

真实 QQ 收发与原生卡片按钮已通过本次群测；后续使用可查询服务器完成真实 A2S、点击连接页及 Steam 唤起验证，再验收长期断线恢复和平台限流。
