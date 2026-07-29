# SmartBlock 三路 ESP32-S3 聚合器

该 ESP-IDF 工程实现 12 个设备最终架构中的三路双向聚合：

```text
nRF 1～4  → BLE 网关 A ─ UART1 ─┐
nRF 5～8  → BLE 网关 B ─ UART2 ─┼→ 聚合 ESP32-S3 → 原生 USB → PC
nRF 9～12 → BLE 网关 C ─ UART0 ─┘
```

聚合器不运行 BLE、姿态、轨迹或运动状态算法，只进行完整二进制帧的校验、
隔离、合并和反向路由。

## 固定路由

| 链路 | gateway_id | 设备 | 聚合器 UART | 聚合器 TX / RX |
|---|---:|---:|---:|---|
| A | 0 | 1～4 | UART1 | GPIO17 / GPIO18 |
| B | 1 | 5～8 | UART2 | GPIO15 / GPIO16 |
| C | 2 | 9～12 | UART0 | GPIO5 / GPIO6 |

三个 UART 均为 `921600 8N1`、全双工、无硬件流控。GPIO19/GPIO20 保留给
ESP32-S3 原生 USB，不得作为网关 UART 引脚。

## 正向数据路径

每路 UART 都有独立的：

- 16 KB 驱动接收缓冲；
- COBS 分帧状态；
- CRC32 和格式校验；
- 64 个完整帧的接收队列；
- 接收字节、有效帧、COBS/CRC/格式错误和丢帧计数。

聚合器检查输入帧的 `gateway_id` 和设备范围：

```text
链路 A 只接受 gateway_id=0、device_id=1..4
链路 B 只接受 gateway_id=1、device_id=5..8
链路 C 只接受 gateway_id=2、device_id=9..12
```

合并任务轮流访问 A、B、C，每次每路最多取 4 帧，再进入 256 帧公共 USB
输出队列。某一路积压、损坏或重新启动时，不会占用另外两路的解析状态或无限
垄断输出调度。

Motion、控制响应和 BLE 链路诊断帧在校验后保持原有编码、网关序号和 CRC，
原样发送给 PC。

## 反向命令路径

PC 发来的控制请求根据全局 `device_id` 路由：

```text
设备 1～4  → 网关 A
设备 5～8  → 网关 B
设备 9～12 → 网关 C
```

每路有独立的 16 帧命令发送队列和 UART TX 任务。聚合器会把控制请求头中的
`gateway_id` 修正为目标网关号并重新计算 CRC、COBS 编码，然后发送到对应
UART。支持的命令保持不变：

```text
START_STREAM
STOP_STREAM
GET_INFO
GET_STATUS
RESET_STATS
RECALIBRATE
REBOOT
```

## 异步启动和故障隔离

聚合器不要求三个网关同时开机或复位：

- 每路以 `0x00` 独立重新同步 COBS 帧边界；
- 聚合器启动时即使落在某个网关的半帧中，也只丢弃该路当前半帧；
- 尚未上电的网关不会阻塞其他两路；
- 网关重新上电后的第一个完整合法帧会恢复该路“活跃”状态；
- 2.5 秒没有收到合法帧时，仅把对应链路标记为“等待”，不清除其他链路。

未上电网关对应的控制命令仍会写入该 UART，但 PC 最终会因没有控制响应而
超时。聚合器不会跨网关重发命令。

## 聚合器健康帧

聚合器每秒向 PC 发送一帧 `frame_type=0x31`、`gateway_id=0xFF` 的健康状态，
包括：

- 聚合器运行时间、USB 收发字节、有效命令和输入/输出错误；
- USB 输出队列历史峰值；
- A/B/C 每路是否活跃、实际 UART 号和最后合法帧龄；
- 每路接收字节、有效帧、COBS/CRC/格式错误；
- 每路完整帧丢弃、反向命令发送丢弃和接收队列峰值。

PC 页面会在串口控制区下方分别显示三路状态。健康帧格式见
`../ble_gateway/README_PROTOCOL.md`。

## 接线

所有 BLE 网关继续使用自身 UART1 的 GPIO17 TX、GPIO18 RX：

```text
网关 A GPIO17 TX  -> 聚合器 GPIO18 RX
网关 A GPIO18 RX  <- 聚合器 GPIO17 TX

网关 B GPIO17 TX  -> 聚合器 GPIO16 RX
网关 B GPIO18 RX  <- 聚合器 GPIO15 TX

网关 C GPIO17 TX  -> 聚合器 GPIO6 RX
网关 C GPIO18 RX  <- 聚合器 GPIO5 TX

网关 A/B/C GND    -- 聚合器 GND
```

TX 必须交叉连接到 RX。各 ESP32-S3 可以独立供电，但必须共地，不要连接各板
的 5 V 引脚。若各板可能异步上电，建议每根 TX 线上串联 `470 Ω～1 kΩ`
电阻以限制未上电钳位电流。

聚合器的 `COM` 口用于烧录，单独的原生 `USB` 口是 PC 二进制业务通道。
原生 USB 不使用物理波特率，PC 页面上的 921600 设置会被忽略。

## 构建和烧录

```powershell
. 'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\export.ps1'
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' `
  -B build-three-link build
```

通过聚合器开发板的 `COM` 口烧录：

```powershell
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' `
  -B build-three-link -p COM_PORT flash
```

当前生成文件：

```text
build-three-link/smartblock_three_link_aggregator.bin
```

## 网关前置条件

聚合器固件已经支持三路，但真实三路闭环还要求：

- 网关 A 使用 `gateway_id=0`，目标设备 1～4；
- 网关 B 使用 `gateway_id=1`，目标设备 5～8；
- 网关 C 使用 `gateway_id=2`，目标设备 9～12。

当前仓库的 BLE 网关固定配置仍然是网关 A。网关 B/C 的编译配置和设备
5～12 的 BLE 地址需要在下一阶段补充，不能把三块相同的网关 A 固件直接接到
三条链路，否则 B/C 帧会因网关号和设备范围不匹配而被正确拒绝。

## 验收顺序

1. 只接网关 A，确认设备 1～4 与原单路稳定基线一致。
2. 用模拟帧或测试网关同时向三路输入约 104 帧/s，确认公平合并。
3. 检查 PC 页面 A/B/C 的 CRC、格式和丢弃计数保持为零。
4. 分别晚启动、复位和断开一个网关，确认另外两路连续工作。
5. 对设备 1、5、9 分别执行七个控制命令，确认只进入目标 UART。
6. 配置真实网关 B/C 和设备 5～12 后进行 12 节点满负载测试。
