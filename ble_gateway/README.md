# ESP32-S3 Motion V1 Four-Node USB Gateway

This ESP-IDF project implements gateway A for four nRF52832 TX devices.
The ESP32-S3 acts as a BLE Central and uses a fixed device-ID/address map:

```text
device 1: E0:CF:E0:E7:F7:4B
device 2: FF:F1:F9:E7:17:27
device 3: DC:CB:F3:67:03:F1
device 4: FD:8E:25:27:E3:F0
```

The gateway repeats the connection flow until all four targets are ready:

```text
scan -> connect -> request DLE 251 -> exchange MTU 185 -> discover Motion Service
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

The current implementation supports these four fixed targets. Forwarding three
four-node gateways to the final aggregator remains a later stage.

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

Based on that result, the production configuration now requests 30 ms on all
four ESP32 central links, matching the 30 ms preference in every nRF firmware.
The web diagnostics must report an actual 30 ms interval for devices 1 through
4 after they reconnect. Each connection also advertises a 5 ms maximum
connection-event length, enough for a complete 100-byte notification and
weak-signal retries with the negotiated DLE while limiting the four-link total
to 20 ms of each 30 ms scheduling period. The ESP32 controller honors this HCI
connection-event value rather than use its original internal length algorithm.
During initial establishment only, the ESP32 uses 30, 32.5, 35 and 37.5 ms
respectively to seed different link-anchor phases; each nRF then requests its
configured 30 ms preference after approximately five seconds.

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

Successful `STOP_STREAM` and `START_STREAM` commands reset only the ESP32
sequence baseline. This prevents the intentional no-stream interval—during
which the nRF sampling sequence continues—from being counted as BLE packet
loss when streaming resumes.
