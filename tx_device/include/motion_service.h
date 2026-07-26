#ifndef MOTION_SERVICE_H
#define MOTION_SERVICE_H

#include <stdbool.h>
#include <stdint.h>

#include "ble.h"
#include "ble_srv_common.h"
#include "motion_control.h"
#include "motion_packet.h"

#define BLE_UUID_MOTION_SERVICE             0x0001U
#define BLE_UUID_MOTION_DATA_CHARACTERISTIC 0x0002U
#define BLE_UUID_MOTION_CONTROL_CHARACTERISTIC 0x0003U
#define BLE_UUID_MOTION_STATUS_CHARACTERISTIC  0x0004U

typedef void (*motion_control_handler_t)(const uint8_t* data,
                                         uint16_t length);

typedef struct
{
    uint16_t service_handle;
    ble_gatts_char_handles_t data_handles;
    ble_gatts_char_handles_t control_handles;
    ble_gatts_char_handles_t status_handles;
    uint8_t uuid_type;
    uint16_t conn_handle;
    bool notification_enabled;
    bool status_notification_enabled;
    motion_control_handler_t control_handler;
} motion_service_t;

uint32_t motion_service_init(motion_service_t* service,
                             motion_control_handler_t control_handler);
void motion_service_on_ble_evt(motion_service_t* service, ble_evt_t* event);
uint32_t motion_service_notify(motion_service_t* service,
                               const uint8_t packet[MOTION_PACKET_SIZE]);
uint32_t motion_service_status_notify(motion_service_t* service,
                                      const uint8_t* response,
                                      uint16_t response_length);

#endif
