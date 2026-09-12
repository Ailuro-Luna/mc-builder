# 多维度使用

`server_status` 的 `dimension_info` 描述服务器实际存在的维度与高度；`configured_dimensions` 描述工具中启用的范围。列出一个维度不代表可以施工。

## 配置

在无活动或未决任务、两端写入关闭时执行：

```bash
python3 configure_dimensions.py --enable minecraft:the_end
python3 configure_dimensions.py --disable minecraft:the_nether
```

这些是接口示例，不代表某台服务器的实际配置。命令读取真实高度并同步策略，保持写入关闭。未知维度默认拒绝，模组维度需明确启用并进行兼容验证。

蓝图的 `dimension`、读取接口的 `dim` 和选址命令的 `--dimension` 必须一致。合法高度按现场配置校验，不能把模板高度当作模组服务器的实际范围。快照外扩在合法边界处截断。

## 保护与恢复

`protected_columns_by_dimension` 按维度保存保护列。旧 `protected_columns` 始终属于主世界，并继续参与保护判断；迁移保留旧信息。维度参与任务摘要、查询、施工与撤销，同一坐标在不同世界不会共用授权范围。

非主世界的源水替换与 `waterlogged=true` 目标暂不启用。单任务大小、加载要求、材料白名单和全局串行限制仍有效。

工作者重载会改变 epoch。旧任务的准备、继续和恢复需要按实时状态核对；常驻 MCP 进程需重新连接以加载新版模块。真实高度、启用状态、现场验收结果只记录在本地。
