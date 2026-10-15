# L4D2 QQ Bot

独立运行的 L4D2 QQ 官方机器人，支持服务器查询、玩家列表、黑盒语音和服务器管理。默认使用带连接按钮的 QQ 原生卡片回复，提供网页配置界面。

## Docker 镜像启动

新建 `.env` 文件，填写 QQ 机器人凭据和配置页密码：

```dotenv
L4D2_APP_ID=你的AppID
L4D2_CLIENT_SECRET=你的AppSecret
L4D2_WEB_PASSWORD=请替换为配置页密码
```

直接拉取镜像并启动，无需下载源码或本地构建：

```bash
docker pull laoyutang/l4d2-qq-bot:latest
docker run -d --name l4d2-qq-bot \
  --init --restart unless-stopped \
  --env-file .env \
  -e L4D2_CONFIG_PATH=/app/data/config.json \
  -p 8080:8080 \
  -v l4d2-qq-bot-data:/app/data \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  laoyutang/l4d2-qq-bot:latest
```

配置保存在 `l4d2-qq-bot-data` 数据卷中，重建容器后保留。需要固定版本时，将 `latest` 替换为已发布的版本号。Compose 部署、源码构建和升级方法见 [部署文档](docs/deployment.md)。

镜像默认以 root 运行。Sealos 部署时将可写持久化存储挂载到 `/app/data`，使用镜像默认启动方式即可，详见 [Sealos 部署说明](docs/deployment.md#sealos-持久化存储)。

## 首次配置

1. 打开 `http://服务器地址:8080/`，使用 `.env` 中的配置页密码登录。
2. 将机器人加入群，发送 `获取群ID`，在配置页添加返回的群 OpenID 和服务器。
3. 在已配置的群中发送 `获取用户ID`，将成员 OpenID 加入管理员列表，即可使用管理指令。

服务器、管理员、连接地址和语音入口都可在配置页设置，保存后立即生效，无需手动创建 `config.json`。顶部操作栏随滚动保持可见，方便随时保存或重新载入。密码错误过多时，页面会提示等待后重试。

群主在 QQ 端开启“接收所有消息”后可直接发指令，否则需要 @机器人。群和成员必须使用机器人返回的 OpenID，不能填写普通群号或 QQ 号。

## 常用指令

| 指令 | 功能 |
| --- | --- |
| `查询1服` / `查询 合作服` | 查询地图、人数、玩家和在线时长，附游戏及已配置的语音入口 |
| `综合查询` | 查看本群全部服务器状态，各服人数和地图以紧凑列表展示 |
| `服务器列表` / `服务器地址` / `连接指令` | 获取连接按钮，每行最多 2 个；无链接时显示连接指令 |
| `connect IP:端口` | 临时查询服务器，默认仅允许公网地址 |
| `语音1服` / `查询1服语音` / `1服语音` | 查询黑盒语音频道人数和成员 |
| `设置1服 status` | 管理员直接执行 RCON 指令 |
| `重启1服` | 管理员重启服务器，需在 30 秒内回复“确认” |
| QQ 闪传链接 | 管理员选择服务器和文件，提交地图下载任务 |
| `帮助` | 查看指令列表 |

“1服”等名称由配置页设置；语音查询需要配置黑盒凭据和频道，管理指令需要配置对应服务器的密码或面板凭据。

查询卡片标题只显示服务器名，在线玩家使用无序列表展示，底部提供“🎮 进入服务器”和已配置的“🎧 进入语音”按钮。

## 本地运行

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。在项目目录填写同样的 `.env` 后运行：

```bash
uv sync --frozen --group dev
uv run l4d2-qq-bot run
```

## 更多说明

- [部署、升级与镜像发布](docs/deployment.md)
- [功能对照](docs/parity.md) · [架构与接口](docs/architecture.md)
- [验证记录](docs/verification.md) · [后续规划](docs/roadmap.md)

项目参考 [l4d2-with-astrbot](https://github.com/LaoYutang/l4d2-with-astrbot)，遵循 AGPL-3.0，来源与归属见 [NOTICE](NOTICE.md)。
