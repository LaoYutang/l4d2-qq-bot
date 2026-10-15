# 来源与许可证

项目功能与 API 迁移规划参考 LaoYutang 的 [l4d2-with-astrbot](https://github.com/LaoYutang/l4d2-with-astrbot)，其仓库使用 GNU Affero General Public License v3。

初始 QQ 传输与卡片代码独立编写。真实查询扩展参考并改写 LaoYutang 上游提交 `fcac25a9344ae99e1441d90af3cf1dbee2563ce2`（2026-10-15 核对），继续遵守上游 AGPL-3.0 许可与归属：

| 上游文件 | 本项目对应模块 | 保留与改动 |
| --- | --- | --- |
| `heybox_voice.py` | `integrations/heybox.py` | 保留 API 路径、COMMON_PARAMS、token 请求头、昵称选择与分页协议；改用独立共享会话、有界缓存、请求合并、严格数据校验与无敏感数据日志 |
| `l4d2_query.py` | `services/query.py`、`integrations/maps.py` | 参考 A2S 与地图名称协议、地图缓存 1 小时；采用原生异步查询、DNS 固定与真实状态区分 |
| `workshop_utils.py` | `integrations/workshop.py` | 兼容原 JSON ID 数组 API 与 children 结构；默认增加 Steam 官方详情 / 合集接口，限制批量并保留没有下载地址的元信息 |

未引入 AstrBot 的事件装饰器、logger 或生命周期，也未迁移上游 RCON / 面板管理实现。

为后续 API 复用保持一致，本项目按 AGPL-3.0-only 初始化，许可证全文见 `LICENSE`。运行依赖各自保留其许可证，中文字体由系统 / 容器软件包提供，不包含 Windows 字体文件。
