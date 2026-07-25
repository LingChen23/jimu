# PC Motion Visualizer

本目录是独立的本地 Python 可视化工具，支持：

- 浏览并打开/关闭串口；
- 为 1～12 号设备分别保存数据；
- 显示设备发送的 XY 坐标轨迹；
- 显示三轴加速度和三轴角速度；
- 用加速度、角速度和静止检测计算一条 PC 端惯导轨迹；
- 检查包序号并统计丢包。

## 启动

```powershell
cd D:\AIOT\majiang\motion_system\pc_visualizer
python -m pip install -r requirements.txt
python app.py
```

浏览器会自动打开：

```text
http://127.0.0.1:8765
```

当前 ESP32-S3 使用 `COM14`、`460800` 波特率。串口同一时间只能被一个
程序占用，启动前请关闭其他串口监视器。

## 当前数据源

后端兼容两种输入：

1. 当前 ESP32 固件输出的 `motion_data` 诊断行；
2. 以后 ESP32 输出的 `MOTION_V1_HEX:<200个十六进制字符>` 完整
   100 字节包。

当前诊断行只输出每个被记录数据包的第一个 IMU 样本，适合先验证界面和
串口闭环。切换为完整包转发后，后端会自动按每包四个 100 Hz 样本计算，
无需修改前端。

PC 惯导轨迹是轻量级对照算法，包含陀螺仪姿态积分、加速度重力修正、
世界坐标转换和静止零速约束。它用于验证数据链路与算法方向，不能替代
后续经过标定、滤波和测试的正式运动识别算法。

## 测试

```powershell
python -m unittest discover -s tests -v
```
