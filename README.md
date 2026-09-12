# MC Builder

通过 Python、MCP、RCON 与 Carpet，在 Minecraft 中执行有范围限制的蓝图施工、快照、核验和撤销。公开仓库只保存通用工具、配置模板、虚构示例和测试。

## 文档

- [项目结构](docs/PROJECT_STRUCTURE.md)
- [建造操作流程](AGENT_BUILDING_GUIDE.md)
- [多维度配置](docs/MULTI_DIMENSION.md)
- [开发路线](docs/ROADMAP.md)
- [公开与本地资料边界](docs/PUBLICATION.md)
- [开发与提交](CONTRIBUTING.md)

## 离线使用

需要 Linux 和 Python 3.10+，Python 部分使用标准库。新检出目录中执行：

```bash
test -e config.json || cp config.example.json config.json
python3 -m unittest discover -v
python3 builder.py preview plans/example-preview-only.json
```

示例坐标为虚构的几何输入，不是施工位置或授权。预览只编译本地蓝图，不放置方块。真实 `config.json`、任务记录、现场蓝图与运维资料均留在本机。

## 在线接入

在本地配置中填写自己的 `server_root` 和 `shared_root`。新部署的共享目录以 `mc_builder` 命名，工作者源文件为 `mc_builder.sc`，回复使用通用标识。将工作者和关闭写入的策略部署到自己的 Carpet 环境后，再进行健康检查和选址。

已有部署可以继续使用自己的工作者名称：Python 优先使用本地 `worker_name`，缺省时从 `shared_root` 的目录名推导，并兼容既有回复标识。RCON 凭据始终从本机服务器配置读取；修改公开文档不需要重载工作者。

## 能力边界

- 按已配置的维度、高度与施工范围执行，使用实际服务器信息校验。
- 默认只向空气放置白名单方块；源水或既有方块替换需要单独许可。
- 单任务每轴最多 32 格、最多 10,000 个目标位置；全局串行施工。
- 快照区域必须加载，方块实体和玩家改动受到保护。
- 撤销只恢复工具直接改动且仍匹配预期的位置，不是整服恢复。

本地管理员和 Agent 的完整现场指南可保存在 `.local/server/AGENT_BUILDING_GUIDE.md`，该目录不会提交到 Git。
