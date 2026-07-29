# Intelligent Building Blocks

12 路 nRF52832 运动数据采集、BLE 网关与聚合系统。

## 当前目录

- `tx_device/`：nRF52832 TX 设备 Keil 工程。
- `ble_gateway/`：ESP32-S3 四连接 BLE 网关 ESP-IDF 工程。
- `aggregator/`：ESP32-S3 单路双向 UART 聚合器 ESP-IDF 工程。
- `pc_visualizer/`：支持 12 个设备的 PC 接收、控制、可视化和运动分析程序。
- `nrf52832_12nodes_ble_gateway_aggregator方案.md`：12 个 TX、3 个 BLE 网关和 1 个聚合器的总体实现方案。

## 当前阶段

设备 1～4、BLE 网关 A 和 PC 的直接 USB 四设备闭环已经稳定，并标记为
`v0.1.0-four-node-stable`。

当前正在验证第一路聚合闭环：

```text
nRF 1～4 → BLE 网关 A → UART → 聚合 ESP32-S3 → 原生 USB → PC
```

单路闭环通过后，再将聚合器扩展为三路输入，并增加网关 B/C 和设备 5～12。

## TX 工程

使用 Keil 打开：

```text
tx_device/tx.uvprojx
```

当前 TX 工程已经实现100字节运动包、100 Hz六轴数据、25 Hz坐标、
设备控制、电池电压、运行诊断、32包发送队列和有效看门狗。
