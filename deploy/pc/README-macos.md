# Mac 打包和使用

复用同一套 Vue 页面、Python 采集后端、上传队列和日志功能。Mac 数据目录为
`~/Library/Application Support/JimuMotion/`，升级或替换应用后继续保留。
Windows 仍使用 EXE 同级的 `runtime`。

## GitHub Actions 打包

1. 在仓库 Settings → Secrets and variables → Actions 中配置 `JIMU_UPLOAD_TOKEN`，
   值为现有上传 Token，不能提交到代码中。另配置 `JIMU_ARTIFACT_KEY`，
   值为随机 32 字节密钥的 Base64；本机副本保存在 Git 忽略的
   `vue_backend/runtime/mac_artifact_key.txt`，不要提交。
2. 在 Actions → Build macOS app → Run workflow 选择 `main` 手动运行。
   普通代码推送不会触发打包。
3. 成功后在本次运行的 Artifacts 下载对应包：
   - `JimuMotion-Mac-arm64`：Apple M 系列芯片。
   - `JimuMotion-Mac-x86_64`：Intel 芯片。

公开产物只包含加密的 `mac-packages.enc`，保留 7 天，避免包内 Token 公开。
下载后使用本机密钥进行 AES-256-GCM 解密：前 12 字节为 nonce，后面为密文及
认证标签，附加认证数据为 `JimuMotion-macos-v1`。解密后是 TAR，内含 `.dmg`
和保存应用目录结构的 `.app.zip`。对外发送解密后的安装包即可。
当前构建环境为 macOS 15，先支持 macOS 15 及以上；更早版本需要单独验证。

## Mac 上使用

打开匹配芯片的 DMG，把 JimuMotion 拖到 Applications 后双击启动。
程序自动打开 `http://127.0.0.1:8765/`，顶部菜单栏出现积木图标，
菜单提供“打开界面”和“退出程序”。关闭网页后程序继续运行，退出程序才释放串口。
串口选择实际出现的 `/dev/cu.*` 设备；采集、历史和上传操作与 Windows 相同。

首次启动会将包内云端配置复制到数据目录，已有配置不会被覆盖。
任务、上传队列和日志均保存在上述数据目录，不写入应用包或 DMG。

当前包使用本地临时签名，尚无 Apple Developer ID 签名与公证，不能承诺在
所有 Mac 上直接双击放行。需要使用者按 macOS 的安全提示确认来源；正式分发
时可再配置开发者签名与公证。打包成功不等同于真实设备串口采集已验收。

## 在 Mac 本机打包

安装 Node.js 22、Python 3.12，在仓库根目录运行：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r vue_backend/requirements.txt 'pyinstaller>=6.16,<7'
npm ci --prefix vue_frontend
# 已有 vue_backend/runtime/cloud_upload.json 时直接复用，否则设置 JIMU_UPLOAD_TOKEN。
python deploy/pc/build-macos.py
```

输出默认位于仓库旁的 `JimuMotion-Mac-arm64` 或 `JimuMotion-Mac-x86_64`。
脚本拒绝覆盖已有输出目录。构建需要 macOS 环境。
