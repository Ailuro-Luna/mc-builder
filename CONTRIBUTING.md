# 开发与提交

使用功能分支管理改动，保留现有用户改动及本地运行资料。施工、权限和恢复路径的变更需要验证对应边界与失败行为。

## 检查

```bash
test -e config.json || cp config.example.json config.json
python3 -m unittest discover -v
python3 scripts/check_publication.py --staged
```

`git config --local core.hooksPath .githooks` 可启用提交前与推送前检查。CI 同样检查公开文件及离线测试。

## 提交范围

`.gitignore` 使用公开文件允许清单。新增通用源文件需同时更新允许清单与公开检查工具；现场资料保留在 `.local/` 或其他被忽略的位置。

PR 说明写通用行为、兼容性和测试结果，不粘贴服务器配置、真实坐标或运行日志。在线测试必须单独选址，不在 CI 中连接真实服务器。

公开源码更新不自动部署。已有工作者和本地任务的升级要遵循本机运维记录，不能在活动任务期间重载。
