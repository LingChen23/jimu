#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "motion_protocol.h"

#define TARGET_DEVICE_NAME       "SmartBlock"
#define SCAN_DURATION_MS         BLE_HS_FOREVER
#define CONNECT_TIMEOUT_MS       30000
#define MAX_MAHJONG_CONNECTIONS  1U
#define INVALID_PEER_INDEX       UINT8_MAX
#define GATEWAY_ID               0U
#define GATEWAY_FRAME_VERSION    1U
#define GATEWAY_FRAME_MOTION     0x10U
#define GATEWAY_FRAME_CONTROL_REQUEST  0x20U
#define GATEWAY_FRAME_CONTROL_RESPONSE 0x21U
#define GATEWAY_HEADER_SIZE      10U
#define GATEWAY_CRC_SIZE         4U
#define GATEWAY_MAX_PAYLOAD_SIZE MOTION_PACKET_SIZE
#define GATEWAY_MAX_RAW_FRAME_SIZE \
    (GATEWAY_HEADER_SIZE + GATEWAY_MAX_PAYLOAD_SIZE + GATEWAY_CRC_SIZE)
#define GATEWAY_COBS_MAX_SIZE    (GATEWAY_MAX_RAW_FRAME_SIZE + 1U)
#define GATEWAY_TX_QUEUE_DEPTH   128U
#define GATEWAY_USB_RX_BUFFER    256U
#define GATEWAY_USB_TX_BUFFER    32768U
#define GATEWAY_USB_WRITE_WAIT_MS 1000U
#define MOTION_CONTROL_VERSION   1U
#define MOTION_CONTROL_HEADER_SIZE 4U
#define MOTION_RESPONSE_HEADER_SIZE 5U
#define MOTION_CONTROL_MAX_SIZE  20U
#define MOTION_RESPONSE_MAX_SIZE 53U
#define MOTION_RESULT_BUSY       3U
#define MOTION_RESULT_DEVICE_OFFLINE 5U
#define MOTION_RESULT_GATEWAY_ERROR 6U

#define MAHJONG_COMPANY_ID_LSB   0xcd
#define MAHJONG_COMPANY_ID_MSB   0xab
#define MAHJONG_CONFIG_MAGIC_LSB 0xba
#define MAHJONG_CONFIG_MAGIC_MSB 0xab
#define MAHJONG_CONFIG_NO_OFFSET 6
#define MAHJONG_CONFIG_TYPE_OFFSET 7

static const char *TAG = "motion_gateway";

typedef struct {
    ble_addr_t addr;
    uint8_t expected_device_id;
} mahjong_target_t;

/* Single-node closure target: nRF52832 device 1. */
static const mahjong_target_t TARGETS[] = {
    {
        .addr = {
            .type = BLE_ADDR_RANDOM,
            .val = {0x4b, 0xf7, 0xe7, 0xe0, 0xcf, 0xe0},
        },
        .expected_device_id = 1,
    },
};

#define TARGET_COUNT (sizeof(TARGETS) / sizeof(TARGETS[0]))

_Static_assert(TARGET_COUNT <= MAX_MAHJONG_CONNECTIONS,
               "TARGETS exceeds MAX_MAHJONG_CONNECTIONS");

/* One 25 packet/s motion stream fits comfortably at a 20 ms interval. */
static const struct ble_gap_conn_params TARGET_CONN_PARAMS = {
    .scan_itvl = BLE_GAP_SCAN_FAST_INTERVAL_MIN,
    .scan_window = BLE_GAP_SCAN_FAST_WINDOW,
    .itvl_min = BLE_GAP_CONN_ITVL_MS(20),
    .itvl_max = BLE_GAP_CONN_ITVL_MS(20),
    .latency = 0,
    .supervision_timeout = BLE_GAP_SUPERVISION_TIMEOUT_MS(4000),
    .min_ce_len = 0,
    .max_ce_len = 0,
};

/* NimBLE stores 128-bit UUID bytes in little-endian order. */
static const ble_uuid_t *MOTION_SERVICE_UUID =
    BLE_UUID128_DECLARE(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                        0x93, 0xf3, 0xa3, 0xb5, 0x01, 0x00, 0x41, 0x6e);
static const ble_uuid_t *MOTION_DATA_UUID =
    BLE_UUID128_DECLARE(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                        0x93, 0xf3, 0xa3, 0xb5, 0x02, 0x00, 0x41, 0x6e);
static const ble_uuid_t *MOTION_CONTROL_UUID =
    BLE_UUID128_DECLARE(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                        0x93, 0xf3, 0xa3, 0xb5, 0x03, 0x00, 0x41, 0x6e);
static const ble_uuid_t *MOTION_STATUS_UUID =
    BLE_UUID128_DECLARE(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                        0x93, 0xf3, 0xa3, 0xb5, 0x04, 0x00, 0x41, 0x6e);
static const ble_uuid_t *CCCD_UUID = BLE_UUID16_DECLARE(0x2902);

typedef enum {
    PEER_FREE,
    PEER_CONNECTING,
    PEER_DISCOVERING,
    PEER_READY,
} peer_state_t;

typedef struct {
    peer_state_t state;
    uint8_t target_index;
    uint16_t conn_handle;
    ble_addr_t addr;
    uint16_t service_start_handle;
    uint16_t service_end_handle;
    uint16_t notify_def_handle;
    uint16_t notify_value_handle;
    uint16_t notify_cccd_handle;
    uint16_t control_def_handle;
    uint16_t control_value_handle;
    uint16_t status_def_handle;
    uint16_t status_value_handle;
    uint16_t status_cccd_handle;
    bool command_in_flight;
    uint8_t pending_command;
    uint8_t pending_request_id;
    bool has_board_info;
    uint8_t board_no;
    uint8_t board_type;
    bool has_sequence;
    uint16_t last_sequence;
    uint32_t received_frames;
    uint32_t lost_frames;
    uint32_t invalid_frames;
} mahjong_peer_t;

typedef struct {
    uint8_t target_index;
    bool has_board_info;
    uint8_t board_no;
    uint8_t board_type;
} advertisement_match_t;

static uint8_t s_own_addr_type;
static bool s_scanning;
static uint8_t s_connecting_peer = INVALID_PEER_INDEX;
static mahjong_peer_t s_peers[MAX_MAHJONG_CONNECTIONS];
static QueueHandle_t s_gateway_tx_queue;
static uint32_t s_gateway_sequence;
static volatile uint32_t s_gateway_queue_drops;

typedef struct {
    uint8_t frame_type;
    uint16_t payload_length;
    uint8_t payload[GATEWAY_MAX_PAYLOAD_SIZE];
} gateway_tx_item_t;

static int gap_event_handler(struct ble_gap_event *event, void *arg);
static void start_scan(void);
static void restart_after_discovery_error(mahjong_peer_t *peer,
                                          const char *stage,
                                          int status);
static int service_discovery_cb(uint16_t conn_handle,
                                const struct ble_gatt_error *error,
                                const struct ble_gatt_svc *service,
                                void *arg);
static void gateway_usb_rx_task(void *param);

static void gateway_put_le16(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
}

static void gateway_put_le32(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
    dst[2] = (uint8_t)(value >> 16);
    dst[3] = (uint8_t)(value >> 24);
}

static uint16_t gateway_get_le16(const uint8_t *src)
{
    return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

static uint32_t gateway_get_le32(const uint8_t *src)
{
    return (uint32_t)src[0] |
           ((uint32_t)src[1] << 8) |
           ((uint32_t)src[2] << 16) |
           ((uint32_t)src[3] << 24);
}

static uint32_t gateway_crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xffffffffU;
    size_t index;
    uint8_t bit;

    for (index = 0; index < length; index++) {
        crc ^= data[index];
        for (bit = 0; bit < 8U; bit++) {
            crc = (crc >> 1) ^
                  ((crc & 1U) ? 0xedb88320U : 0U);
        }
    }
    return crc ^ 0xffffffffU;
}

static size_t cobs_encode(const uint8_t *input,
                          size_t input_length,
                          uint8_t *output)
{
    size_t read_index = 0U;
    size_t write_index = 1U;
    size_t code_index = 0U;
    uint8_t code = 1U;

    while (read_index < input_length) {
        if (input[read_index] == 0U) {
            output[code_index] = code;
            code = 1U;
            code_index = write_index++;
            read_index++;
        } else {
            output[write_index++] = input[read_index++];
            code++;
            if (code == 0xffU) {
                output[code_index] = code;
                code = 1U;
                code_index = write_index++;
            }
        }
    }
    output[code_index] = code;
    return write_index;
}

static bool cobs_decode(const uint8_t *input,
                        size_t input_length,
                        uint8_t *output,
                        size_t output_capacity,
                        size_t *output_length)
{
    size_t read_index = 0U;
    size_t write_index = 0U;

    while (read_index < input_length) {
        uint8_t code = input[read_index++];
        uint8_t copy_length;

        if (code == 0U) {
            return false;
        }
        copy_length = (uint8_t)(code - 1U);
        if ((read_index + copy_length > input_length) ||
            (write_index + copy_length > output_capacity)) {
            return false;
        }
        memcpy(&output[write_index], &input[read_index], copy_length);
        read_index += copy_length;
        write_index += copy_length;
        if ((code != 0xffU) && (read_index < input_length)) {
            if (write_index >= output_capacity) {
                return false;
            }
            output[write_index++] = 0U;
        }
    }
    *output_length = write_index;
    return true;
}

static bool gateway_queue_frame(uint8_t frame_type,
                                const uint8_t *payload,
                                uint16_t payload_length)
{
    gateway_tx_item_t item;

    if (payload_length > sizeof(item.payload)) {
        return false;
    }
    item.frame_type = frame_type;
    item.payload_length = payload_length;
    if (payload_length > 0U) {
        memcpy(item.payload, payload, payload_length);
    }
    if (xQueueSend(s_gateway_tx_queue, &item, 0) != pdTRUE) {
        s_gateway_queue_drops++;
        return false;
    }
    return true;
}

static bool gateway_usb_write_all(const uint8_t *data, size_t length)
{
    size_t offset = 0U;

    while (offset < length) {
        int written = usb_serial_jtag_write_bytes(
            data + offset,
            length - offset,
            pdMS_TO_TICKS(GATEWAY_USB_WRITE_WAIT_MS));
        if (written <= 0) {
            return false;
        }
        offset += (size_t)written;
    }
    return true;
}

static void gateway_usb_tx_task(void *param)
{
    gateway_tx_item_t item;
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    uint8_t encoded[GATEWAY_COBS_MAX_SIZE + 1U];
    uint32_t crc;
    uint32_t gateway_sequence;
    size_t raw_length;
    size_t encoded_length;

    (void)param;
    for (;;) {
        if (xQueueReceive(s_gateway_tx_queue, &item, portMAX_DELAY) != pdTRUE) {
            continue;
        }

        raw[0] = GATEWAY_FRAME_VERSION;
        raw[1] = GATEWAY_ID;
        raw[2] = item.frame_type;
        raw[3] = 0U;
        gateway_sequence = s_gateway_sequence++;
        gateway_put_le32(&raw[4], gateway_sequence);
        gateway_put_le16(&raw[8], item.payload_length);
        memcpy(&raw[GATEWAY_HEADER_SIZE], item.payload, item.payload_length);
        raw_length = GATEWAY_HEADER_SIZE + item.payload_length +
                     GATEWAY_CRC_SIZE;
        crc = gateway_crc32(raw, raw_length - GATEWAY_CRC_SIZE);
        gateway_put_le32(&raw[raw_length - GATEWAY_CRC_SIZE], crc);

        encoded_length = cobs_encode(raw, raw_length, encoded);
        encoded[encoded_length++] = 0U;
        (void)gateway_usb_write_all(encoded, encoded_length);
    }
}

static void gateway_usb_init(void)
{
    usb_serial_jtag_driver_config_t config = {
        .rx_buffer_size = GATEWAY_USB_RX_BUFFER,
        .tx_buffer_size = GATEWAY_USB_TX_BUFFER,
    };
    const uint8_t delimiter = 0U;

    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&config));
    (void)gateway_usb_write_all(&delimiter, 1U);

    s_gateway_tx_queue =
        xQueueCreate(GATEWAY_TX_QUEUE_DEPTH, sizeof(gateway_tx_item_t));
    ESP_ERROR_CHECK(s_gateway_tx_queue != NULL
                        ? ESP_OK
                        : ESP_ERR_NO_MEM);
    if (xTaskCreate(gateway_usb_tx_task,
                    "gateway_usb_tx",
                    4096,
                    NULL,
                    8,
                    NULL) != pdPASS) {
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
    if (xTaskCreate(gateway_usb_rx_task,
                    "gateway_usb_rx",
                    4096,
                    NULL,
                    8,
                    NULL) != pdPASS) {
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
}

static void format_addr(const ble_addr_t *addr, char *buffer, size_t buffer_size)
{
    snprintf(buffer, buffer_size, "%02X:%02X:%02X:%02X:%02X:%02X",
             addr->val[5], addr->val[4], addr->val[3],
             addr->val[2], addr->val[1], addr->val[0]);
}

static void reset_peer(mahjong_peer_t *peer)
{
    memset(peer, 0, sizeof(*peer));
    peer->state = PEER_FREE;
    peer->target_index = INVALID_PEER_INDEX;
    peer->conn_handle = BLE_HS_CONN_HANDLE_NONE;
}

static void reset_all_peers(void)
{
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        reset_peer(&s_peers[index]);
    }
    s_scanning = false;
    s_connecting_peer = INVALID_PEER_INDEX;
}

static mahjong_peer_t *find_free_peer(uint8_t *peer_index)
{
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        if (s_peers[index].state == PEER_FREE) {
            if (peer_index != NULL) {
                *peer_index = index;
            }
            return &s_peers[index];
        }
    }
    return NULL;
}

static mahjong_peer_t *find_peer_by_conn_handle(uint16_t conn_handle)
{
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        if (s_peers[index].state != PEER_FREE &&
            s_peers[index].conn_handle == conn_handle) {
            return &s_peers[index];
        }
    }
    return NULL;
}

static mahjong_peer_t *find_peer_for_disconnect(
  const struct ble_gap_conn_desc *desc)
{
    mahjong_peer_t *peer = find_peer_by_conn_handle(desc->conn_handle);

    if (peer == NULL || peer->addr.type != desc->peer_ota_addr.type ||
        memcmp(peer->addr.val, desc->peer_ota_addr.val,
               sizeof(peer->addr.val)) != 0) {
        return NULL;
    }
    return peer;
}

static bool target_is_in_use(uint8_t target_index)
{
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        if (s_peers[index].state != PEER_FREE &&
            s_peers[index].target_index == target_index) {
            return true;
        }
    }
    return false;
}

static uint8_t active_peer_count(void)
{
    uint8_t count = 0;
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        if (s_peers[index].state != PEER_FREE) {
            count++;
        }
    }
    return count;
}

static uint8_t ready_peer_count(void)
{
    uint8_t count = 0;
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        if (s_peers[index].state == PEER_READY) {
            count++;
        }
    }
    return count;
}

static mahjong_peer_t *find_ready_peer_by_device_id(uint8_t device_id)
{
    uint8_t index;

    for (index = 0; index < MAX_MAHJONG_CONNECTIONS; index++) {
        if (s_peers[index].state == PEER_READY &&
            TARGETS[s_peers[index].target_index].expected_device_id ==
                device_id) {
            return &s_peers[index];
        }
    }
    return NULL;
}

static void gateway_queue_control_result(uint8_t device_id,
                                         uint8_t command,
                                         uint8_t request_id,
                                         uint8_t result)
{
    uint8_t payload[1U + MOTION_RESPONSE_HEADER_SIZE];

    payload[0] = device_id;
    payload[1] = MOTION_CONTROL_VERSION;
    payload[2] = command | 0x80U;
    payload[3] = request_id;
    payload[4] = result;
    payload[5] = 0U;
    (void)gateway_queue_frame(GATEWAY_FRAME_CONTROL_RESPONSE,
                              payload,
                              sizeof(payload));
}

static int command_write_cb(uint16_t conn_handle,
                            const struct ble_gatt_error *error,
                            struct ble_gatt_attr *attr,
                            void *arg)
{
    mahjong_peer_t *peer = arg;
    uint8_t device_id;

    (void)attr;
    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }
    if (error->status != 0 && peer->command_in_flight) {
        device_id = TARGETS[peer->target_index].expected_device_id;
        gateway_queue_control_result(device_id,
                                     peer->pending_command,
                                     peer->pending_request_id,
                                     MOTION_RESULT_GATEWAY_ERROR);
        peer->command_in_flight = false;
    }
    return 0;
}

static void gateway_handle_encoded_command(const uint8_t *encoded,
                                           size_t encoded_length)
{
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    uint8_t device_id;
    const uint8_t *request;
    uint16_t payload_length;
    uint16_t request_length;
    uint32_t expected_crc;
    size_t raw_length;
    mahjong_peer_t *peer;
    int rc;

    if (!cobs_decode(encoded, encoded_length, raw, sizeof(raw), &raw_length) ||
        raw_length < GATEWAY_HEADER_SIZE + GATEWAY_CRC_SIZE) {
        return;
    }
    payload_length = gateway_get_le16(&raw[8]);
    if (raw[0] != GATEWAY_FRAME_VERSION ||
        raw[2] != GATEWAY_FRAME_CONTROL_REQUEST ||
        raw_length != GATEWAY_HEADER_SIZE + payload_length +
                      GATEWAY_CRC_SIZE) {
        return;
    }
    expected_crc = gateway_get_le32(&raw[raw_length - GATEWAY_CRC_SIZE]);
    if (gateway_crc32(raw, raw_length - GATEWAY_CRC_SIZE) != expected_crc ||
        payload_length < 1U + MOTION_CONTROL_HEADER_SIZE) {
        return;
    }

    device_id = raw[GATEWAY_HEADER_SIZE];
    request = &raw[GATEWAY_HEADER_SIZE + 1U];
    request_length = (uint16_t)(payload_length - 1U);
    if (request_length > MOTION_CONTROL_MAX_SIZE ||
        request[0] != MOTION_CONTROL_VERSION ||
        request_length !=
            (uint16_t)(MOTION_CONTROL_HEADER_SIZE + request[3])) {
        return;
    }

    peer = find_ready_peer_by_device_id(device_id);
    if (peer == NULL) {
        gateway_queue_control_result(device_id,
                                     request[1],
                                     request[2],
                                     MOTION_RESULT_DEVICE_OFFLINE);
        return;
    }
    if (peer->command_in_flight) {
        gateway_queue_control_result(device_id,
                                     request[1],
                                     request[2],
                                     MOTION_RESULT_BUSY);
        return;
    }

    peer->command_in_flight = true;
    peer->pending_command = request[1];
    peer->pending_request_id = request[2];
    rc = ble_gattc_write_flat(peer->conn_handle,
                              peer->control_value_handle,
                              request,
                              request_length,
                              command_write_cb,
                              peer);
    if (rc != 0) {
        peer->command_in_flight = false;
        gateway_queue_control_result(device_id,
                                     request[1],
                                     request[2],
                                     MOTION_RESULT_GATEWAY_ERROR);
    }
}

static void gateway_usb_rx_task(void *param)
{
    uint8_t read_buffer[64];
    uint8_t encoded[GATEWAY_COBS_MAX_SIZE];
    size_t encoded_length = 0U;
    bool overflow = false;
    int read_length;
    int index;

    (void)param;
    for (;;) {
        read_length = usb_serial_jtag_read_bytes(read_buffer,
                                                 sizeof(read_buffer),
                                                 pdMS_TO_TICKS(100));
        if (read_length <= 0) {
            continue;
        }
        for (index = 0; index < read_length; index++) {
            if (read_buffer[index] == 0U) {
                if (!overflow && encoded_length > 0U) {
                    gateway_handle_encoded_command(encoded, encoded_length);
                }
                encoded_length = 0U;
                overflow = false;
            } else if (!overflow) {
                if (encoded_length < sizeof(encoded)) {
                    encoded[encoded_length++] = read_buffer[index];
                } else {
                    overflow = true;
                }
            }
        }
    }
}

static bool has_available_target(void)
{
    uint8_t target_index;

    if (find_free_peer(NULL) == NULL) {
        return false;
    }

    for (target_index = 0; target_index < TARGET_COUNT; target_index++) {
        if (!target_is_in_use(target_index)) {
            return true;
        }
    }
    return false;
}

static void consume_notification(mahjong_peer_t *peer, struct os_mbuf *om)
{
    uint8_t packet_bytes[MOTION_PACKET_SIZE];
    uint16_t packet_len = OS_MBUF_PKTLEN(om);
    motion_packet_t packet;
    uint16_t expected_sequence;
    uint16_t sequence_gap;
    uint8_t expected_device_id = TARGETS[peer->target_index].expected_device_id;

    if (packet_len != sizeof(packet_bytes)) {
        peer->invalid_frames++;
        ESP_LOGW(TAG, "Invalid notification length: conn=%u length=%u",
                 peer->conn_handle, packet_len);
        return;
    }

    if (os_mbuf_copydata(om, 0, packet_len, packet_bytes) != 0) {
        ESP_LOGE(TAG, "Failed to copy notification payload: conn=%u",
                 peer->conn_handle);
        return;
    }

    if (!motion_packet_decode(packet_bytes, packet_len, &packet)) {
        peer->invalid_frames++;
        ESP_LOGW(TAG, "Invalid Motion V1 packet header: conn=%u",
                 peer->conn_handle);
        return;
    }

    if (packet.device_id != expected_device_id) {
        peer->invalid_frames++;
        ESP_LOGW(TAG,
                 "Unexpected device id: conn=%u expected=%u actual=%u",
                 peer->conn_handle, expected_device_id, packet.device_id);
        return;
    }

    if (peer->has_sequence) {
        expected_sequence = (uint16_t)(peer->last_sequence + 1U);
        if (packet.packet_sequence != expected_sequence) {
            sequence_gap =
                (uint16_t)(packet.packet_sequence - expected_sequence);
            if (sequence_gap < 0x8000U) {
                peer->lost_frames += sequence_gap;
                ESP_LOGW(TAG,
                         "Sequence gap: conn=%u id=%u expected=%u actual=%u "
                         "lost=%u total_lost=%lu",
                         peer->conn_handle, packet.device_id,
                         expected_sequence, packet.packet_sequence,
                         sequence_gap,
                         (unsigned long)peer->lost_frames);
            } else {
                ESP_LOGW(TAG,
                         "Sequence restarted: conn=%u id=%u previous=%u actual=%u",
                         peer->conn_handle, packet.device_id,
                         peer->last_sequence, packet.packet_sequence);
            }
        }
    }

    peer->has_sequence = true;
    peer->last_sequence = packet.packet_sequence;
    peer->received_frames++;

    (void)gateway_queue_frame(GATEWAY_FRAME_MOTION,
                              packet_bytes,
                              sizeof(packet_bytes));
}

static void consume_status_notification(mahjong_peer_t *peer,
                                        struct os_mbuf *om)
{
    uint8_t payload[1U + MOTION_RESPONSE_MAX_SIZE];
    uint16_t response_length = OS_MBUF_PKTLEN(om);
    uint8_t device_id =
        TARGETS[peer->target_index].expected_device_id;

    if (response_length < MOTION_RESPONSE_HEADER_SIZE ||
        response_length > MOTION_RESPONSE_MAX_SIZE) {
        peer->invalid_frames++;
        return;
    }
    if (os_mbuf_copydata(om, 0, response_length, &payload[1]) != 0) {
        return;
    }
    if (payload[1] != MOTION_CONTROL_VERSION ||
        response_length !=
            (uint16_t)(MOTION_RESPONSE_HEADER_SIZE + payload[5])) {
        peer->invalid_frames++;
        return;
    }

    payload[0] = device_id;
    if (peer->command_in_flight &&
        payload[2] == (uint8_t)(peer->pending_command | 0x80U) &&
        payload[3] == peer->pending_request_id) {
        peer->command_in_flight = false;
    }
    (void)gateway_queue_frame(GATEWAY_FRAME_CONTROL_RESPONSE,
                              payload,
                              (uint16_t)(response_length + 1U));
}

static int mtu_exchange_cb(uint16_t conn_handle,
                           const struct ble_gatt_error *error,
                           uint16_t mtu,
                           void *arg)
{
    mahjong_peer_t *peer = arg;
    int rc;

    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }
    if (error->status != 0 || mtu < (MOTION_PACKET_SIZE + 3U)) {
        restart_after_discovery_error(peer, "MTU exchange",
                                      error->status != 0
                                          ? error->status
                                          : BLE_HS_EMSGSIZE);
        return 0;
    }

    ESP_LOGI(TAG, "MTU ready: conn=%u mtu=%u", conn_handle, mtu);
    rc = ble_gattc_disc_svc_by_uuid(conn_handle,
                                    MOTION_SERVICE_UUID,
                                    service_discovery_cb,
                                    peer);
    if (rc != 0) {
        restart_after_discovery_error(peer,
                                      "Motion service discovery start", rc);
    }
    return 0;
}

static void restart_after_discovery_error(mahjong_peer_t *peer,
                                          const char *stage,
                                          int status)
{
    int rc;

    if (peer == NULL || peer->state == PEER_FREE) {
        return;
    }

    ESP_LOGE(TAG, "%s failed: conn=%u status=%d",
             stage, peer->conn_handle, status);
    if (status == BLE_HS_ENOTCONN) {
        reset_peer(peer);
        start_scan();
        return;
    }

    rc = ble_gap_terminate(peer->conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    if (rc != 0 && rc != BLE_HS_ENOTCONN) {
        ESP_LOGE(TAG, "Failed to terminate connection: conn=%u rc=%d",
                 peer->conn_handle, rc);
    }
    if (rc == BLE_HS_ENOTCONN) {
        reset_peer(peer);
        start_scan();
    }
}

static int status_subscribe_write_cb(uint16_t conn_handle,
                                     const struct ble_gatt_error *error,
                                     struct ble_gatt_attr *attr,
                                     void *arg)
{
    mahjong_peer_t *peer = arg;
    char addr[18];

    (void)attr;

    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }

    if (error->status != 0) {
        restart_after_discovery_error(peer, "CCCD subscription",
                                      error->status);
        return 0;
    }

    peer->state = PEER_READY;
    format_addr(&peer->addr, addr, sizeof(addr));
    ESP_LOGI(TAG,
             "Motion/control notifications enabled: addr=%s conn=%u "
             "id=%u ready=%u/%u",
             addr, conn_handle,
             TARGETS[peer->target_index].expected_device_id,
             ready_peer_count(), (unsigned int)TARGET_COUNT);
    return 0;
}

static int data_subscribe_write_cb(uint16_t conn_handle,
                                   const struct ble_gatt_error *error,
                                   struct ble_gatt_attr *attr,
                                   void *arg)
{
    static const uint8_t enable_notifications[] = {0x01, 0x00};
    mahjong_peer_t *peer = arg;
    int rc;

    (void)attr;
    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }
    if (error->status != 0) {
        restart_after_discovery_error(peer, "Motion CCCD subscription",
                                      error->status);
        return 0;
    }
    rc = ble_gattc_write_flat(conn_handle,
                              peer->status_cccd_handle,
                              enable_notifications,
                              sizeof(enable_notifications),
                              status_subscribe_write_cb,
                              peer);
    if (rc != 0) {
        restart_after_discovery_error(peer,
                                      "Status CCCD write start", rc);
    }
    return 0;
}

static int descriptor_discovery_cb(uint16_t conn_handle,
                                   const struct ble_gatt_error *error,
                                   uint16_t chr_val_handle,
                                   const struct ble_gatt_dsc *dsc,
                                   void *arg)
{
    static const uint8_t enable_notifications[] = {0x01, 0x00};
    mahjong_peer_t *peer = arg;
    int rc;

    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }

    if (error->status == 0) {
        if (chr_val_handle == peer->notify_value_handle && dsc != NULL &&
            ble_uuid_cmp(&dsc->uuid.u, CCCD_UUID) == 0) {
            peer->notify_cccd_handle = dsc->handle;
        } else if (chr_val_handle == peer->status_value_handle &&
                   dsc != NULL &&
                   ble_uuid_cmp(&dsc->uuid.u, CCCD_UUID) == 0) {
            peer->status_cccd_handle = dsc->handle;
        }
        return 0;
    }

    if (error->status != BLE_HS_EDONE) {
        restart_after_discovery_error(peer, "Descriptor discovery",
                                      error->status);
        return 0;
    }

    if (chr_val_handle == peer->notify_value_handle &&
        peer->notify_cccd_handle != 0U &&
        peer->status_cccd_handle == 0U) {
        rc = ble_gattc_disc_all_dscs(conn_handle,
                                     peer->status_value_handle,
                                     peer->service_end_handle,
                                     descriptor_discovery_cb,
                                     peer);
        if (rc != 0) {
            restart_after_discovery_error(peer,
                                          "Status descriptor discovery start",
                                          rc);
        }
        return 0;
    }

    if (peer->notify_cccd_handle == 0 ||
        peer->status_cccd_handle == 0) {
        restart_after_discovery_error(peer, "Motion/status CCCD not found",
                                      BLE_HS_ENOENT);
        return 0;
    }

    rc = ble_gattc_write_flat(conn_handle, peer->notify_cccd_handle,
                              enable_notifications,
                              sizeof(enable_notifications),
                              data_subscribe_write_cb, peer);
    if (rc != 0) {
        restart_after_discovery_error(peer, "CCCD write start", rc);
    }
    return 0;
}

static int characteristic_discovery_cb(uint16_t conn_handle,
                                       const struct ble_gatt_error *error,
                                       const struct ble_gatt_chr *chr,
                                       void *arg)
{
    mahjong_peer_t *peer = arg;
    int rc;

    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }

    if (error->status == 0) {
        if (chr != NULL &&
            ble_uuid_cmp(&chr->uuid.u, MOTION_DATA_UUID) == 0) {
            peer->notify_def_handle = chr->def_handle;
            peer->notify_value_handle = chr->val_handle;
        } else if (chr != NULL &&
                   ble_uuid_cmp(&chr->uuid.u, MOTION_CONTROL_UUID) == 0) {
            peer->control_def_handle = chr->def_handle;
            peer->control_value_handle = chr->val_handle;
        } else if (chr != NULL &&
                   ble_uuid_cmp(&chr->uuid.u, MOTION_STATUS_UUID) == 0) {
            peer->status_def_handle = chr->def_handle;
            peer->status_value_handle = chr->val_handle;
        }
        return 0;
    }

    if (error->status != BLE_HS_EDONE) {
        restart_after_discovery_error(peer, "Characteristic discovery",
                                      error->status);
        return 0;
    }

    if (peer->notify_value_handle == 0 ||
        peer->control_value_handle == 0 ||
        peer->status_value_handle == 0) {
        restart_after_discovery_error(peer,
                                      "Motion characteristics not found",
                                      BLE_HS_ENOENT);
        return 0;
    }

    rc = ble_gattc_disc_all_dscs(conn_handle, peer->notify_value_handle,
                                 (uint16_t)(peer->control_def_handle - 1U),
                                 descriptor_discovery_cb, peer);
    if (rc != 0) {
        restart_after_discovery_error(peer,
                                      "Descriptor discovery start", rc);
    }
    return 0;
}

static int service_discovery_cb(uint16_t conn_handle,
                                const struct ble_gatt_error *error,
                                const struct ble_gatt_svc *service,
                                void *arg)
{
    mahjong_peer_t *peer = arg;
    int rc;

    if (peer == NULL || peer->state == PEER_FREE ||
        peer->conn_handle != conn_handle) {
        return 0;
    }

    if (error->status == 0) {
        if (service != NULL) {
            peer->service_start_handle = service->start_handle;
            peer->service_end_handle = service->end_handle;
        }
        return 0;
    }

    if (error->status != BLE_HS_EDONE) {
        restart_after_discovery_error(peer, "Service discovery",
                                      error->status);
        return 0;
    }

    if (peer->service_start_handle == 0 || peer->service_end_handle == 0) {
        restart_after_discovery_error(peer, "Motion service not found",
                                      BLE_HS_ENOENT);
        return 0;
    }

    rc = ble_gattc_disc_all_chrs(conn_handle,
                                 peer->service_start_handle,
                                 peer->service_end_handle,
                                 characteristic_discovery_cb, peer);
    if (rc != 0) {
        restart_after_discovery_error(peer,
                                      "Characteristic discovery start", rc);
    }
    return 0;
}

static void read_board_info(const struct ble_hs_adv_fields *fields,
                            advertisement_match_t *match)
{
    const uint8_t *mfg = fields->mfg_data;

    match->has_board_info = false;
    if (mfg == NULL || fields->mfg_data_len <= MAHJONG_CONFIG_TYPE_OFFSET) {
        return;
    }

    if (mfg[0] == MAHJONG_COMPANY_ID_LSB &&
        mfg[1] == MAHJONG_COMPANY_ID_MSB &&
        mfg[2] == MAHJONG_CONFIG_MAGIC_LSB &&
        mfg[3] == MAHJONG_CONFIG_MAGIC_MSB) {
        match->has_board_info = true;
        match->board_no = mfg[MAHJONG_CONFIG_NO_OFFSET];
        match->board_type = mfg[MAHJONG_CONFIG_TYPE_OFFSET];
    }
}

static bool matches_target(const struct ble_gap_disc_desc *disc,
                           advertisement_match_t *match)
{
    struct ble_hs_adv_fields fields;
    size_t target_len = strlen(TARGET_DEVICE_NAME);
    uint8_t target_index;
    int rc;

    for (target_index = 0; target_index < TARGET_COUNT; target_index++) {
        if (disc->addr.type == TARGETS[target_index].addr.type &&
            memcmp(disc->addr.val, TARGETS[target_index].addr.val,
                   sizeof(disc->addr.val)) == 0) {
            break;
        }
    }

    if (target_index == TARGET_COUNT || target_is_in_use(target_index)) {
        return false;
    }

    memset(&fields, 0, sizeof(fields));
    rc = ble_hs_adv_parse_fields(&fields, disc->data, disc->length_data);
    if (rc != 0 || fields.name == NULL || fields.name_len != target_len ||
        memcmp(fields.name, TARGET_DEVICE_NAME, target_len) != 0) {
        return false;
    }

    memset(match, 0, sizeof(*match));
    match->target_index = target_index;
    read_board_info(&fields, match);
    return true;
}

static void connect_target(const struct ble_gap_disc_desc *disc,
                           const advertisement_match_t *match)
{
    mahjong_peer_t *peer;
    uint8_t peer_index;
    char addr[18];
    int rc;

    peer = find_free_peer(&peer_index);
    if (peer == NULL) {
        return;
    }

    reset_peer(peer);
    peer->state = PEER_CONNECTING;
    peer->target_index = match->target_index;
    peer->addr = disc->addr;
    peer->has_board_info = match->has_board_info;
    peer->board_no = match->board_no;
    peer->board_type = match->board_type;
    s_connecting_peer = peer_index;
    format_addr(&peer->addr, addr, sizeof(addr));
    ESP_LOGI(TAG,
             "Found %s: addr=%s id=%u rssi=%d slot=%u",
             TARGET_DEVICE_NAME, addr,
             TARGETS[peer->target_index].expected_device_id,
             disc->rssi, peer_index);

    rc = ble_gap_disc_cancel();
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to stop scan: rc=%d", rc);
        reset_peer(peer);
        s_connecting_peer = INVALID_PEER_INDEX;
        return;
    }
    s_scanning = false;

    rc = ble_gap_connect(s_own_addr_type, &peer->addr, CONNECT_TIMEOUT_MS,
                         &TARGET_CONN_PARAMS, gap_event_handler, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to start connection: addr=%s rc=%d", addr, rc);
        reset_peer(peer);
        s_connecting_peer = INVALID_PEER_INDEX;
        start_scan();
    }
}

static void start_scan(void)
{
    struct ble_gap_disc_params params;
    int rc;

    if (s_scanning || s_connecting_peer != INVALID_PEER_INDEX ||
        !has_available_target()) {
        return;
    }

    memset(&params, 0, sizeof(params));
    params.passive = 0;
    params.filter_duplicates = 1;

    rc = ble_gap_disc(s_own_addr_type, SCAN_DURATION_MS, &params,
                      gap_event_handler, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to start scan: rc=%d", rc);
        s_scanning = false;
        return;
    }

    s_scanning = true;
    ESP_LOGI(TAG, "Scanning for %s targets: active=%u configured=%u max=%u",
             TARGET_DEVICE_NAME,
             active_peer_count(), (unsigned int)TARGET_COUNT,
             MAX_MAHJONG_CONNECTIONS);
}

static int gap_event_handler(struct ble_gap_event *event, void *arg)
{
    advertisement_match_t match;
    struct ble_gap_conn_desc desc;
    mahjong_peer_t *peer;
    char addr[18];
    int rc;

    (void)arg;

    switch (event->type) {
    case BLE_GAP_EVENT_DISC:
        if (s_scanning && matches_target(&event->disc, &match)) {
            connect_target(&event->disc, &match);
        }
        return 0;

    case BLE_GAP_EVENT_CONNECT:
        if (s_connecting_peer == INVALID_PEER_INDEX) {
            ESP_LOGW(TAG, "Connection event without a pending peer: status=%d",
                     event->connect.status);
            return 0;
        }

        peer = &s_peers[s_connecting_peer];
        s_connecting_peer = INVALID_PEER_INDEX;
        if (event->connect.status != 0) {
            format_addr(&peer->addr, addr, sizeof(addr));
            ESP_LOGW(TAG, "Connection failed: addr=%s status=%d",
                     addr, event->connect.status);
            reset_peer(peer);
            start_scan();
            return 0;
        }

        peer->conn_handle = event->connect.conn_handle;
        rc = ble_gap_conn_find(peer->conn_handle, &desc);
        if (rc == 0) {
            peer->addr = desc.peer_ota_addr;
        }
        format_addr(&peer->addr, addr, sizeof(addr));
        ESP_LOGI(TAG, "Connected: addr=%s conn=%u id=%u active=%u/%u",
                 addr, peer->conn_handle,
                 TARGETS[peer->target_index].expected_device_id,
                 active_peer_count(), (unsigned int)TARGET_COUNT);

        peer->state = PEER_DISCOVERING;
        rc = ble_gattc_exchange_mtu(peer->conn_handle,
                                    mtu_exchange_cb,
                                    peer);
        if (rc != 0) {
            restart_after_discovery_error(peer,
                                          "MTU exchange start", rc);
        }
        start_scan();
        return 0;

    case BLE_GAP_EVENT_DISCONNECT:
        peer = find_peer_for_disconnect(&event->disconnect.conn);
        if (peer == NULL) {
            ESP_LOGW(TAG, "Unknown connection disconnected: conn=%u reason=%d",
                     event->disconnect.conn.conn_handle,
                     event->disconnect.reason);
            return 0;
        }
        format_addr(&peer->addr, addr, sizeof(addr));
        ESP_LOGW(TAG,
                 "Disconnected: addr=%s conn=%u id=%u reason=%d "
                 "received=%lu lost=%lu invalid=%lu",
                 addr, peer->conn_handle,
                 TARGETS[peer->target_index].expected_device_id,
                 event->disconnect.reason,
                 (unsigned long)peer->received_frames,
                 (unsigned long)peer->lost_frames,
                 (unsigned long)peer->invalid_frames);
        if (peer->command_in_flight) {
            gateway_queue_control_result(
                TARGETS[peer->target_index].expected_device_id,
                peer->pending_command,
                peer->pending_request_id,
                MOTION_RESULT_DEVICE_OFFLINE);
        }
        reset_peer(peer);
        start_scan();
        return 0;

    case BLE_GAP_EVENT_DISC_COMPLETE:
        s_scanning = false;
        start_scan();
        return 0;

    case BLE_GAP_EVENT_NOTIFY_RX:
        peer = find_peer_by_conn_handle(event->notify_rx.conn_handle);
        if (peer != NULL && peer->state == PEER_READY) {
            if (event->notify_rx.attr_handle ==
                peer->notify_value_handle) {
                consume_notification(peer, event->notify_rx.om);
            } else if (event->notify_rx.attr_handle ==
                       peer->status_value_handle) {
                consume_status_notification(peer, event->notify_rx.om);
            }
        }
        return 0;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "MTU updated: conn=%u mtu=%u",
                 event->mtu.conn_handle, event->mtu.value);
        return 0;

    default:
        return 0;
    }
}

static void ble_on_reset(int reason)
{
    ESP_LOGE(TAG, "NimBLE host reset: reason=%d", reason);
    reset_all_peers();
}

static void ble_on_sync(void)
{
    int rc;

    rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGE(TAG, "No usable BLE address: rc=%d", rc);
        return;
    }

    rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to select own BLE address: rc=%d", rc);
        return;
    }

    start_scan();
}

static void nimble_host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void app_main(void)
{
    esp_err_t err;
    int rc;

    esp_log_level_set("*", ESP_LOG_NONE);
    gateway_usb_init();

    err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES ||
        err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    reset_all_peers();

    ESP_ERROR_CHECK(nimble_port_init());
    ble_svc_gap_init();

    rc = ble_svc_gap_device_name_set("ESP32S3-Motion-Gateway");
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to set local BLE name: rc=%d", rc);
    }

    ble_hs_cfg.reset_cb = ble_on_reset;
    ble_hs_cfg.sync_cb = ble_on_sync;

    ESP_LOGI(TAG,
             "ESP32-S3 Motion V1 gateway started: targets=%u max_links=%u",
             (unsigned int)TARGET_COUNT, MAX_MAHJONG_CONNECTIONS);
    nimble_port_freertos_init(nimble_host_task);
}
