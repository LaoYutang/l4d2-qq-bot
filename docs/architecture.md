# 架构与 API 约定

## 模块与数据流

```text
QQ WebSocket → 有界队列 / 去重 → 群权限与命令 → A2S / 外部接口
                                            ↓
                                    原版正文 / 统一脱敏
                                            ↓
                                 原生 Markdown + keyboard / PNG

浏览器 → 密码请求头 → config API → 校验 / 版本冲突检查 / 原子保存
                                            ↓
                                 新业务配置与客户端即时替换
```

- `config.py`：Pydantic 配置与环境变量；QQ 凭据和页面密码只在 Settings。
- `config_store.py`：SHA-256 文件 revision、串行保存、同目录临时文件、fsync / os.replace。
- `web.py`、`static/`：独立配置页与受保护 API，移除 AstrBot Page Bridge。
- `app.py`：事件过滤、去重、队列、配置快照、指令编排和回复。
- `services/query.py`：DNS 固定、私网策略、异步 A2S INFO / PLAYER。
- `services/queries.py`：单服、总览、临时地址和 CLI 共用地图 / 语音编排。
- `integrations/`：地图、黑盒、工坊、面板与 Source RCON。
- `services/management.py`：管理员权限、RCON 直接执行、重启确认与闪传会话、去重后的操作及审计。
- `cards/`：领域结果转原版正文；Markdown / PNG 均使用相同正文和脱敏。原生排版只修饰现有正文，保留标题、字段、分区和原有文字；在转义前按渲染字节预算分页，避免截断转义符或样式。
- `qq/`：Token、OpenAPI、WS 状态机、被动回复序号、平台拒绝回退。
- `runtime.py`：QQ 与外部 HTTP 会话、配置页、worker、网关及关闭流程。

## QQ 官方协议

保持 `GROUP_AND_C2C_EVENT = 1 << 25`，同时处理 `GROUP_AT_MESSAGE_CREATE` 与 `GROUP_MESSAGE_CREATE`。后者要求 QQ 端开启“接收所有消息”。普通聊天在入队前过滤；会话等待期间仅将同群同成员的后续输入交给该会话。群 OpenID、成员 OpenID 均为字符串，不能用群号 / QQ 号替代。

未配置群只允许“获取群ID”这一个引导指令。配置群按配置列表隔离；不能跨群使用管理员身份或服务器。相同群与消息 ID 跨事件去重 10 分钟，缓存最多 4096 项；队列默认 100 项、2 个 worker。队列满时让网关恢复补发，业务不阻塞心跳。

Token 接口 `POST https://api.bot.qq.com/app/getAppAccessToken` 使用 appId / clientSecret；REST 使用 `Authorization: QQBot {access_token}`，`GET /gateway` 获取网关。保留 Hello、Identify、Heartbeat / ACK、READY、Resume、Reconnect、Invalid Session、退避重连。

查询结果、服务器列表和帮助默认使用 `msg_type=2`，由现有正文生成原生 Markdown 标题、字段和分区。ID、未配置、离线、错误及管理操作提示使用 `msg_type=0` 普通文本，不加标题或加粗。有按钮时，`markdown.content` 和 `keyboard.content` 在同一次 `POST /v2/groups/{group_openid}/messages` 中发送，分页时按钮仅附在最后一页。动态连接按钮统一由 connect_base_url/address 生成，不支持单服 URL 覆盖，不依赖共享“当前服务器”。

PNG 通过 files 接口上传获得 file_info，再以 `msg_type=7` 被动发送，补充文字使用普通文本。原消息 ID 为 d.id，每次发送递增 msg_seq，最多 5 次；原生正文按转义和排版后的 UTF-8 字节分页，普通文本直接按 UTF-8 字节分页。平台明确拒绝按钮或 Markdown 才降级，普通文本降级使用保留的原正文并说明原因；降级不再尝试卡片，不重新执行业务。网络结果不确定时不盲目重发。

正文采用上游字段、标点和时长；外部名称清理控制字符并转义 Markdown。圆括号保留，方括号使用实体，不生成 QQ 公式分隔符。去掉产品说明、快捷名说明及统计页脚；保留真实故障提示。详见 [原版对照](parity.md)。

## 配置与热更新

`GET /api/config` 返回 config 和 revision；`POST /api/config` 提交相同结构。两个接口均要求 `X-Config-Password`，服务器使用环境变量 `L4D2_WEB_PASSWORD` 直接比较。浏览器只在 localStorage 保存原密码；退出删除它，不生成 JWT、Cookie 或服务端会话。无密码时不开放 API，静态登录页不含配置数据。

QQ AppID / AppSecret 不进入 AppConfig、磁盘业务配置或 Web API。黑盒凭据、每服 RCON 密码、面板凭据由配置页管理；RCON 与面板只读取直接配置的 rcon_password / panel_token，已删除环境变量引用字段及解析逻辑。单服 connect_url 字段及覆盖逻辑也已删除。读取旧文件时仅丢弃这三项废弃字段，保存后不再写出它们。API 使用 no-store，错误不输出敏感输入，访问日志不记录请求头或正文。

保存先完成 Pydantic 校验，再检查文件 revision；冲突返回 409。临时文件以 0600 创建，在同目录原子替换，Docker 必须挂载可写目录，不能使用只读的单文件 bind mount。

运行时替换 ApplicationResources。每条命令持有自己的配置 / 客户端快照，在途查询完成后再关闭旧缓存；新命令立即使用新配置。地图 API、黑盒凭据或工坊地址改变后创建新客户端，缓存不会跨接口混用。配置变化清除待确认管理会话。

## 查询与外部 API

所有网络操作有超时。外部 API 使用独立于 QQ 的共享 aiohttp 会话，有并发上限和 1 MiB 响应上限，拒绝重定向，不输出响应体或凭据。缓存有 TTL、容量上限、并发合并和显式关闭。

A2S 区分 INFO 失败、PLAYER 失败和真正空名单。players=None 为未知；空 tuple 为已确认的空名单。人数以 INFO 为准，不使用名单长度替代。临时地址在 DNS 解析后检查所有结果并固定实际 IP，默认仅公网；配置服为维护者信任的地址，可访问私网。

地图统一使用 `GET {map_name_url}/{map_code}`，2 秒超时，UTF-8 文本，成功缓存 3600 秒；失败不缓存并返回原代码。同客户端服务所有群、单服、总览和临时 connect；CLI 也使用相同 QueryCoordinator / MapNameClient。

黑盒沿用上游频道、房间 API 和 token 请求头。频道人数实时查询；昵称缓存 600 秒、每页 100、最多 20 页。语音昵称不参与 safe_mode 脱敏，Markdown 与 PNG 均显示原名，仅清理控制字符和限制长度；safe_mode 只作用于游戏玩家名。成功响应的 result 对象中 user_ids 缺失或为 null 时，按上游空频道语义处理；错误状态、非对象 result 和其他非法名单类型仍报错，不伪装成零人。昵称失败仍保留真实人数。

工坊默认 GetPublishedFileDetails / GetCollectionDetails，用合集接口确认类型。也支持原版 `workshop_api_url` POST 数字 ID 数组；children 合集保序，兼容解析 API 的可下载父项保留。默认最多 20 项，原版正文聚合展示；显式页码展示单项。缺下载 URL 保留元信息，不猜测链接。

面板沿用 `POST /download/link/parse`（JSON url）与 `POST /download/add`（表单 url / filename / referer），使用该服 `Authorization: Bearer {token}`。解析与添加分别 60 / 30 秒，无自动重试。只接受面板标记支持且扩展名为 .vpk/.zip/.rar/.7z 的文件，文件名去路径、清理控制字符。

Source RCON 使用 TCP，认证后才发送指令，检查包大小、请求 ID 和结束标记，合并分包结果并限制响应量，总超时 15 秒。_restart 在成功写入后返回原版发送文案，不把发送失败报告成重启成功。

## 管理会话

会话键为 group_openid + member_openid，最多 64 个、30 秒过期。等待期间其他输入取消操作；新的管理请求替换本成员会话。超时通知复用原被动回复上下文，关闭时取消定时器和任务。

RCON / 重启要求本群 admin_openids 与完整精确服务器名 / 别名。“设置”指令通过权限、目标、指令格式和凭据校验后直接执行，不创建确认会话；“重启”仍需明确确认。两条路径共用执行、审计与密码回显遮蔽逻辑，并在剩余处理预算不足时取消执行。闪传先选本群有面板凭据的服务器，解析后列出文件，选择序号 / 多选 / 全部即确认提交。会话后续操作执行前重新核对权限和配置，配置变化停止尚未提交的任务。

任务提交受命令剩余处理预算限制，保留回复时间；已提交、失败、未提交分别如实汇总，状态不明确的 POST 不自动重试。审计记录动作和摘要标识，不记录命令正文、凭据或完整 OpenID。

## 协议依据

- [上游源码与归属](../NOTICE.md)
- [QQ 访问凭证](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html)
- [QQ 群消息事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)
- [QQ 群消息发送](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)
- [QQ Markdown 格式与自定义正文](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)
- [QQ Markdown / 按钮](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html)
- [Steam 工坊接口](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage)
