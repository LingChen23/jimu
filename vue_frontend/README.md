# 积木运动数据采集与评分系统（Vue 前端）

Vue 3 + TypeScript + Vite 前端，默认连接同级独立目录
`../vue_backend`。原有 `../pc_visualizer` 保持独立，二者不共享代码或运行数据。

## 已接入能力

- 真实串口发现、刷新、打开和关闭
- 三路聚合器 A / B / C 运行状态与链路诊断
- 10 台设备状态、健康信息和设备控制命令
- 设备 1～10 与积木 A～J 的可编辑一一绑定
- 一次采集任务覆盖全部 10 块积木，并保存每块积木的多次完整移动
- 采集任务开始、结束、持久化历史及真实移动关联
- 实时运动、持久化历史移动、轨迹、速度、震颤及导出
- 三个主页面统一 1.2× 界面比例

任务、设备、串口、运动和历史移动来自独立的 `../vue_backend`；
评分模型尚未配置，已完成任务会显示为“未评分”。

前端 API 请求带有超时保护；实时页只轮询轻量的 `/api/live` 数据，页面切换会
停止轮询并丢弃迟到响应，避免后端断开或快速切换积木时界面永久卡住。

## 运行

需要 Node.js 20+、Python 3.11+，并安装复制后端的依赖：

```powershell
python -m pip install -r ../vue_backend/requirements.txt
```

分别启动后端和前端：

```powershell
npm run dev:backend
npm run dev
```

- 前端：`http://127.0.0.1:5173`
- Vue 配套后端：`http://127.0.0.1:8766`

如果 `pc_visualizer` 已经打开同一个 COM 端口，需要先在原页面关闭串口；同一
串口不能同时被两个后端占用。

## 验证

```powershell
npm test
npm run build
python -m unittest discover -s ../vue_backend/tests -v
```
