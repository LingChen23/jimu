# 12 路 nRF52832 运动数据采集、BLE 网关与聚合系统实现方案

## 1. 文档目标

本文给出一套可落地的完整系统方案，用于采集 12 个 nRF52832 从机的运动数据，并通过 3 个 ESP32-S3 BLE 网关和 1 个 ESP32-S3 聚合节点，将全部数据通过单一 USB/串口链路发送到 PC。

系统目标：

- 12 个 nRF52832 从机长期在线；
- 每个从机独立采集和计算，不建立 12 台设备统一时间轴；
- 每个从机输出：
  - 100 Hz 三轴加速度；
  - 100 Hz 三轴角速度；
  - 25 Hz 三维坐标；
- PC 按设备独立计算：
  - 移动；
  - 停顿/静止；
  - 晃动；
  - 翻转；
  - 旋转；
  - 静止时间；
  - 移动时间；
  - 剧烈晃动次数；
  - 翻转或旋转次数；
  - 掉落或异常操作；
- 3 个 ESP32-S3 各固定连接 4 个 nRF52832；
- 第 4 个 ESP32-S3 负责三路串口聚合并向 PC 输出；
- 系统按 12 台设备同时满速发送设计，不依赖“多数设备静止”的假设。

> 虽然需求表述为“100 Hz 加速度数据”，为了可靠识别旋转、翻转和剧烈晃动，建议同时发送 100 Hz 三轴陀螺仪数据。现有 ICM-20689 本身已提供六轴数据，因此增加角速度不会增加额外传感器硬件。

---

## 2. 现有硬件基础

从现有原理图可确认：

- 主控：nRF52832-QFAA；
- IMU：ICM-20689；
- IMU 与 nRF52832 通过 SPI 连接；
- IMU 有独立数据就绪中断；
- nRF52832 配有 32 MHz 高频晶振和 32.768 kHz 低频晶振；
- 板上有调试接口、运行 LED、故障 LED、电池供电和充电电路。

原理图位置：

- 第 1 页：nRF52832 主控、晶振、天线和接口；
- 第 2 页：电池、充电和 3.3 V 电源；
- 第 3 页：ICM-20689、SPI、INT 和受控供电；
- 第 4 页：SWD、UART 和状态 LED。

---

## 3. 总体系统架构

```text
nRF 01 ─┐
nRF 02 ─┼─ BLE ─> ESP32-S3 网关 A ─ UART ─┐
nRF 03 ─┼                                  │
nRF 04 ─┘                                  │
                                           │
nRF 05 ─┐                                  │
nRF 06 ─┼─ BLE ─> ESP32-S3 网关 B ─ UART ─┼─> ESP32-S3 聚合器 ─ USB/串口 ─> PC
nRF 07 ─┼                                  │
nRF 08 ─┘                                  │
                                           │
nRF 09 ─┐                                  │
nRF 10 ─┼─ BLE ─> ESP32-S3 网关 C ─ UART ─┘
nRF 11 ─┼
nRF 12 ─┘
```

固定分组：

```text
网关 A：设备 1～4
网关 B：设备 5～8
网关 C：设备 9～12
```

推荐增加独立聚合 ESP32-S3，而不是让某个 BLE 网关兼任聚合器。主要原因：

- BLE 连接和数据聚合完全解耦；
- 三个 BLE 网关负载对称；
- 聚合器重启不导致 BLE 设备重新连接；
- 单个 BLE 网关异常只影响对应 4 个设备；
- 故障定位和长期维护更容易。

---

## 4. 时间模型

### 4.1 不建立统一时间轴

12 个设备之间不做时间同步。

每个设备只使用自己的本地时间，PC 对每个设备独立计算：

- 样本间隔；
- 静止持续时间；
- 移动持续时间；
- 晃动持续时间；
- 掉落持续时间；
- 单设备轨迹和事件。

设备 1 的时间戳不与设备 2～12 比较。

### 4.2 每个 nRF 必须保留的时间字段

```c
uint32_t session_id;       // 本次启动会话标识
uint32_t sample_sequence;  // 连续样本序号
uint64_t sample_tick_32k;  // 本机 32.768 kHz 单调时钟
```

PC 计算单设备相邻样本时间：

```text
dt = (current_tick - previous_tick) / 32768.0
```

### 4.3 session_id 的作用

即使系统长期不断电，也可能发生：

- 看门狗复位；
- 电压瞬时跌落；
- 软件异常复位；
- 固件升级；
- 人工复位。

当 PC 发现同一 `device_id` 的 `session_id` 变化，应：

- 结束旧会话；
- 清空该设备的姿态、轨迹和状态机；
- 重新进行标定；
- 不把重启前后的轨迹连续起来。

### 4.4 64 位 RTC 扩展

nRF52832 RTC 原始计数器为 24 位，需要软件扩展成 64 位。

```c
static uint32_t s_last_tick24;
static uint64_t s_tick_high;

uint64_t sample_clock_extend(uint32_t tick24)
{
    tick24 &= 0x00FFFFFFU;

    if (tick24 < s_last_tick24) {
        s_tick_high += (1ULL << 24);
    }

    s_last_tick24 = tick24;
    return s_tick_high | tick24;
}
```

100 Hz 下每 10 ms 调用一次，不会漏过多个 RTC 回绕周期。

---

## 5. 数据采样与坐标输出

### 5.1 IMU 采样

```text
加速度：100 Hz
角速度：100 Hz
采样周期：10 ms
```

建议量程：

```text
加速度：±8 g 或 ±16 g
角速度：±1000 °/s 或 ±2000 °/s
```

原因：

- 当前 ±4 g 对掉落冲击容易饱和；
- 快速翻转或剧烈晃动可能使 ±500 °/s 饱和；
- 饱和后 PC 只能知道“超过量程”，无法恢复真实峰值。

### 5.2 坐标输出

```text
坐标频率：25 Hz
坐标周期：40 ms
```

现有轨迹算法继续在 nRF52832 上运行，PC 同时接收：

- 原始六轴数据，用于运动状态识别；
- 25 Hz 坐标，用于轨迹显示或算法对比。

### 5.3 批量发送原则

每 40 ms 发送一次：

```text
4 个 100 Hz 六轴样本
+
1 个 25 Hz 坐标
```

每台 nRF 每秒仅发送 25 个 BLE Notification。

---

## 6. nRF52832 到 BLE 网关的数据包

### 6.1 固定 100 字节数据包

所有字段使用小端序。

| 偏移 | 长度 | 字段 | 说明 |
|---:|---:|---|---|
| 0 | 1 | `protocol_version` | 固定为 1 |
| 1 | 1 | `packet_type` | 固定为 `0x10` |
| 2 | 1 | `device_id` | 1～12，全局唯一 |
| 3 | 1 | `packet_flags` | 数据质量标志 |
| 4 | 4 | `session_id` | 启动会话标识 |
| 8 | 4 | `first_sample_sequence` | 本包首个 IMU 样本序号 |
| 12 | 8 | `first_sample_tick_32k` | 本包首样本本地时间 |
| 20 | 2 | `packet_sequence` | BLE 数据包序号 |
| 22 | 1 | `sample_count` | 固定为 4 |
| 23 | 1 | `imu_format` | 固定为 1 |
| 24 | 56 | `imu_samples[4]` | 4 个六轴样本 |
| 80 | 4 | `coordinate_source_sequence` | 坐标对应的 IMU 样本序号 |
| 84 | 4 | `x_mm` | X 坐标，毫米 |
| 88 | 4 | `y_mm` | Y 坐标，毫米 |
| 92 | 4 | `z_mm` | Z 坐标，毫米 |
| 96 | 2 | `coordinate_flags` | 坐标状态 |
| 98 | 2 | `reserved` | 置零 |
| **合计** | **100** |  |  |

### 6.2 单个 IMU 样本格式

```c
typedef struct
{
    uint16_t tick_offset_32k;

    int16_t ax;
    int16_t ay;
    int16_t az;

    int16_t gx;
    int16_t gy;
    int16_t gz;
} imu_wire_sample_t;
```

单样本大小：

```text
2 + 6 × 2 = 14 字节
```

4 个样本：

```text
4 × 14 = 56 字节
```

### 6.3 样本时间恢复

第 i 个样本：

```text
sample_tick[i] = first_sample_tick_32k + tick_offset_32k[i]
```

100 Hz 时典型偏移：

```text
样本 0：0
样本 1：约 328
样本 2：约 655
样本 3：约 983
```

### 6.4 坐标与原始样本的关联

当前轨迹算法可能存在居中窗口延迟，所以坐标不一定对应当前包内最后一个样本。

因此使用：

```c
uint32_t coordinate_source_sequence;
```

表示该坐标对应哪个 IMU 输入样本。

例如：

```text
当前包 IMU 序号：1000～1003
坐标来源序号：973
```

PC 可以根据序号把坐标放回正确的单设备时间位置。

### 6.5 packet_flags

```c
#define PACKET_FLAG_CALIBRATED       0x01
#define PACKET_FLAG_SAMPLE_GAP       0x02
#define PACKET_FLAG_ACCEL_SATURATED  0x04
#define PACKET_FLAG_GYRO_SATURATED   0x08
#define PACKET_FLAG_SENSOR_ERROR     0x10
#define PACKET_FLAG_QUEUE_DROP       0x20
#define PACKET_FLAG_COORD_VALID      0x40
#define PACKET_FLAG_UNRELIABLE       0x80
```

### 6.6 coordinate_flags

```c
#define COORD_FLAG_STATIONARY   0x0001
#define COORD_FLAG_MOVING       0x0002
#define COORD_FLAG_ORIGIN       0x0004
#define COORD_FLAG_STOP         0x0008
#define COORD_FLAG_HELD         0x0010
#define COORD_FLAG_UNRELIABLE   0x0020
#define COORD_FLAG_CALIBRATING  0x0040
#define COORD_FLAG_ABORTED      0x0080
```

### 6.7 编码要求

禁止直接将 C 结构体 `memcpy` 到无线包中，因为不同编译器和对齐规则可能改变结构体布局。

使用显式编码函数：

```c
static void put_le16(uint8_t *p, uint16_t v);
static void put_le32(uint8_t *p, uint32_t v);
static void put_le64(uint8_t *p, uint64_t v);
```

PC 端对应使用显式小端解码。

---

## 7. nRF52832 固件设计

### 7.1 模块划分

```text
src/
  main.c
  motion_service.c
  motion_packet.c
  sample_clock.c
  imu_batcher.c
  motion_status.c
  realtime_traj.c
  sensor_icm_20689.c

include/
  motion_service.h
  motion_packet.h
  sample_clock.h
  imu_batcher.h
  motion_status.h
  protocol_common.h
  tx_device_config.h
```

### 7.2 主数据流程

```text
ICM-20689 数据就绪中断
          ↓
记录 RTC tick
          ↓
主循环/调度任务读取 SPI 六轴数据
          ↓
检查 SPI 错误、采样间隔、量程饱和
          ↓
调用轨迹算法
          ↓
保存 IMU 样本
          ↓
每 4 个样本打包 1 个坐标
          ↓
生成 100 字节数据包
          ↓
进入 BLE 发送队列
          ↓
发送 Notification
```

### 7.3 中断处理原则

中断中只记录时间和置位，不进行较长 SPI 操作。

```c
void imu_drdy_handler(void)
{
    uint32_t tick24 = app_timer_cnt_get();
    sample_timestamp_queue_push(tick24);
}
```

SPI 读取放到主循环或低优先级调度中完成。

### 7.4 轨迹输出频率

现有配置若为：

```c
#define REALTIME_TRAJ_SAMPLE_HZ 100U
#define REALTIME_TRAJ_OUTPUT_HZ 50U
```

修改为：

```c
#define REALTIME_TRAJ_SAMPLE_HZ 100U
#define REALTIME_TRAJ_OUTPUT_HZ 25U
```

输出分频从 2 改为 4。

### 7.5 自定义 GATT 服务

不建议继续使用默认 20 字节 NUS 作为正式数据通道。

新增 `Motion Service`：

| 特征 | 属性 | 用途 |
|---|---|---|
| Motion Data | Notify | 100 字节运动数据包 |
| Motion Control | Write Without Response | 启停、查询、重置统计 |
| Motion Status | Read + Notify | 电池、错误、队列状态 |
| Device Information | Read | ID、版本、量程、采样率 |

NUS 可以保留用于调试命令，但不发送正式数据。

### 7.6 ATT MTU

推荐：

```c
#define NRF_BLE_MAX_MTU_SIZE 185
```

100 字节 Notification 要求：

```text
ATT MTU >= 100 + 3 = 103
```

目标 MTU 设为 185，网关连接后主动发起 MTU 交换。

自定义数据特征：

```c
attr_md.vlen = 1;
attr.max_len = 100;
```

增大 SoftDevice MTU 后需要重新检查并调整 RAM 起始地址和大小。

### 7.7 BLE 连接参数

推荐初始配置：

```c
#define MIN_CONN_INTERVAL MSEC_TO_UNITS(30, UNIT_1_25_MS)
#define MAX_CONN_INTERVAL MSEC_TO_UNITS(30, UNIT_1_25_MS)
#define SLAVE_LATENCY     0
#define CONN_SUP_TIMEOUT  MSEC_TO_UNITS(5000, UNIT_10_MS)
```

可测试范围：

```text
30～37.5 ms
```

不建议从 45 ms 起步，因为数据包每 40 ms 产生一次，余量不足。

### 7.8 BLE 发送队列

```c
#define MOTION_TX_QUEUE_DEPTH 32
#define MOTION_PACKET_SIZE    100
```

缓存大小：

```text
32 × 100 = 3200 字节
```

缓存时长：

```text
32 / 25 = 1.28 秒
```

发送策略：

```c
err = motion_data_notify(packet, 100);

switch (err) {
case NRF_SUCCESS:
    queue_pop();
    break;

case NRF_ERROR_RESOURCES:
    // 等待 BLE_EVT_TX_COMPLETE 后重试
    break;

case NRF_ERROR_INVALID_STATE:
    // 未连接或通知未开启
    break;

case NRF_ERROR_DATA_SIZE:
    // MTU 协商失败或特征长度配置错误
    record_protocol_error();
    break;

case NRF_ERROR_BUSY:
    // 稍后重试
    break;

case NRF_ERROR_TIMEOUT:
    record_ble_timeout();
    break;

case NRF_ERROR_NO_MEM:
    record_ble_no_mem();
    break;

case NRF_ERROR_FORBIDDEN:
case BLE_ERROR_GATTS_SYS_ATTR_MISSING:
default:
    record_ble_error(err);
    break;
}
```

队列满时：

- 丢弃最旧的完整数据包；
- 增加 `queue_drop_count`；
- 后续数据包设置 `PACKET_FLAG_QUEUE_DROP`；
- 不允许丢弃半包。

### 7.9 控制命令

```c
#define CMD_START_STREAM  0x01
#define CMD_STOP_STREAM   0x02
#define CMD_GET_INFO      0x03
#define CMD_GET_STATUS    0x04
#define CMD_RESET_STATS   0x05
#define CMD_RECALIBRATE   0x06
```

开始命令参数：

```text
sample_rate = 100
coordinate_rate = 25
stream_mask = ACCEL | GYRO | COORDINATE
```

### 7.10 状态帧

每秒发送一次状态帧：

```text
protocol_version
packet_type = STATUS
设备 ID
session_id
uptime_seconds
latest_sample_sequence
sample_gap_count
sensor_error_count
queue_drop_count
ble_resource_wait_count
battery_mV
sensor_temperature
current_range
current_calibration_state
```

---

## 8. 三个 ESP32-S3 BLE 网关

三个网关使用同一套固件，只修改：

```c
#define GATEWAY_ID 0 / 1 / 2
```

以及允许连接的设备列表。

### 8.1 ESP-IDF 配置

使用 ESP-NimBLE Central。

```text
CONFIG_BT_ENABLED=y
CONFIG_BT_NIMBLE_ENABLED=y
CONFIG_BT_NIMBLE_ROLE_CENTRAL=y
CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=n

CONFIG_BT_NIMBLE_MAX_CONNECTIONS=4
CONFIG_BT_CTRL_BLE_MAX_ACT=5
CONFIG_BT_NIMBLE_ATT_PREFERRED_MTU=185

CONFIG_BT_NIMBLE_MAX_BONDS=0
CONFIG_BT_NIMBLE_MAX_CCCDS=8
CONFIG_BT_NIMBLE_LOG_LEVEL_WARNING=y
```

关闭：

- Wi-Fi；
- 经典蓝牙；
- BLE 外围设备角色；
- 不需要的 GATT 服务；
- 高频调试日志。

### 8.2 设备固定映射

```c
// Gateway A
static const uint8_t allowed_devices[] = {1, 2, 3, 4};

// Gateway B
static const uint8_t allowed_devices[] = {5, 6, 7, 8};

// Gateway C
static const uint8_t allowed_devices[] = {9, 10, 11, 12};
```

建议同时使用 BLE 地址白名单，避免多个网关竞争同一设备。

### 8.3 连接流程

```text
扫描目标设备
    ↓
建立连接
    ↓
MTU 交换到 185
    ↓
发现 Motion Service
    ↓
发现 Motion Data / Control / Status
    ↓
订阅 Motion Data Notification
    ↓
订阅 Motion Status Notification
    ↓
读取 Device Information
    ↓
校验 device_id
    ↓
发送 CMD_START_STREAM
```

4 台目标设备均连接成功后停止扫描。

某台掉线时：

- 只扫描对应目标设备；
- 其他连接保持运行；
- 重连加入 200～1000 ms 随机退避；
- 重连后重新执行 MTU、服务发现和订阅。

### 8.4 每连接上下文

```c
typedef struct
{
    uint16_t conn_handle;
    uint8_t  device_id;
    uint8_t  state;

    uint16_t data_value_handle;
    uint16_t data_cccd_handle;
    uint16_t control_handle;
    uint16_t status_handle;

    uint32_t session_id;
    uint32_t expected_sample_sequence;
    uint16_t expected_packet_sequence;

    uint32_t notification_count;
    uint32_t sequence_gap_count;
    uint32_t malformed_count;
    uint32_t queue_drop_count;
    uint32_t reconnect_count;

    bool mtu_ok;
    bool subscribed;
    bool streaming;
} ble_device_context_t;
```

网关不维护 nRF 与网关之间的时钟偏移。

### 8.5 Notification 回调

回调中只做：

- 校验长度；
- 校验协议版本和设备 ID；
- 复制到预分配对象池；
- 放入队列；
- 立即返回。

```c
static int motion_notify_callback(...)
{
    if (payload_len != 100) {
        ctx->malformed_count++;
        return 0;
    }

    frame_buffer_t *buf = frame_pool_alloc();
    if (buf == NULL) {
        ctx->queue_drop_count++;
        return 0;
    }

    memcpy(buf->payload, payload, payload_len);
    buf->payload_len = payload_len;
    buf->gateway_id = GATEWAY_ID;

    if (xQueueSend(notification_queue, &buf, 0) != pdPASS) {
        frame_pool_free(buf);
        ctx->queue_drop_count++;
    }

    return 0;
}
```

禁止在回调中：

- 运行姿态算法；
- 运行轨迹算法；
- COBS 编码；
- 阻塞写 UART；
- 写 Flash；
- 打印完整十六进制包；
- 长时间等待锁。

### 8.6 网关任务结构

```text
NimBLE Host Task
       ↓
notification_queue
       ↓
frame_encode_task
       ↓
uart_tx_queue
       ↓
uart_tx_task
```

其他任务：

```text
connection_manager_task
health_monitor_task
```

职责：

- `connection_manager_task`：扫描、连接、发现服务、重连；
- `frame_encode_task`：增加网关头、CRC32 和 COBS；
- `uart_tx_task`：只负责发送；
- `health_monitor_task`：每秒发送网关状态。

---

## 9. 网关到聚合器的 UART 协议

### 9.1 原始帧格式

| 字段 | 长度 | 说明 |
|---|---:|---|
| `frame_version` | 1 | 固定为 1 |
| `gateway_id` | 1 | 0、1、2 |
| `frame_type` | 1 | 数据、状态或控制响应 |
| `reserved` | 1 | 置零 |
| `gateway_sequence` | 4 | 网关输出帧序号 |
| `payload_length` | 2 | nRF 原始数据长度 |
| `nrf_payload` | 100 | nRF 数据包原样保留 |
| `crc32` | 4 | 对前面字段计算 |
| **合计** | **114** |  |

之后：

```text
COBS(原始帧) + 0x00
```

通常每帧约 116 字节。

### 9.2 frame_type

```c
#define GW_FRAME_MOTION_DATA   0x10
#define GW_FRAME_NRF_STATUS    0x11
#define GW_FRAME_GATEWAY_STAT  0x20
#define GW_FRAME_COMMAND_RSP   0x30
```

### 9.3 gateway_id 是否必要

PC 实际区分设备依靠 `device_id`。

保留 `gateway_id` 主要用于诊断：

- 哪个网关发生 UART 丢包；
- 哪个网关连接经常断开；
- 某设备当前由哪个网关转发；
- 哪一路 CRC 错误较多。

### 9.4 UART 参数

每个网关到聚合器：

```text
921600 baud
8 data bits
no parity
1 stop bit
RTS/CTS 硬件流控，条件允许时启用
```

### 9.5 网关缓存

建议：

```text
Notification 对象池：128 帧
UART 发送环形缓冲：32 KB
```

单网关约 11.6 KB/s，32 KB 可覆盖约 2.7 秒短暂阻塞。

---

## 10. 第四个 ESP32-S3 聚合器

### 10.1 硬件连接

```text
UART0 ← 网关 A
UART1 ← 网关 B
UART2 ← 网关 C
USB   → PC
```

推荐聚合器关闭：

- BLE；
- Wi-Fi；
- 业务 USB 口上的日志输出；
- 不必要的后台任务。

### 10.2 聚合器职责

聚合器只做帧级复用：

```text
UART0 字节流 → COBS 解码 → 长度检查 → CRC32 检查 ─┐
UART1 字节流 → COBS 解码 → 长度检查 → CRC32 检查 ─┼→ merge_queue → USB 输出
UART2 字节流 → COBS 解码 → 长度检查 → CRC32 检查 ─┘
```

聚合器不做：

- 姿态计算；
- 坐标计算；
- 运动状态计算；
- 不同设备时间排序；
- 设备间时间同步；
- 修改 nRF 原始数据。

### 10.3 输出顺序

聚合后的顺序可能是：

```text
设备 1：sequence 1000
设备 7：sequence 800
设备 3：sequence 1200
设备 10：sequence 500
```

这是正常的。

PC 根据 `device_id` 分配到 12 个独立处理对象。

### 10.4 任务结构

```c
uart_rx_task_0();
uart_rx_task_1();
uart_rx_task_2();
usb_tx_task();
health_task();
```

每个 UART 任务维护独立：

- 字节环形缓冲；
- COBS 解码状态；
- 当前帧缓存；
- CRC 错误统计；
- 帧丢弃统计。

### 10.5 缓存建议

```text
每路 UART RX 驱动缓冲：16 KB
每路已解码帧队列：64 帧
统一 merge_queue：256 帧
USB 输出环形缓冲：128 KB
```

满负载约 34.8 KB/s，128 KB 可保存约 3.6 秒数据。

缓存满时：

- 丢弃一个完整帧；
- 不丢弃半个帧；
- 增加 `drop_count`；
- 发送聚合器状态帧；
- PC 通过样本序号发现缺失区间。

### 10.6 输出到 PC

推荐优先级：

1. ESP32-S3 原生 USB CDC 或 USB Serial/JTAG；
2. 921600 baud UART；
3. 不推荐 460800 作为最终生产配置。

业务输出端禁止混入：

- Boot log；
- `printf`；
- 调试文本；
- 十六进制调试转储。

---

## 11. PC 端接收与独立计算

### 11.1 接收流程

```text
读取 USB/串口字节流
        ↓
按 0x00 切分
        ↓
COBS 解码
        ↓
CRC32 校验
        ↓
读取 gateway_id、frame_type 和 payload_length
        ↓
解析 nRF 100 字节数据包
        ↓
按 device_id 分配到 12 个独立处理器
```

### 11.2 每设备独立状态

```c
typedef struct
{
    uint8_t  device_id;
    uint32_t session_id;

    uint32_t expected_sample_sequence;
    uint16_t expected_packet_sequence;

    uint64_t last_sample_tick;
    uint32_t last_coordinate_source_sequence;

    uint64_t lost_sample_count;
    uint64_t malformed_packet_count;
    uint64_t crc_error_count;

    double stationary_seconds;
    double moving_seconds;

    uint32_t strong_shake_count;
    uint32_t flip_count;
    uint32_t rotation_count;
    uint32_t drop_count;

    motion_state_t state;
} device_processor_t;
```

### 11.3 单设备处理流程

```text
解析 device_id
      ↓
选择该设备的 device_processor
      ↓
检查 session_id
      ↓
检查 sample_sequence 连续性
      ↓
恢复 4 个 IMU 样本时间
      ↓
加入该设备独立的滤波与状态机
      ↓
处理坐标和对应 source_sequence
```

12 个设备互不影响。

---

## 12. PC 端运动状态算法建议

### 12.1 派生量

每个样本计算：

```text
acc_norm  = sqrt(ax² + ay² + az²)
gyro_norm = sqrt(gx² + gy² + gz²)
jerk      = (a[k] - a[k-1]) / dt
```

姿态算法推荐：

- Mahony；
- Madgwick；
- 互补滤波；
- 误差状态 EKF。

ICM-20689 没有磁力计，因此：

- 俯仰和横滚可由重力长期修正；
- yaw 只能做短期相对积分；
- 长时间绝对航向会漂移。

### 12.2 静止

初始阈值：

```text
|acc_norm - 1 g| 较小
线性加速度 RMS < 0.05～0.10 g
角速度 RMS < 5～10 °/s
持续 300～800 ms
```

使用进入和退出不同阈值，避免抖动。

### 12.3 移动

当静止条件不满足并持续一定时间后进入移动状态。

移动时间使用样本本地时间累积：

```text
moving_seconds += dt
```

### 12.4 剧烈晃动

初始规则：

```text
线性加速度峰值 > 1.5～3 g
或者线性加速度 RMS > 0.8～1.5 g
并结合 jerk 或 gyro_norm
```

事件防抖：

```text
一次晃动期间只计一次
事件结束后设置 300～800 ms 恢复时间
```

### 12.5 翻转

保存初始向上方向。

当当前向上方向与初始方向夹角：

```text
> 120° 且保持约 200 ms：计一次翻转
< 60°：重新允许计数
```

### 12.6 旋转

陀螺仪扣除零偏后积分：

```text
累计角度 += angular_velocity × dt
```

累计达到约 360° 计一次。

建议：

- X/Y/Z 三轴分别统计；
- 正反方向分别统计；
- 设置低角速度死区；
- 静止阶段更新陀螺仪零偏。

### 12.7 掉落

典型事件链：

```text
低重力阶段
    ↓
短时间内出现冲击
    ↓
可能伴随姿态明显变化
```

初始阈值：

```text
free-fall：acc_norm < 0.2～0.4 g，持续 80～200 ms
impact：acc_norm > 4～8 g
```

若使用 ±4 g 量程，冲击峰值容易饱和，因此建议至少 ±8 g，优先 ±16 g。

### 12.8 异常操作

异常操作应由业务规则组合：

- 掉落；
- 剧烈碰撞；
- 高频连续摇晃；
- 快速翻转；
- 长时间倒置；
- 超速旋转；
- 传感器饱和；
- 数据长时间缺失；
- 设备频繁重启。

---

## 13. 丢包与数据缺失处理

假设收到：

```text
包 1：sequence 100～103
包 2：sequence 108～111
```

说明丢失：

```text
104～107，共 4 个样本，约 40 ms
```

该 40 ms 应标记为：

```text
UNKNOWN / DATA_GAP
```

不直接计入：

- 静止时间；
- 移动时间；
- 晃动时间。

恢复后要求连续收到一定数量样本，再重新进入稳定状态。

---

## 14. 数据量评估

### 14.1 BLE 有效载荷

单设备：

```text
100 字节 × 25 包/s = 2500 B/s
```

单网关 4 台：

```text
2500 × 4 = 10000 B/s
```

12 台：

```text
2500 × 12 = 30000 B/s
```

单网关 Notification 数量：

```text
4 × 25 = 100 次/s
```

三网关总计：

```text
300 次/s
```

### 14.2 UART 聚合流量

按 COBS 后约 116 字节/帧计算。

单设备：

```text
116 × 25 = 2900 B/s
```

单网关：

```text
2900 × 4 = 11600 B/s
```

三个网关：

```text
11600 × 3 = 34800 B/s
```

### 14.3 921600 UART 占用

8N1 下每字节约 10 bit：

```text
理论有效字节能力 = 921600 / 10 = 92160 B/s
```

平均占用率：

```text
34800 / 92160 ≈ 37.8%
```

加入状态帧、控制响应和少量 COBS 波动后，预计仍低于约 40%。

### 14.4 最坏突发

12 台设备同时产生一批：

```text
12 × 116 = 1392 字节
```

921600 baud 下发送时间：

```text
1392 × 10 / 921600 ≈ 15.1 ms
```

下一批数据 40 ms 后产生，因此仍有约 25 ms 余量。

---

## 15. 稳定性设计

### 15.1 BLE 层

- 每个网关固定连接 4 个目标设备；
- 使用白名单；
- 全部连接后停止持续扫描；
- 仅掉线时重新扫描；
- 重连加入随机退避；
- MTU 目标 185；
- 连接间隔 30～37.5 ms；
- Wi-Fi 关闭；
- Notification 回调只复制和入队。

### 15.2 nRF 层

- 100 Hz 采样独立于 BLE；
- BLE 发送失败不阻塞采样；
- 至少 32 包发送队列；
- `NRF_ERROR_RESOURCES` 正常等待重试；
- 所有丢包、队列满和传感器错误都计数；
- 看门狗保留；
- 重启生成新 `session_id`。

### 15.3 UART 层

- 每个网关使用独立 UART；
- 不能把多个 TX 直接并联；
- 使用 COBS + 0x00 分帧；
- 每帧带 CRC32；
- 条件允许时启用 RTS/CTS；
- 业务串口禁止输出文本日志。

### 15.4 聚合器层

- 三路 UART 独立接收缓冲；
- 每路独立 COBS 解析器；
- 只按完整帧入队；
- USB 阻塞时使用 128 KB 缓冲；
- 队列满只丢完整帧；
- 周期发送聚合器健康状态。

### 15.5 PC 层

- 持续读取，避免串口线程长时间阻塞；
- 接收线程与算法线程分离；
- 12 个设备分别维护队列；
- CRC 错误、序号缺失、session 变化均记录；
- 数据缺失期间状态标记为 UNKNOWN。

---

## 16. 建议健康统计字段

### 16.1 nRF 状态

```text
sample_count
sample_gap_count
spi_error_count
sensor_reset_count
accel_saturation_count
gyro_saturation_count
ble_resource_wait_count
ble_queue_drop_count
watchdog_reset_reason
battery_mV
sensor_temperature
```

### 16.2 BLE 网关状态

```text
gateway_id
connected_device_count
每设备 notification_count
每设备 sequence_gap_count
每设备 reconnect_count
每设备 malformed_count
notification_queue_high_watermark
uart_queue_high_watermark
uart_drop_count
```

### 16.3 聚合器状态

```text
每路 UART rx_bytes
每路 valid_frame_count
每路 crc_error_count
每路 cobs_error_count
每路 frame_drop_count
merge_queue_high_watermark
usb_tx_bytes
usb_block_count
output_drop_count
```

---

## 17. 推荐项目目录

### 17.1 nRF52832

```text
nrf_firmware/
  include/
    protocol_common.h
    motion_packet.h
    motion_service.h
    sample_clock.h
    imu_batcher.h
    motion_status.h
    tx_device_config.h

  src/
    main.c
    motion_packet.c
    motion_service.c
    sample_clock.c
    imu_batcher.c
    motion_status.c
    realtime_traj.c
    sensor_icm_20689.c
```

### 17.2 BLE 网关

```text
ble_gateway/
  main/
    app_main.c
    ble_manager.c
    ble_device_context.c
    motion_gatt_client.c
    gateway_protocol.c
    frame_pool.c
    cobs.c
    crc32.c
    uart_output.c
    gateway_health.c

  config/
    gateway_0.h
    gateway_1.h
    gateway_2.h
```

### 17.3 聚合器

```text
aggregator/
  main/
    app_main.c
    uart_input.c
    frame_decoder.c
    merge_queue.c
    usb_output.c
    aggregator_health.c
    cobs.c
    crc32.c
```

### 17.4 PC 软件

```text
pc_receiver/
  protocol/
    cobs_decoder.*
    crc32.*
    gateway_frame.*
    nrf_motion_packet.*

  devices/
    device_manager.*
    device_processor.*
    sample_buffer.*

  algorithms/
    attitude_filter.*
    motion_state_machine.*
    shake_detector.*
    flip_detector.*
    rotation_detector.*
    drop_detector.*

  storage/
    raw_data_writer.*
    event_writer.*
```

---

## 18. 实施阶段

### 阶段 1：单 nRF 验证

完成：

```text
100 Hz 六轴采样
25 Hz 坐标
4 样本 + 1 坐标打包
100 字节数据包
MTU 185
单连接 BLE Notification
```

验证：

- 连续 24 小时；
- 样本序号无异常跳变；
- 本地 tick 单调；
- 队列不持续积压；
- 坐标对应 source_sequence 正确。

### 阶段 2：单网关连接 4 台

完成：

```text
4 条 BLE 连接
100 Notification/s
COBS + CRC32
921600 UART 输出
```

验证：

- 4 台同时运动；
- 随机断开/重连；
- 72 小时运行；
- 无持续队列增长；
- 无静默丢包。

### 阶段 3：聚合器模拟压力测试

三个输入端先不接 BLE，使用模拟数据：

```text
每路 11.6 KB/s
三路合计 34.8 KB/s
```

验证：

- 三路随机突发；
- PC 暂停读取 1～3 秒；
- USB 拔插；
- CRC 错误恢复；
- COBS 失步恢复；
- 队列水位。

### 阶段 4：12 台完整联调

测试：

- 12 台全部静止；
- 12 台全部移动；
- 12 台同时剧烈晃动；
- 单台和多台掉落测试；
- BLE 单链路掉线；
- 单网关重启；
- 聚合器重启；
- nRF 重启；
- PC 暂停读取；
- 2.4 GHz 干扰环境。

### 阶段 5：长期测试

建议至少连续运行 7～14 天，记录：

```text
每设备样本缺失数
每设备 BLE 断线次数
每设备重连耗时
nRF 发送队列最高水位
网关 Notification 队列最高水位
网关 UART 队列最高水位
UART CRC 错误数
聚合器队列最高水位
PC 解析错误数
session_id 变化次数
```

---

## 19. 最终冻结配置

### nRF52832 × 12

```text
IMU：ICM-20689
采样：100 Hz 三轴加速度 + 100 Hz 三轴角速度
坐标：25 Hz
量程：±8/16 g，±1000/2000 °/s
打包：4 个 IMU 样本 + 1 个坐标
BLE 包：100 字节
通知频率：25 包/s
ATT MTU：185
连接间隔：30 ms，允许测试 37.5 ms
BLE 队列：32 包
时间：本地 64 位 32.768 kHz tick
会话：32 位 session_id
```

### BLE 网关 ESP32-S3 × 3

```text
ESP-NimBLE Central
每台固定连接 4 个 nRF
Wi-Fi 关闭
MTU：185
每台接收：100 Notification/s
UART：921600、8N1
协议：COBS + CRC32
UART 输出缓冲：32 KB
```

### 聚合 ESP32-S3 × 1

```text
三路独立 UART 输入
BLE/Wi-Fi 关闭
每路 RX 缓冲：16 KB
每路解码队列：64 帧
统一 merge_queue：256 帧
输出缓冲：128 KB
PC 输出：原生 USB，或 921600 UART
只做帧级聚合，不做运动计算
```

### PC

```text
单端口接收全部数据
按 device_id 分为 12 路
每台设备独立使用 session、sequence、tick
独立计算移动、静止、晃动、翻转、旋转、掉落
不建立统一时间轴
```

---

## 20. 容量结论

按 12 台设备同时满速发送：

```text
BLE 有效数据：约 30.0 KB/s
聚合串口数据：约 34.8 KB/s
921600 UART 占用：约 37.8%
12 台同时产生一批数据时，发送约需 15.1 ms
下一批在 40 ms 后产生
```

因此，在以下条件下，系统具有足够余量：

```text
3 个 BLE 网关，每个固定 4 连接
100 字节批量 Notification
ATT MTU 185
连接间隔 30～37.5 ms
网关到聚合器 921600 UART
聚合器到 PC 使用 USB 或 921600 UART
完整帧分包、COBS、CRC32 和足够缓存
```

系统瓶颈不在平均数据量，而主要在：

- BLE 回调是否阻塞；
- 多连接重连策略；
- 串口分帧是否可靠；
- 缓冲是否足够；
- 错误是否可观测；
- PC 是否持续及时读取。

只要严格按本方案实现，12 台设备长期连接、100 Hz 六轴数据和 25 Hz 坐标持续传输是可行的。
