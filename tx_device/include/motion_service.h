#ifndef MOTION_SERVICE_H
#define MOTION_SERVICE_H

#include <stdbool.h>
#include <stdint.h>

#include "ble.h"
#include "ble_srv_common.h"
#include "motion_packet.h"

#define BLE_UUID_MOTION_SERVICE             0x0001U
#define BLE_UUID_MOTION_DATA_CHARACTERISTIC 0x0002U

typedef struct
{
    uint16_t service_handle;
    ble_gatts_char_handles_t data_handles;
    uint8_t uuid_type;
    uint16_t conn_handle;
    bool notification_enabled;
} motion_service_t;

uint32_t motion_service_init(motion_service_t* service);
void motion_service_on_ble_evt(motion_service_t* service, ble_evt_t* event);
uint32_t motion_service_notify(motion_service_t* service,
                               const uint8_t packet[MOTION_PACKET_SIZE]);

#endif
