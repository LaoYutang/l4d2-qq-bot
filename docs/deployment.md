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

Compose 使用非 root、只读根文件系统和可写 bot-data 卷。业务配置在 `/app/data/config.json`，发现记录也在 data 卷内，重建容器不会丢失。配置页原子替换文件，不能继续挂载旧版只读的 `/app/config.json`。端口默认 8080，可通过主机的 `L4D2_WEB_PORT` 改映射；反向代理时保留 `X-Config-Password` 请求头。

密码以原值存入当前浏览器 localStorage，每个配置请求直接带上密码，无 JWT。QQ 凭据和页面密码变化后重启容器；页面中的群组、服务器、黑盒、RCON 和面板设置保存后立即应用。退出页面登录会删除本浏览器保存的密码。

旧版已有 config.json 时，可在 Linux / macOS 执行 `docker compose exec -T bot sh -c 'cat > /app/data/config.json' < config.json`，由容器中的 bot 用户写入，再重启 bot。RCON 密码 / 面板凭据只读取直接配置的值，连接 URL 统一使用全局基础地址；旧的 rcon_password_env、panel_token_env、connect_url 字段不再生效，读取时忽略、保存后清除。原 AstrBot 的群号、QQ 号须人工映射为官方 OpenID。升级前可用 `docker compose cp bot:/app/data/config.json ./config.backup.local.json` 备份业务配置。不要执行 `docker compose down -v`，它会删除命名数据卷。

主机和 Docker 实例只保留一份在线；WS 连接使用出站 HTTPS/WSS，A2S 使用到游戏服务器的出站 UDP。QQ 平台的沙箱成员、群事件权限及出口 IP 白名单需在应用后台满足。

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

镜像采用多阶段构建、Python 3.12、冻结的 uv.lock、非 root 用户、CA 和 Noto CJK。配置页使用入站 8080；WS 需要出站 HTTPS/WSS，A2S 需要到服务器的 UDP，RCON 需要相应 TCP 端口。

## GitHub Actions

- `.github/workflows/ci.yml`：分支与 PR 运行 Ruff、Python 3.12 / 3.13 / 3.14 测试、示例与包构建，然后进行 Docker 冒烟。
- `.github/workflows/release.yml`：仅 `v*.*.*` tag 触发，独立执行同等的 Python 多版本检查与 Docker 冒烟。检查与 tag / 包版本一致性校验均通过后，发布 `linux/amd64`、`linux/arm64` 镜像到 GHCR。
- 无需 Docker Hub 账号；使用自动提供的 GITHUB_TOKEN，发布 job 单独申请 packages:write。
- setup-uv 固定为已核实存在的 `v10.3.0`；uv 工具仍固定为 `0.11.7`。发布流程不依赖编辑器解析本地 reusable workflow，未绑定 GitHub 远端时也可以校验文件。
- GitHub 仓库首次发布后，镜像可见性由 GHCR Package 设置管理；私有包拉取需要有权限的账号。

正式 tag `v0.1.0` 生成 `:v0.1.0`、`:0.1.0`、`:0.1`、`:latest`。预发布 `v0.2.0-rc.1` 生成带预发布标记的标签，不更新 `:latest` 或 `:0.2`。

发版前同步 `pyproject.toml` 与包 `__version__`，更新 uv.lock；预发布包版本可用 PEP 440 形式 `0.2.0rc1`，工作流与 `v0.2.0-rc.1` 归一化比较。先完成并提交检查，再由维护者显式推送 tag，例如：

```bash
git tag v0.1.0
git push origin v0.1.0
```

这些命令是后续操作说明，本次初始化没有创建提交、tag 或发布。镜像内容依当前代码阶段决定，tag 不能替代 QQ 真实联调。
