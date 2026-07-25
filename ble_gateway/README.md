# ESP32-S3 Multi BLE Receiver

This is the current four-connection ESP32-S3 BLE gateway project used with
the Mahjong TX trajectory firmware.

The ESP32-S3 is a BLE Central and NUS client for up to four approved Mahjong
TX devices. Every link has independent connection, GATT discovery,
notification subscription, sequence tracking, loss counters, and recovery.
The receiver keeps scanning until every configured target is connected and
restarts scanning when any target disconnects.

## Add another TX

Add one entry to `TARGETS` near the top of `main\main.c`:

```c
{
    .addr = {
        .type = BLE_ADDR_RANDOM,
        .val = {0x06, 0x05, 0x04, 0x03, 0x02, 0x01},
    },
    .expected_device_id = 2,
},
```

NimBLE stores address bytes in reverse display order. The example above is
the displayed address `01:02:03:04:05:06`. Each TX must have a unique address
and a unique nonzero `device_id`. The current targets are:

```text
device 1  E0:CF:E0:E7:F7:4B
device 2  FF:F1:F9:E7:17:27
device 3  DC:CB:F3:67:03:F1
device 4  FD:8E:25:27:E3:F0
```

## Build and flash

```powershell
. 'D:\AIOT\ESP\ESPIDF\V523\v5.2.3\esp-idf\export.ps1'
idf.py set-target esp32s3
idf.py build
idf.py -p COM12 -b 460800 flash monitor
```

The console baud rate is `460800`, because four 50 Hz formatted streams are
too close to the usable throughput limit of 115200 baud.

## Current limitation

Four physical TX addresses are currently configured. Every TX must be powered
and send its matching unique device ID for a concurrent-link test.
