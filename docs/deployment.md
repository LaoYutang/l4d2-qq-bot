# Docker 与 tag 发布

镜像包含 QQ 官方 WS、群回复、密码配置页与本地验收工具，默认执行 `run --config /app/data/config.json`。私人凭据与业务配置不写入镜像。

## 启动 QQ 机器人

复制 `.env.example` 为 `.env`，填写 `L4D2_APP_ID`、`L4D2_CLIENT_SECRET`、`L4D2_WEB_PASSWORD`。首次不需要创建 config.json：机器人可在空群配置下启动。在群中发送“获取群ID”，到 `http://服务器地址:8080/` 登录并添加该 OpenID、服务器和管理员，保存立即生效。

```bash
# --quiet 避免将 env_file 中的凭据展开打印到终端。
docker compose config --quiet
docker compose up -d --build
docker compose logs -f --tail 30 bot
docker compose down
```

Compose 使用镜像默认的 root 用户、只读根文件系统和可写 bot-data 卷。业务配置在 `/app/data/config.json`，发现记录也在 data 卷内，重建容器不会丢失。配置页原子替换文件，不能继续挂载旧版只读的 `/app/config.json`。端口默认 8080，可通过主机的 `L4D2_WEB_PORT` 改映射；反向代理时保留 `X-Config-Password` 请求头。

密码以原值存入当前浏览器 localStorage，每个配置请求直接带上密码，无 JWT。QQ 凭据和页面密码变化后重启容器；页面中的群组、服务器、黑盒、RCON 和面板设置保存后立即应用。退出页面登录会删除本浏览器保存的密码。

错误密码（包括缺失密码）按连接 IP 使用令牌桶：容量 5 次，每 60 秒补充 1 次，5 分钟可补满。正确密码不消耗额度；桶空时该 IP 的配置 API 鉴权暂停，页面显示等待秒数，服务端返回 429 和 `Retry-After`。这是允许短时连续 5 次的令牌桶，随后逐次恢复额度。GET / POST 共用额度，刷新页面不会重置，服务重启会清空记录。

应用只按实际连接对端 IP 限流，忽略客户端传入的 `Forwarded`、`X-Forwarded-For` 和 `X-Real-IP`。反向代理或多实例部署需要按真实客户端 IP 限流时，在代理入口另行配置，并限制绕过代理直连；应用内同一代理后的请求默认共享额度，各实例记录独立。

旧版已有 config.json 时，可在 Linux / macOS 执行 `docker compose exec -T bot sh -c 'cat > /app/data/config.json' < config.json`，由容器中的 bot 用户写入，再重启 bot。RCON 密码 / 面板凭据只读取直接配置的值，连接 URL 统一使用全局基础地址；旧的 rcon_password_env、panel_token_env、connect_url 字段不再生效，读取时忽略、保存后清除。原 AstrBot 的群号、QQ 号须人工映射为官方 OpenID。升级前可用 `docker compose cp bot:/app/data/config.json ./config.backup.local.json` 备份业务配置。不要执行 `docker compose down -v`，它会删除命名数据卷。

主机和 Docker 实例只保留一份在线；WS 连接使用出站 HTTPS/WSS，A2S 使用到游戏服务器的出站 UDP。QQ 平台的沙箱成员、群事件权限及出口 IP 白名单需在应用后台满足。

## Sealos 持久化存储

使用单实例，将可写持久化存储挂载到 `/app/data`，保留 `L4D2_CONFIG_PATH=/app/data/config.json`。无需通过平台的“配置文件”功能创建 config.json，首次保存时程序会自动创建。

镜像默认以 root（UID / GID `0:0`）运行，兼容平台创建的 root 所有存储卷，无需编辑 Pod YAML 设置用户或 fsGroup。挂载方式见 [Sealos 持久化存储文档](https://sealos.io/docs/guides/app-deploy/persistent-volume/)。

旧镜像以 UID 10001 运行，遇到 root 所有且其他用户不可写的存储卷时会保存失败。需要使用包含此修复的新发布镜像，在 Sealos 更新镜像版本并重新部署，保留现有 `/app/data` 挂载即可。

若仍失败，在应用容器终端检查实际运行身份、目录权限和磁盘空间，并确认挂载卷可写：

```sh
id
ls -ldn /app/data
df -h /app/data
```

保存使用同目录临时文件和原子替换，因此整个 `/app/data` 目录都需要可写，仅修改 config.json 文件权限不够。root 用户同样无法写入只读挂载或已满的存储卷。

## 本地镜像

```bash
docker build -t l4d2-qq-bot:local .
docker run --rm l4d2-qq-bot:local validate --config config.example.json
docker run --rm l4d2-qq-bot:local demo --output /tmp/demo
```

第二个命令只校验示例配置；第三个在容器临时目录运行全套示例并输出自检结果。要把卡片复制出来，可保留容器：

```bash
docker run --name l4d2-demo l4d2-qq-bot:local demo --output /app/artifacts/demo
docker cp l4d2-demo:/app/artifacts/demo ./artifacts/docker-demo
docker rm l4d2-demo
```

镜像采用多阶段构建、Python 3.12、冻结的 uv.lock、默认 root 用户、CA 和 Noto CJK。配置页使用入站 8080；WS 需要出站 HTTPS/WSS，A2S 需要到服务器的 UDP，RCON 需要相应 TCP 端口。

## GitHub Actions

- `.github/workflows/ci.yml`：分支与 PR 运行 Ruff、Python 3.12 / 3.13 / 3.14 测试、示例与包构建，然后进行 Docker 冒烟。
- `.github/workflows/release.yml`：仅 `v*.*.*` tag 触发，独立执行同等的 Python 多版本检查与 Docker 冒烟。检查与 tag 格式校验通过后，发布 `linux/amd64`、`linux/arm64` 镜像到 Docker Hub 的 `laoyutang/l4d2-qq-bot`。
- 在 GitHub 仓库 Settings → Secrets and variables → Actions → Secrets 中设置 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_PASSWORD`，分别填写 Docker Hub 登录用户名和密码。账号须有目标仓库的推送权限；密码不写入 `.env`、业务配置或镜像层。工作流的 GitHub 权限仅保留 `contents: read`。
- setup-uv 固定为已核实存在的 `v10.3.0`；uv 工具仍固定为 `0.11.7`。发布流程不依赖编辑器解析本地 reusable workflow，未绑定 GitHub 远端时也可以校验文件。
- 镜像推送地址固定为 `docker.io/laoyutang/l4d2-qq-bot`；公开拉取需将 Docker Hub 仓库设为公开，私有仓库则须先登录有权限的账号。

每次发布只生成两个镜像标签：`latest` 和去掉 Git tag 的 `v` 前缀后的完整版本号。正式 tag `v0.1.0` 对应 `laoyutang/l4d2-qq-bot:latest` 与 `laoyutang/l4d2-qq-bot:0.1.0`；预发布 `v0.2.0-rc.1` 对应 `:latest` 与 `:0.2.0-rc.1`，同样更新 `latest`。

发布后可直接拉取指定版本：

```bash
docker pull laoyutang/l4d2-qq-bot:0.1.0
```

使用现有 Compose 部署远端镜像时，设置 `L4D2_IMAGE=laoyutang/l4d2-qq-bot:0.1.0`，执行 `docker compose pull` 和 `docker compose up -d --no-build`。需要跟随每次发布时，将版本替换为 `latest`。

发布版本以 Git tag 为准，无需手动修改项目内的包版本。Docker 构建时自动把 tag 去掉 `v` 前缀，更新构建目录中的包元数据和锁文件；预发布自动转换为 Python 包版本格式，例如 `v0.2.0-rc.1` 对应 `0.2.0rc1`。程序的 `--version` 读取安装后的包版本，与发布镜像保持一致。工作区中的 `pyproject.toml` / `uv.lock` 仅保留本地打包所需的基础版本，不限制可发布的镜像版本。

版本更新步骤允许联网获取依赖元数据，安装仍使用 `uv sync --frozen`，不要求预先缓存依赖。分支 / PR 构建检查会传入测试版本并核对容器的 `--version`；发布前构建检查使用实际发布 tag，覆盖带版本参数的构建路径。

先提交代码，再由维护者在该提交上显式推送 tag，例如：

```bash
git tag v0.1.0
git push origin v0.1.0
```

这些命令是后续操作说明，本次初始化没有创建提交、tag 或发布。镜像内容依当前代码阶段决定，tag 不能替代 QQ 真实联调。

已有 tag 仍指向创建时的提交，重新运行旧 Actions 不会自动使用分支上的修复。更新工作流后，需要从修复后的提交创建发布 tag；复用失败的旧 tag 时也必须先让它指向修复提交。
