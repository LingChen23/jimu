# ESP32-S3 Motion V1 Single-Node USB Gateway

This ESP-IDF project is the first hardware closure for one nRF52832 TX and
one ESP32-S3 gateway. The ESP32-S3 acts as a BLE Central and connects only to:

```text
device_id: 1
BLE address: E0:CF:E0:E7:F7:4B
```

The connection flow is:

```text
scan -> connect -> exchange MTU 185 -> discover Motion Service
     -> discover Motion Data -> enable notifications -> validate and decode
```

Motion Data is a fixed 100-byte little-endian packet. Every packet contains
four 100 Hz six-axis IMU samples plus the latest coordinate, so the BLE
notification rate is 25 packets/s. Every packet is decoded, checked and
forwarded unchanged through native USB Serial/JTAG CDC as a COBS + CRC32
gateway frame.

Each decoded gateway frame is 114 bytes and each delimited USB frame is
normally about 116 bytes. Runtime text logging is disabled so it cannot
corrupt the binary stream. See
`README_PROTOCOL.md` for the byte layout.

The custom UUIDs are:

```text
Motion Service: 6E410001-B5A3-F393-E0A9-E50E24DCCA9E
Motion Data:    6E410002-B5A3-F393-E0A9-E50E24DCCA9E
```

## Build and flash

```powershell
. 'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\export.ps1'
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' build
& 'D:\AIOT\ESP\ESPIDF\V523\523Tools\python_env\idf5.2_py3.11_env\Scripts\python.exe' `
  'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\tools\idf.py' `
  -p COM14 -b 460800 flash
```

The board's `COM` connector remains available for flashing (currently COM14).
The binary business stream uses the separate native `USB` connector, which
enumerates as an ESP32-S3 USB Serial/JTAG CDC port (currently COM15). A baud
setting supplied by PC serial software is ignored by native USB.

The current implementation is intentionally limited to one TX. Multi-node
connection scheduling and forwarding to an aggregator are later stages.

## Hardware validation

The previous UART single-node binary closure was verified on 2026-07-25 with
nRF device 1, ESP32-S3 COM14 and the local Python visualizer:

```text
valid gateway frames: 5755
decoded Motion V1 packets: 5755
decoded IMU samples: 23020
RTC-derived sample rate: 99.987 Hz
gateway sequence loss: 0
nRF packet sequence loss: 0
COBS / CRC / frame errors: 0 / 0 / 0
wire size: approximately 116 bytes per gateway frame
```

The device remained stationary during this capture, so its coordinate state
was `STATIONARY` and the coordinate-valid bit remained clear. The coordinate
fields and flags were still transported and decoded as part of every complete
100-byte payload.
