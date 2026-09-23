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
