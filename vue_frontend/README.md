# 积木运动数据采集与评分系统（Vue 前端）

Vue 3 + TypeScript + Vite 前端，默认连接同级独立目录
`../vue_backend`。原有 `../pc_visualizer` 保持独立，二者不共享代码或运行数据。

## 已接入能力

- 真实串口发现、刷新、打开和关闭
- 三路聚合器 A / B / C 运行状态与链路诊断
- 12 台真实设备状态、健康信息和七种控制命令
- 设备 1～12 与积木 A～L 的可编辑一一绑定
- 采集任务开始、结束、持久化历史及真实移动关联
- 实时运动、历史移动、轨迹、速度、震颤及导出
- 三个主页面统一 1.2× 界面比例

任务、设备、串口、运动和历史移动来自独立的 `../vue_backend`；
评分模型尚未配置，已完成任务会显示为“未评分”。

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

## 数据源

默认 `.env.example`：

```env
VITE_DATA_SOURCE=motion-system
```

如需离线界面演示，可在本地 `.env` 中改为 `VITE_DATA_SOURCE=mock`。

## 验证

```powershell
npm test
npm run build
python -m unittest discover -s ../vue_backend/tests -v
```
