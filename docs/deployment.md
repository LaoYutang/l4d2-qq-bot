# Docker 与 tag 发布

镜像已包含 QQ 官方 WS、群回复与本地验收工具，默认执行 `run --config /app/config.json`。私人凭据与配置通过运行时注入，镜像本身不包含它们。

## 启动 QQ 机器人

准备好本地 `.env` 与 `config.json`，先确认群 OpenID 已填写，并替换示例服务器地址。尚不知道群 OpenID 时，可在主机先运行 `uv run l4d2-qq-bot discover`，按 README 的步骤获取。

```bash
# --quiet 避免将 env_file 中的凭据展开打印到终端。
docker compose config --quiet
docker compose up -d --build
docker compose logs -f --tail 30 bot
docker compose down
```

Compose 仅挂载只读配置、注入 `.env`，使用非 root、只读根文件系统和可写 data 卷。OpenID 发现记录保存在 data 卷内。run 始终使用真实 A2S 与外部接口，原生 Markdown 卡片和“进入服务器”按钮为默认展示；在 config.json 自定义 connect_base_url。离线外观演示使用单独的 demo 命令，不能再给 run 添加 --demo-data。

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

镜像采用多阶段构建、Python 3.12、冻结的 uv.lock、非 root 用户、CA 和 Noto CJK。没有内置应用凭据、私人配置或 Windows 字体。不要求开放入站端口；WS 运行需要出站 HTTPS/WSS、A2S 需要到服务器的 UDP 通信。

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
