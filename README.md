# Intelligent Building Blocks

12 路 nRF52832 运动数据采集、BLE 网关与聚合系统。

## 当前目录

- `tx_device/`：nRF52832 TX 设备 Keil 工程。
- `ble_gateway/`：ESP32-S3 四连接 BLE 网关 ESP-IDF 工程。
- `nrf52832_12nodes_ble_gateway_aggregator方案.md`：12 个 TX、3 个 BLE 网关和 1 个聚合器的总体实现方案。

## 计划目录

- `aggregator/`：ESP32-S3 三路串口聚合固件。
- `pc_receiver/`：PC 数据接收和运动状态计算程序。

## TX 工程

使用 Keil 打开：

```text
tx_device/tx.uvprojx
```

当前 TX 工程已完成 `TRAJ_EN` 主动上电时序修复。加速度、角速度和坐标的新数据协议将按总体方案分阶段实现。
