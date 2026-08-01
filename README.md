# Intelligent Building Blocks

12 路 nRF52832 运动数据采集、BLE 网关、三路聚合和 PC 可视化系统。

## 目录结构

- `tx_device/`：nRF52832 传感器 Keil 工程。
- `ble_gateway/`：ESP32-S3 四连接 BLE 网关 ESP-IDF 工程，单工程生成 A/B/C 三套固件。
- `aggregator/`：ESP32-S3 三路双向 UART 聚合器 ESP-IDF 工程。
- `pc_visualizer/`：支持 12 台设备的接收、控制、可视化和运动分析程序。
- `nrf52832_12nodes_ble_gateway_aggregator方案.md`：12 台传感器、3 台 BLE 网关和1台聚合器的总体方案。

## 当前完成状态

原有四设备稳定版本已标记为 `v0.1.0-four-node-stable`。当前第二阶段已经完成两路八设备闭环：

```text
nRF 1～4 ─BLE─> 网关 A ─UART─┐
                              ├─> 三路聚合器 ─原生 USB / COM21─> PC
nRF 5～8 ─BLE─> 网关 B ─UART─┘
```

三路聚合器已经实现三路独立 UART、分路队列、公平合并、设备 1～12 命令路由、链路诊断和聚合器健康状态。只连接 A、只连接 B 或仅连接部分网关时，也可以独立运行。

### 网关分组

| 网关 | nRF 设备 | 稳态连接间隔 | BLE 启动延迟 | 当前状态 |
| --- | --- | ---: | ---: | --- |
| A | 1～4 | 30 ms | 0 s | 地址已配置，已烧录并验证 |
| B | 5～8 | 32.5 ms | 1.5 s | 地址已配置，已烧录并验证 |
| C | 9～12 | 35 ms | 3 s | 固件已构建，地址未配置，目标槽位禁用 |

网关采用 P9 发射功率。断线重连使用低占空比被动扫描：扫描间隔100 ms、窗口10 ms、每次扫描1秒、暂停4秒后重试。连接阶段会请求 DLE，并保留每条连接5 ms的连接事件上限，避免单个弱信号设备长期占用无线时隙。

### nRF52832 固件

设备 1～8 已统一烧录 `0.1.4`：

- 100 字节运动数据包；
- 100 Hz 六轴采样，4组采样封装为1包，约25包/秒；
- 50 Hz 设备坐标；
- 加速度量程 ±8 g，角速度量程 ±1000 °/s；
- BLE 发射功率 +4 dBm；
- 32 包发送队列；
- 主循环有效看门狗；
- 电池电压和运行诊断；
- `START_STREAM`、`STOP_STREAM`、`GET_INFO`、`GET_STATUS`、`RESET_STATS`、`RECALIBRATE`、`REBOOT` 命令。

每台设备约在连接5秒后主动请求本组连接间隔，保证重连后仍恢复为 A=30 ms、B=32.5 ms、C=35 ms。

### 八设备传输验证

设备 1～8、网关 A/B、聚合器和 PC 已完成60秒稳态增量检查：

- 8 台设备全部在线，`GET_INFO` 和 `GET_STATUS` 全部响应成功；
- 实际连接间隔为 A组30 ms、B组32.5 ms；
- 实际 ATT MTU 185、DLE 189字节、1M PHY，没有链路停留在27字节；
- 共接收约12,227个运动包，每台约25包/秒；
- 测试期间新增设备序列间隙、网关丢包、USB队列丢弃、CRC/COBS错误和断连均为0；
- 所有设备传感器错误、采样间隙和当前队列深度均为0。

设备5在网关启动和首次连接阶段曾累计329包队列丢弃，但在上述稳态测试及后续复查中没有继续增长。后续移动压力测试仍需重点观察启动阶段和弱信号状态。

## 工程入口

### nRF52832

使用 Keil 打开：

```text
tx_device/tx.uvprojx
```

烧录不同设备前，在 `tx_device/include/tx_device_config.h` 中设置对应的 `TX_DEVICE_ID`，然后选择 `nrf52832_xxaa` 目标执行 `Rebuild` 和 `Download`。

### BLE 网关

在 ESP-IDF 5.2.3 环境中生成三套固件：

```powershell
cd ble_gateway
./build_gateway_profiles.ps1 -Profile All
```

构建目录分别为 `build-gateway-a/`、`build-gateway-b/` 和 `build-gateway-c/`。

### PC 可视化程序

```powershell
cd pc_visualizer
python app.py
```

业务串口为聚合器原生 USB 串口，当前使用921600波特率打开 COM21。

## 下一步

1. 清零设备、PC和聚合器计数，完成8台设备同时移动的60秒压力测试。
2. 获取并配置设备9～12的固定 BLE 地址。
3. 烧录网关 C 和 nRF 9～12。
4. 进行三路十二节点静止、同时移动、断线重连和长时间满负载验证。
