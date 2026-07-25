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

当前 ESP32-S3 使用 `COM14`、`921600` 波特率。串口同一时间只能被一个
程序占用，启动前请关闭其他串口监视器。

## 当前数据源

后端直接读取二进制串口流：

```text
COBS(114字节网关帧) + 0x00
```

每个网关帧使用 CRC32 校验，并原样携带一个 nRF Motion V1 100字节包。
后端会解析每包四个 IMU 样本，因此单设备正常速率为每秒25包、100个
加速度样本和100个角速度样本。串口状态接口同时提供字节数、COBS错误、
CRC错误、格式错误和网关序号丢帧统计。

PC 惯导轨迹是轻量级对照算法，包含陀螺仪姿态积分、加速度重力修正、
世界坐标转换和静止零速约束。它用于验证数据链路与算法方向，不能替代
后续经过标定、滤波和测试的正式运动识别算法。

## 测试

```powershell
python -m unittest discover -s tests -v
```
