# PC 公网上传配置

PC 自动上传使用 ECS 公网地址和 Bearer Token，不需要 SSH 用户、SSH 私钥或本地 SSH 隧道。

首次配置时，在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\pc\setup-cloud-upload.ps1
```

脚本默认使用 `http://39.102.212.53`，运行时输入 ECS 上的 `JIMU_UPLOAD_TOKEN`。也可以直接传参：

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\pc\setup-cloud-upload.ps1 `
  -CloudUrl "http://39.102.212.53" -Token "你的Token"
```

配置写入 `vue_backend\runtime\cloud_upload.json`，不会提交 Git。之后正常启动 PC 后端即可，新完成的任务会自动加入上传队列；旧任务可在历史页面点击“上传”。

Token 只用于 ECS 接口认证。ECS 使用 `JimuEcsOssRole` 访问 OSS，PC 不保存 OSS 密钥。

## 历史记录删除

PC 历史页提供“删除本地”“删除云端”，勾选任务后可批量执行，点击后直接删除，无确认弹窗；结果和失败原因显示在页面内。采集中或上传中的任务不能删除。

- 删除本地只清理本机任务和运动文件，云端有归档时仍保留该行。
- 删除云端调用 `DELETE /api/history/tasks/{task_id}`，清理 ECS 索引及对应 OSS 任务目录。本地副本保留，只有两端都没有数据时才移除该行。
- 上传成功不会自动删除本地采集数据，只清理上传缓存。手动删除云端后，本地任务显示“待上传”并暂停自动上传，重启也不会自动传回；需要时点击“上传”。

删除流程在 `vue_backend/task_deletion.py`，前端批量处理在 `pages/history/use-task-deletion.ts`。ECS 复用归档模块的锁及恢复流程；内部 `deleting` 状态使用现有索引字段，不迁移表结构。OSS 删除需要 ECS 角色具有任务路径的列举与删除权限。云端历史页支持单条与批量删除，无需填写 Token，点击后直接执行。上传及归档状态查询仍使用现有 Token。
