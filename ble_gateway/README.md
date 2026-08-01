# ESP32-S3 SmartBlock BLE Gateway

This is one ESP-IDF source project with three compile-time gateway profiles.
Every ESP32-S3 remains a four-link BLE Central, while the selected profile
fixes its gateway ID and the four nRF52832 devices that it is allowed to
connect:

| Profile | Gateway ID | Device IDs | Current address state |
| --- | ---: | --- | --- |
| A | 0 | 1-4 | Configured and enabled |
| B | 1 | 5-8 | Configured and enabled |
| C | 2 | 9-12 | Empty and disabled |

Gateway A currently uses this fixed device-ID/address map:

```text
device 1: E0:CF:E0:E7:F7:4B
device 2: FF:F1:F9:E7:17:27
device 3: DC:CB:F3:67:03:F1
device 4: FD:8E:25:27:E3:F0
```

Gateway B uses:

```text
device 5: DE:7A:A0:A1:06:5C
device 6: FC:D9:6A:DF:B3:E4
device 7: E1:69:9E:5A:1D:B3
device 8: C1:2F:3F:19:FB:92
```

Profile C retains device IDs 9 through 12 but uses an all-zero address with
`enabled=false`. Disabled slots are skipped by matching, scanning and
reconnect decisions. Therefore a C image is safe to run before those devices
exist: it emits offline diagnostics for its four IDs but cannot accidentally
connect to another SmartBlock.

The gateway repeats the connection flow until all four targets are ready:

```text
scan -> connect -> request DLE 251 -> exchange MTU 185 -> discover Motion Service
     -> discover Motion Data -> enable notifications -> validate and decode
```

Motion Data is a fixed 100-byte little-endian packet. Every packet contains
four 100 Hz six-axis IMU samples plus the latest coordinate, so the BLE
notification rate is 25 packets/s. Every packet is decoded, checked and
forwarded unchanged as a COBS + CRC32 gateway frame. The default build uses
native USB Serial/JTAG CDC directly to PC. A separate UART build uses UART1
at 921600 8N1 to connect to the SmartBlock aggregator.

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

The normal `sdkconfig.defaults` build remains the stable profile-A direct-USB
variant. The three production aggregator images all use UART1 and are built
from the same sources with separate configuration and build directories:

```powershell
.\build_gateway_profiles.ps1

# Or build only one profile:
.\build_gateway_profiles.ps1 -Profile B
```

The outputs are:

```text
build-gateway-a/esp32s3_multi_receiver.bin  (gateway_id 0, devices 1-4)
build-gateway-b/esp32s3_multi_receiver.bin  (gateway_id 1, devices 5-8)
build-gateway-c/esp32s3_multi_receiver.bin  (gateway_id 2, devices 9-12)
```

Flash the required image by selecting its existing build directory:

```powershell
idf.py -B build-gateway-a -p COM13 -b 460800 flash
idf.py -B build-gateway-b -p COM13 -b 460800 flash
idf.py -B build-gateway-c -p COM13 -b 460800 flash
```

UART mode uses UART1, TX GPIO17, RX GPIO18, 921600 baud, 8N1 and no hardware
flow control. See `../aggregator/README.md` for three-link wiring and
acceptance procedures.

## Assign devices 9 through 12

All target assignments live in `main/gateway_profile.c`; do not create or
copy another ESP-IDF project. After measuring a device address, replace only
its disabled entry. For example, if device 9 is `AA:BB:CC:DD:EE:FF`, change:

```c
DISABLED_TARGET(9U)
```

to:

```c
RANDOM_TARGET(9U, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff)
```

The macro accepts the same left-to-right address order displayed by a BLE
scanner and performs NimBLE's byte-order conversion internally. Rebuild the
C profile afterward. Startup validation rejects wrong
device-number ranges, duplicate enabled addresses, zero enabled addresses,
and nonzero disabled placeholders.

## Hardware validation

The previous single-node binary closure was verified on 2026-07-25 with
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

## Connection-interval A/B validation

On 2026-07-26, device 1 was tested with J-Link RAM counters while all four
devices streamed simultaneously. In group A, all four links used 20 ms:

```text
device 1 average receive rate: 17.4 packets/s
device 1 nRF queue drops: 500 in approximately 65.5 s
device 1 ESP sequence gaps: 501
device 1 queue depth: continuously 31..32
devices 2..4: approximately 25 packets/s, zero gaps
ESP USB queue drop delta: 0
```

In group B, device 1 used 30 ms while devices 2 through 4 remained at 20 ms:

```text
device 1 average receive rate: 25.0 packets/s
device 1 nRF queue drops: 0
device 1 ESP sequence gaps: 0
device 1 queue depth: continuously 0
devices 2..4: approximately 25 packets/s, zero gaps
ESP USB queue drop delta: 0
```

Based on that result, every connection retains a 5 ms maximum connection-event
length, enough for a complete 100-byte notification and weak-signal retries
with the negotiated DLE. The ESP32 controller honors this HCI connection-event
value rather than use its original internal length algorithm.

The multi-gateway production configuration intentionally uses a different
steady interval for each independent radio:

```text
gateway A / devices 1..4:   30 ms (24 BLE units)
gateway B / devices 5..8: 32.5 ms (26 BLE units)
gateway C / devices 9..12:  35 ms (28 BLE units)
```

Each gateway requests its group interval from the first connection. Firmware
0.1.4 on every nRF requests the same group interval after approximately five
seconds, so a reconnect cannot collapse all colocated gateways back to a
phase-locked 30 ms period. The web diagnostics should therefore report 30,
32.5 or 35 ms according to the device group.

The four-link follow-up on 2026-07-26 showed why both controls are required:
starting all four links directly at 30 ms caused one link to remain near
10 packets/s with a full nRF queue. Supplying an event-length field while the
controller remained in its original scheduling mode had no deterministic
effect because that mode did not honor the HCI value. After enabling the HCI
CE-length mode, using the 5 ms cap and staggering initial intervals, all four
links settled to 30 ms. A clean 30-second continuous-stream check after a cold
ESP32 boot produced:

```text
receive rate: 25.03 / 25.03 / 24.97 / 25.17 packets/s
ESP sequence-gap delta: 0 / 0 / 0 / 0
nRF queue-drop delta: 0 / 0 / 0 / 0
nRF final queue depth: 0 / 0 / 0 / 0
nRF BLE resource-wait delta: 0 / 0 / 0 / 0
disconnect delta: 0 / 0 / 0 / 0
ESP USB queue-drop delta: 0
sensor/sample errors: 0
negotiated links: MTU 185, DLE 189 octets, PHY 1M
```

## Link-budget and reconnect protection

The gateway uses the ESP-IDF controller `P9` transmit-power selector. Lowering
the colocated gateway radios from the previous maximum selector reduces
receiver blocking while the matching nRF52832 application keeps its supported
maximum of +4 dBm.

BLE startup is offset by profile so gateways powered from the same module do
not establish all connection anchors at the same instant:

```text
gateway A:    0 ms
gateway B: 1500 ms
gateway C: 3000 ms
```

Scanning no longer runs continuously when a target disconnects. Each retry is:

```text
passive scan
scan interval: 100 ms
scan window: 10 ms
scan duration: 1 second
pause before next retry: 4 seconds
```

The 10% in-burst scan duty cycle and the longer pause between retries preserve radio
time for links that remain connected. The existing 5 ms per-link connection
event cap remains enabled so a weak link cannot monopolize the controller.

Successful `STOP_STREAM` and `START_STREAM` commands reset only the ESP32
sequence baseline. This prevents the intentional no-stream interval—during
which the nRF sampling sequence continues—from being counted as BLE packet
loss when streaming resumes.
