#ifndef SMARTBLOCK_GATEWAY_TRANSPORT_H
#define SMARTBLOCK_GATEWAY_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

/*
 * Business-channel transport used by the BLE gateway.
 *
 * USB mode keeps the existing four-device direct-to-PC behavior.
 * UART mode carries the exact same COBS + CRC32 frames to an aggregator.
 */
esp_err_t gateway_transport_init(void);
int gateway_transport_read(uint8_t *data,
                           size_t length,
                           TickType_t wait_ticks);
int gateway_transport_write(const uint8_t *data,
                            size_t length,
                            TickType_t wait_ticks);
const char *gateway_transport_name(void);

#endif
