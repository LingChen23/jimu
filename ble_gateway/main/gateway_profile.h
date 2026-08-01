#ifndef SMARTBLOCK_GATEWAY_PROFILE_H
#define SMARTBLOCK_GATEWAY_PROFILE_H

#include <stdbool.h>
#include <stdint.h>

#include "host/ble_hs.h"

#define GATEWAY_PROFILE_TARGET_COUNT 4U

typedef struct {
    bool enabled;
    ble_addr_t addr;
    uint8_t expected_device_id;
} gateway_target_t;

typedef struct {
    const char *name;
    uint8_t gateway_id;
    uint16_t preferred_conn_interval_units;
    uint16_t ble_startup_delay_ms;
    gateway_target_t targets[GATEWAY_PROFILE_TARGET_COUNT];
} gateway_profile_t;

const gateway_profile_t *gateway_profile_get(void);
uint8_t gateway_profile_enabled_target_count(
    const gateway_profile_t *profile);
bool gateway_profile_validate(const gateway_profile_t *profile);

#endif
