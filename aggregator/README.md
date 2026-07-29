# SmartBlock Single-Link ESP32-S3 Aggregator

This ESP-IDF project implements the first aggregator closure:

```text
nRF devices 1..4
        |
       BLE
        |
BLE gateway A
        |
UART1, 921600 8N1, full duplex
        |
single-link aggregator
        |
native USB Serial/JTAG
        |
PC visualizer
```

The aggregator does not run BLE, motion processing or trajectory algorithms.
It validates and forwards complete binary frames only.

## Forward path

For every delimited frame received from gateway A, the aggregator:

1. splits on `0x00`;
2. COBS-decodes the frame;
3. checks version, reserved byte, payload length and gateway ID;
4. checks CRC-32/IEEE;
5. accepts Motion (`0x10`), control-response (`0x21`) and link-diagnostic
   (`0x30`) frames;
6. forwards the original encoded frame unchanged to native USB.

Keeping the original frame unchanged preserves gateway ID, gateway sequence
and CRC. The existing PC decoder therefore continues to work without a
protocol conversion.

## Reverse command path

For every frame received from PC native USB, the aggregator:

1. checks COBS, frame layout and CRC;
2. accepts only control-request frames (`0x20`);
3. accepts only device IDs 1 through 4 in this single-link build;
4. forwards the original encoded command to gateway A over UART.

All existing commands remain available through the aggregator:

```text
START_STREAM
STOP_STREAM
GET_INFO
GET_STATUS
RESET_STATS
RECALIBRATE
REBOOT
```

Malformed frames are discarded as complete frames. Partial frames are never
forwarded.

## Wiring

Both ESP32-S3 boards use UART1 with GPIO17 and GPIO18:

```text
BLE gateway A GPIO17 TX  -> aggregator GPIO18 RX
BLE gateway A GPIO18 RX  <- aggregator GPIO17 TX
BLE gateway A GND        -- aggregator GND
```

Do not connect TX to TX or RX to RX. Both boards may be powered from their own
USB connectors; do not join their 5 V pins. Ground must be shared.

The GPIO19/GPIO20 native USB pins are not used by this UART link.

Hardware flow control is intentionally disabled for the first closure.
The aggregator uses a 16 KB UART RX buffer, a 32 KB UART TX buffer and a
256-frame USB output queue.

## Build the aggregator

```powershell
& 'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\export.ps1'
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' build
```

Flash through the aggregator board's `COM` connector:

```powershell
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' `
  -p COM_PORT flash
```

The aggregator board's separate native `USB` connector is the PC business
channel. The baud value selected in the PC visualizer is ignored by native
USB.

## Build gateway A for UART output

The normal `ble_gateway` build remains the stable direct-USB variant. Build
the UART variant in a separate directory:

```powershell
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' `
  -B build-uart `
  -D 'SDKCONFIG=sdkconfig.gateway_uart' `
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.gateway_uart.defaults' `
  build
```

Flash it through gateway A's `COM` connector:

```powershell
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' `
  -B build-uart -p COM_PORT flash
```

To return gateway A to direct-PC mode, flash the normal `ble_gateway/build`
image.

## Acceptance check

After both boards are running and wired:

1. open the aggregator native USB COM port in the PC visualizer;
2. verify devices 1 through 4 become online;
3. verify approximately 25 packets/s per device;
4. verify COBS, CRC and frame errors remain zero;
5. verify gateway sequence loss and per-device sequence gaps do not grow;
6. run `GET_INFO` and `GET_STATUS` for all four devices;
7. run `STOP_STREAM` followed by `START_STREAM`;
8. verify `RESET_STATS`, `RECALIBRATE` and `REBOOT` are routed to the selected
   device only;
9. reset only the aggregator and confirm the four BLE connections on gateway
   A remain established.

The first closure is accepted only when both the continuous data path and the
reverse command path pass.
