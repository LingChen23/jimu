# ESP32-S3 Motion V1 Single-Node Gateway

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
notification rate is 25 packets/s. Every packet is decoded and checked.
Diagnostic UART output shows the first five packets and then one packet per
second; sequence gaps and malformed packets are always reported.

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

The diagnostic UART is `COM14` at `460800` baud on the currently connected
board. If flashing reports that COM14 is busy, disconnect any serial monitor
before retrying.

The current implementation is intentionally limited to one TX. Multi-node
connection scheduling and forwarding to an aggregator are later stages.
