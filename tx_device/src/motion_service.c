#include "motion_service.h"

#include <string.h>

#define MOTION_BASE_UUID                                                     \
    {                                                                       \
        { 0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,                 \
          0x93, 0xf3, 0xa3, 0xb5, 0x00, 0x00, 0x41, 0x6e }                \
    }

static uint32_t data_characteristic_add(motion_service_t* service)
{
    ble_gatts_char_md_t char_md;
    ble_gatts_attr_md_t cccd_md;
    ble_gatts_attr_md_t attr_md;
    ble_gatts_attr_t attr_value;
    ble_uuid_t uuid;

    memset(&char_md, 0, sizeof(char_md));
    memset(&cccd_md, 0, sizeof(cccd_md));
    memset(&attr_md, 0, sizeof(attr_md));
    memset(&attr_value, 0, sizeof(attr_value));

    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&cccd_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&cccd_md.write_perm);
    cccd_md.vloc = BLE_GATTS_VLOC_STACK;

    char_md.char_props.notify = 1;
    char_md.p_cccd_md = &cccd_md;

    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&attr_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_NO_ACCESS(&attr_md.write_perm);
    attr_md.vloc = BLE_GATTS_VLOC_STACK;
    attr_md.vlen = 1;

    uuid.type = service->uuid_type;
    uuid.uuid = BLE_UUID_MOTION_DATA_CHARACTERISTIC;

    attr_value.p_uuid = &uuid;
    attr_value.p_attr_md = &attr_md;
    attr_value.init_len = 1U;
    attr_value.max_len = MOTION_PACKET_SIZE;

    return sd_ble_gatts_characteristic_add(service->service_handle,
                                           &char_md,
                                           &attr_value,
                                           &service->data_handles);
}

static uint32_t control_characteristic_add(motion_service_t* service)
{
    ble_gatts_char_md_t char_md;
    ble_gatts_attr_md_t attr_md;
    ble_gatts_attr_t attr_value;
    ble_uuid_t uuid;
    uint8_t initial_value = 0U;

    memset(&char_md, 0, sizeof(char_md));
    memset(&attr_md, 0, sizeof(attr_md));
    memset(&attr_value, 0, sizeof(attr_value));

    char_md.char_props.write = 1;

    BLE_GAP_CONN_SEC_MODE_SET_NO_ACCESS(&attr_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&attr_md.write_perm);
    attr_md.vloc = BLE_GATTS_VLOC_STACK;
    attr_md.vlen = 1;

    uuid.type = service->uuid_type;
    uuid.uuid = BLE_UUID_MOTION_CONTROL_CHARACTERISTIC;

    attr_value.p_uuid = &uuid;
    attr_value.p_attr_md = &attr_md;
    attr_value.init_len = 1U;
    attr_value.max_len =
      MOTION_CONTROL_HEADER_SIZE + MOTION_CONTROL_MAX_PAYLOAD;
    attr_value.p_value = &initial_value;

    return sd_ble_gatts_characteristic_add(service->service_handle,
                                           &char_md,
                                           &attr_value,
                                           &service->control_handles);
}

static uint32_t status_characteristic_add(motion_service_t* service)
{
    ble_gatts_char_md_t char_md;
    ble_gatts_attr_md_t cccd_md;
    ble_gatts_attr_md_t attr_md;
    ble_gatts_attr_t attr_value;
    ble_uuid_t uuid;
    uint8_t initial_value = 0U;

    memset(&char_md, 0, sizeof(char_md));
    memset(&cccd_md, 0, sizeof(cccd_md));
    memset(&attr_md, 0, sizeof(attr_md));
    memset(&attr_value, 0, sizeof(attr_value));

    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&cccd_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&cccd_md.write_perm);
    cccd_md.vloc = BLE_GATTS_VLOC_STACK;

    char_md.char_props.notify = 1;
    char_md.p_cccd_md = &cccd_md;

    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&attr_md.read_perm);
    BLE_GAP_CONN_SEC_MODE_SET_NO_ACCESS(&attr_md.write_perm);
    attr_md.vloc = BLE_GATTS_VLOC_STACK;
    attr_md.vlen = 1;

    uuid.type = service->uuid_type;
    uuid.uuid = BLE_UUID_MOTION_STATUS_CHARACTERISTIC;

    attr_value.p_uuid = &uuid;
    attr_value.p_attr_md = &attr_md;
    attr_value.init_len = 1U;
    attr_value.max_len = MOTION_RESPONSE_MAX_SIZE;
    attr_value.p_value = &initial_value;

    return sd_ble_gatts_characteristic_add(service->service_handle,
                                           &char_md,
                                           &attr_value,
                                           &service->status_handles);
}

uint32_t motion_service_init(motion_service_t* service,
                             motion_control_handler_t control_handler)
{
    uint32_t err_code;
    ble_uuid_t uuid;
    ble_uuid128_t base_uuid = MOTION_BASE_UUID;

    if (service == NULL) {
        return NRF_ERROR_NULL;
    }

    memset(service, 0, sizeof(*service));
    service->conn_handle = BLE_CONN_HANDLE_INVALID;
    service->control_handler = control_handler;

    err_code = sd_ble_uuid_vs_add(&base_uuid, &service->uuid_type);
    if (err_code != NRF_SUCCESS) {
        return err_code;
    }

    uuid.type = service->uuid_type;
    uuid.uuid = BLE_UUID_MOTION_SERVICE;
    err_code = sd_ble_gatts_service_add(BLE_GATTS_SRVC_TYPE_PRIMARY,
                                        &uuid,
                                        &service->service_handle);
    if (err_code != NRF_SUCCESS) {
        return err_code;
    }

    err_code = data_characteristic_add(service);
    if (err_code != NRF_SUCCESS) {
        return err_code;
    }
    err_code = control_characteristic_add(service);
    if (err_code != NRF_SUCCESS) {
        return err_code;
    }
    return status_characteristic_add(service);
}

void motion_service_on_ble_evt(motion_service_t* service, ble_evt_t* event)
{
    ble_gatts_evt_write_t* write;

    if ((service == NULL) || (event == NULL)) {
        return;
    }

    switch (event->header.evt_id) {
        case BLE_GAP_EVT_CONNECTED:
            service->conn_handle = event->evt.gap_evt.conn_handle;
            break;

        case BLE_GAP_EVT_DISCONNECTED:
            service->conn_handle = BLE_CONN_HANDLE_INVALID;
            service->notification_enabled = false;
            service->status_notification_enabled = false;
            break;

        case BLE_GATTS_EVT_WRITE:
            write = &event->evt.gatts_evt.params.write;
            if ((write->handle == service->data_handles.cccd_handle) &&
                (write->len == 2U)) {
                service->notification_enabled =
                  ble_srv_is_notification_enabled(write->data);
            } else if ((write->handle ==
                        service->status_handles.cccd_handle) &&
                       (write->len == 2U)) {
                service->status_notification_enabled =
                  ble_srv_is_notification_enabled(write->data);
            } else if ((write->handle ==
                        service->control_handles.value_handle) &&
                       (service->control_handler != NULL)) {
                service->control_handler(write->data, write->len);
            }
            break;

        default:
            break;
    }
}

uint32_t motion_service_notify(motion_service_t* service,
                               const uint8_t packet[MOTION_PACKET_SIZE])
{
    ble_gatts_hvx_params_t hvx;
    uint16_t length = MOTION_PACKET_SIZE;

    if ((service == NULL) || (packet == NULL)) {
        return NRF_ERROR_NULL;
    }
    if ((service->conn_handle == BLE_CONN_HANDLE_INVALID) ||
        (!service->notification_enabled)) {
        return NRF_ERROR_INVALID_STATE;
    }

    memset(&hvx, 0, sizeof(hvx));
    hvx.handle = service->data_handles.value_handle;
    hvx.type = BLE_GATT_HVX_NOTIFICATION;
    hvx.offset = 0U;
    hvx.p_len = &length;
    hvx.p_data = (uint8_t*)packet;
    return sd_ble_gatts_hvx(service->conn_handle, &hvx);
}

uint32_t motion_service_status_notify(motion_service_t* service,
                                      const uint8_t* response,
                                      uint16_t response_length)
{
    ble_gatts_hvx_params_t hvx;
    uint16_t length = response_length;

    if ((service == NULL) || (response == NULL)) {
        return NRF_ERROR_NULL;
    }
    if ((response_length == 0U) ||
        (response_length > MOTION_RESPONSE_MAX_SIZE)) {
        return NRF_ERROR_DATA_SIZE;
    }
    if ((service->conn_handle == BLE_CONN_HANDLE_INVALID) ||
        (!service->status_notification_enabled)) {
        return NRF_ERROR_INVALID_STATE;
    }

    memset(&hvx, 0, sizeof(hvx));
    hvx.handle = service->status_handles.value_handle;
    hvx.type = BLE_GATT_HVX_NOTIFICATION;
    hvx.offset = 0U;
    hvx.p_len = &length;
    hvx.p_data = (uint8_t*)response;
    return sd_ble_gatts_hvx(service->conn_handle, &hvx);
}
