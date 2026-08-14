#include "gateway_profile.h"

#include <stddef.h>
#include <string.h>

/*
 * RANDOM_TARGET accepts the address in the same left-to-right order shown by
 * BLE scanners. NimBLE stores ble_addr_t.val in the opposite byte order.
 */
#define RANDOM_TARGET(device_id_, a5_, a4_, a3_, a2_, a1_, a0_) \
    {                                                            \
        .enabled = true,                                         \
        .addr = {                                                 \
            .type = BLE_ADDR_RANDOM,                              \
            .val = {a0_, a1_, a2_, a3_, a4_, a5_},               \
        },                                                       \
        .expected_device_id = (device_id_),                       \
    }

/*
 * An unassigned slot has an all-zero address and is explicitly disabled.
 * It cannot match an advertisement until its real address is configured.
 */
#define DISABLED_TARGET                              \
    {                                                \
        .enabled = false,                            \
        .addr = {                                    \
            .type = BLE_ADDR_RANDOM,                 \
            .val = {0, 0, 0, 0, 0, 0},              \
        },                                           \
        .expected_device_id = 0U,                    \
    }

#if defined(CONFIG_SMARTBLOCK_GATEWAY_PROFILE_A)

static const gateway_profile_t PROFILE = {
    .name = "A",
    .gateway_id = 0U,
    .preferred_conn_interval_units = 25U, /* 31.25 ms; avoid 60 ms lockstep */
    .ble_startup_delay_ms = 0U,
    .targets = {
        RANDOM_TARGET(1U, 0xe0, 0xcf, 0xe0, 0xe7, 0xf7, 0x4b),
        RANDOM_TARGET(2U, 0xff, 0xf1, 0xf9, 0xe7, 0x17, 0x27),
        RANDOM_TARGET(3U, 0xdc, 0xcb, 0xf3, 0x67, 0x03, 0xf1),
        RANDOM_TARGET(4U, 0xfd, 0x8e, 0x25, 0x27, 0xe3, 0xf0),
    },
};

#elif defined(CONFIG_SMARTBLOCK_GATEWAY_PROFILE_B)

static const gateway_profile_t PROFILE = {
    .name = "B",
    .gateway_id = 1U,
    .preferred_conn_interval_units = 26U, /* 32.5 ms */
    .ble_startup_delay_ms = 1500U,
    .targets = {
        RANDOM_TARGET(5U, 0xde, 0x7a, 0xa0, 0xa1, 0x06, 0x5c),
        RANDOM_TARGET(6U, 0xfc, 0xd9, 0x6a, 0xdf, 0xb3, 0xe4),
        RANDOM_TARGET(7U, 0xe1, 0x69, 0x9e, 0x5a, 0x1d, 0xb3),
        DISABLED_TARGET,
    },
};

#elif defined(CONFIG_SMARTBLOCK_GATEWAY_PROFILE_C)

static const gateway_profile_t PROFILE = {
    .name = "C",
    .gateway_id = 2U,
    .preferred_conn_interval_units = 28U, /* 35 ms */
    .ble_startup_delay_ms = 3000U,
    .targets = {
        RANDOM_TARGET(8U, 0xc1, 0x2f, 0x3f, 0x19, 0xfb, 0x92),
        RANDOM_TARGET(9U, 0xfc, 0xb0, 0x29, 0x81, 0x12, 0x1e),
        RANDOM_TARGET(10U, 0xc1, 0x58, 0x2f, 0xb8, 0xbb, 0xa5),
        DISABLED_TARGET,
    },
};

#else
#error "Select one SmartBlock gateway profile in menuconfig"
#endif

static bool address_is_zero(const ble_addr_t *addr)
{
    static const uint8_t ZERO_ADDRESS[sizeof(addr->val)] = {0};

    return memcmp(addr->val, ZERO_ADDRESS, sizeof(addr->val)) == 0;
}

const gateway_profile_t *gateway_profile_get(void)
{
    return &PROFILE;
}

uint8_t gateway_profile_enabled_target_count(
    const gateway_profile_t *profile)
{
    uint8_t count = 0U;
    size_t index;

    if (profile == NULL) {
        return 0U;
    }

    for (index = 0U; index < GATEWAY_PROFILE_TARGET_COUNT; index++) {
        if (profile->targets[index].enabled) {
            count++;
        }
    }

    return count;
}

bool gateway_profile_validate(const gateway_profile_t *profile)
{
    static const uint8_t EXPECTED_DEVICE_IDS[][GATEWAY_PROFILE_TARGET_COUNT] = {
        {1U, 2U, 3U, 4U},
        {5U, 6U, 7U, 0U},
        {8U, 9U, 10U, 0U},
    };
    static const uint16_t EXPECTED_INTERVAL_UNITS[] = {
        25U, /* A: 31.25 ms */
        26U, /* B: 32.5 ms */
        28U, /* C: 35 ms */
    };
    uint16_t expected_startup_delay_ms;
    size_t first;
    size_t second;

    if (profile == NULL || profile->name == NULL ||
        profile->gateway_id > 2U ||
        profile->preferred_conn_interval_units < 6U ||
        profile->preferred_conn_interval_units > 3200U ||
        profile->ble_startup_delay_ms > 10000U) {
        return false;
    }

    expected_startup_delay_ms =
        (uint16_t)(1500U * profile->gateway_id);
    if (profile->preferred_conn_interval_units !=
            EXPECTED_INTERVAL_UNITS[profile->gateway_id] ||
        profile->ble_startup_delay_ms !=
            expected_startup_delay_ms) {
        return false;
    }

    for (first = 0U; first < GATEWAY_PROFILE_TARGET_COUNT; first++) {
        const gateway_target_t *target = &profile->targets[first];
        uint8_t expected_device_id =
            EXPECTED_DEVICE_IDS[profile->gateway_id][first];

        if (target->expected_device_id != expected_device_id) {
            return false;
        }

        if (!target->enabled) {
            if (expected_device_id != 0U ||
                !address_is_zero(&target->addr)) {
                return false;
            }
            continue;
        }

        if (expected_device_id == 0U || address_is_zero(&target->addr) ||
            (target->addr.type != BLE_ADDR_PUBLIC &&
             target->addr.type != BLE_ADDR_RANDOM)) {
            return false;
        }

        for (second = first + 1U;
             second < GATEWAY_PROFILE_TARGET_COUNT; second++) {
            const gateway_target_t *other = &profile->targets[second];

            if (other->enabled &&
                target->addr.type == other->addr.type &&
                memcmp(target->addr.val, other->addr.val,
                       sizeof(target->addr.val)) == 0) {
                return false;
            }
        }
    }

    return true;
}

#undef DISABLED_TARGET
#undef RANDOM_TARGET
