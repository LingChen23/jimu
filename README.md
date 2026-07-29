# Intelligent Building Blocks

12 路 nRF52832 运动数据采集、BLE 网关与聚合系统。

## 当前目录

- `tx_device/`：nRF52832 TX 设备 Keil 工程。
- `ble_gateway/`：ESP32-S3 四连接 BLE 网关 ESP-IDF 工程。
- `aggregator/`：ESP32-S3 三路双向 UART 聚合器 ESP-IDF 工程。
- `pc_visualizer/`：支持 12 个设备的 PC 接收、控制、可视化和运动分析程序。
- `nrf52832_12nodes_ble_gateway_aggregator方案.md`：12 个 TX、3 个 BLE 网关和 1 个聚合器的总体实现方案。

## 当前阶段

设备 1～4、BLE 网关 A 和 PC 的直接 USB 四设备闭环已经稳定，并标记为
`v0.1.0-four-node-stable`。

第一路聚合闭环已经完成验证：

```text
nRF 1～4 → BLE 网关 A → UART → 聚合 ESP32-S3 → 原生 USB → PC
```

三路聚合器固件现已实现并通过构建，包含三路独立 UART、分路队列、公平合并、
设备 1～12 命令路由和聚合器健康状态。下一阶段是把 BLE 网关改成 A/B/C
编译配置，补充设备 5～12 的地址，然后进行三路硬件和 12 节点满负载验证。

## TX 工程

使用 Keil 打开：

```text
tx_device/tx.uvprojx
```

当前 TX 工程已经实现100字节运动包、100 Hz六轴数据、25 Hz坐标、
设备控制、电池电压、运行诊断、32包发送队列和有效看门狗。
