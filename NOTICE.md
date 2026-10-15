# 来源与许可证

项目功能与 API 迁移规划参考 LaoYutang 的 [l4d2-with-astrbot](https://github.com/LaoYutang/l4d2-with-astrbot)，其仓库使用 GNU Affero General Public License v3。

初始 QQ 传输与卡片代码独立编写。真实查询扩展参考并改写 LaoYutang 上游提交 `fcac25a9344ae99e1441d90af3cf1dbee2563ce2`（2026-10-15 核对），继续遵守上游 AGPL-3.0 许可与归属：

| 上游文件 | 本项目对应模块 | 保留与改动 |
| --- | --- | --- |
| `heybox_voice.py` | `integrations/heybox.py` | 保留 API 路径、COMMON_PARAMS、token 请求头、昵称选择与分页协议；改用独立共享会话、有界缓存、请求合并、严格数据校验与无敏感数据日志 |
| `l4d2_query.py` | `services/query.py`、`integrations/maps.py` | 参考 A2S 与地图名称协议、地图缓存 1 小时；采用原生异步查询、DNS 固定与真实状态区分 |
| `rcon_client.py`、`main.py` | `integrations/rcon.py`、`services/management.py` | 沿用 Source RCON 协议、原版结果文案与闪传步骤；改为异步、有界读取、管理员精确匹配、确认和审计 |
| `panel_client.py` | `integrations/panel.py`、`services/management.py` | 沿用解析 / 下载路径、字段、选择和结果格式；增加实际扩展名检查、响应边界及禁止自动重试 |
| `config_manager.py`、`pages/settings/*` | `config_store.py`、`web.py`、`static/*` | 页面布局、样式及交互从原版改写，保留版本检查和原子保存；替换 Page Bridge 为环境密码直接鉴权，适配官方 OpenID 与新版字段 |
| `main.py` 的回复正文 | `cards/views.py` | 沿用单服、总览、语音与地址列表文案；共用 Markdown / PNG 输出，保留真实未知状态和脱敏 |

未引入 AstrBot 的事件装饰器、logger 或生命周期。新增 / 改写模块继续遵循上游 AGPL-3.0 和 LaoYutang 归属。

为后续 API 复用保持一致，本项目按 AGPL-3.0-only 初始化，许可证全文见 `LICENSE`。运行依赖各自保留其许可证，中文字体由系统 / 容器软件包提供，不包含 Windows 字体文件。
