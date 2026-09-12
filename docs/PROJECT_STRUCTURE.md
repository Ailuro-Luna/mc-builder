# 项目结构

## 公开源码

| 文件 | 职责 |
|---|---|
| `builder.py` | 蓝图编译、只读查询、任务管理、快照、施工与撤销 |
| `dimensions.py` | 维度、高度、保护区间与旧配置兼容 |
| `configure_dimensions.py` | 按实际高度启用或停用维度，保持写入关闭 |
| `set_area.py` | 限定施工区域并同步两端策略 |
| `rcon.py` | 从本地配置定位服务器，读取凭据并进行 RCON 通信 |
| `mcp_server.py` | MCP stdio 工具入口 |
| `mc_builder.sc` | 通用 Carpet 工作者，服务端策略复核和分批执行 |
| `config.example.json` | 不含部署信息的默认配置模板 |
| `plans/example-preview-only.json` | 虚构坐标的离线几何示例 |
| `test_*.py` | 公开通用功能的离线回归 |
| `scripts/check_publication.py`、`.githooks/` | 提交及推送前的公开范围检查 |

调用链为 Agent → MCP → Python → 共享请求/RCON → Carpet → 世界。MCP 和本机 Python 使用同一套施工实现。

## 本地专用资料

真实 `config.json`、`jobs/`、`scans/`、现场 `plans/`、建筑项目、预览图、旧运维脚本、部署记录及地图备份均默认忽略，保留本机原位置。完整文档与整理前文件的本地副本位于 `.local/server/`。

公开配置中的路径是占位符；真实路径与凭据不能通过修改模板发布。Git 源码历史不能代替世界、任务日志和本地配置的独立备份。
