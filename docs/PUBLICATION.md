# 公开源码与本地资料

公开仓库只维护通用代码、虚构示例、测试和使用方法。部署资料由本机单独保存。

## 本地查看

- `.local/server/AGENT_BUILDING_GUIDE.md`：完整现场指南的本地副本。
- `.local/server/docs/`：完整项目说明与历史验收文档副本。
- `config.json`、`jobs/`、`scans/`、现场蓝图和建筑项目：继续保留原路径。

`.local/` 及所有未列入允许清单的根目录内容均被 Git 忽略。本地管理员可以继续读取和更新这些文件。

## 防止再次发布

提交前运行 `python3 scripts/check_publication.py --staged`；推送前的 hook 检查将要发送的提交及其全部祖先。检查同时限制文件清单并识别实际用户目录、连接信息、私钥和常见访问令牌。

`.gitignore` 不会清理已存在的历史，也不能阻止强制添加或外部工具绕过 hook。公开检查作为额外保护，不能替代对配置、坐标、截图及文档内容的审阅。

如仓库历史已重新整理，旧副本应重新克隆，不要合并或推送旧历史。GitHub 管理的旧 PR 引用和缓存可能需要平台支持处理；已有第三方副本无法由仓库维护者撤回。

参考：[GitHub 清理敏感历史说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
