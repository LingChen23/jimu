#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_bt.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/ble_hs_hci.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "gateway_profile.h"
#include "gateway_transport.h"
#include "motion_protocol.h"

#define TARGET_DEVICE_NAME       "SmartBlock"
#define SCAN_DURATION_MS         1000
#define SCAN_INTERVAL_MS         100
#define SCAN_WINDOW_MS           10
#define SCAN_RETRY_DELAY_MS      4000
#define CONNECT_TIMEOUT_MS       30000
#define MAX_MAHJONG_CONNECTIONS  4U
#define INVALID_PEER_INDEX       UINT8_MAX
#define GATEWAY_FRAME_VERSION    1U
#define GATEWAY_FRAME_MOTION     0x10U
#define GATEWAY_FRAME_CONTROL_REQUEST  0x20U
#define GATEWAY_FRAME_CONTROL_RESPONSE 0x21U
#define GATEWAY_FRAME_LINK_DIAGNOSTIC  0x30U
#define GATEWAY_HEADER_SIZE      10U
#define GATEWAY_CRC_SIZE         4U
#define GATEWAY_MAX_PAYLOAD_SIZE MOTION_PACKET_SIZE
#define GATEWAY_MAX_RAW_FRAME_SIZE \
    (GATEWAY_HEADER_SIZE + GATEWAY_MAX_PAYLOAD_SIZE + GATEWAY_CRC_SIZE)
#define GATEWAY_COBS_MAX_SIZE    (GATEWAY_MAX_RAW_FRAME_SIZE + 1U)
#define GATEWAY_TX_QUEUE_DEPTH   128U
#define GATEWAY_TRANSPORT_WRITE_WAIT_MS 1000U
#define LINK_DIAGNOSTIC_VERSION  2U
#define LINK_DIAGNOSTIC_PAYLOAD_SIZE 64U
#define LINK_DIAGNOSTIC_PERIOD_MS 1000U
#define LINK_RATE_WINDOW_SIZE    10U
#define LINK_FLAG_CONNECTED      0x01U
#define LINK_FLAG_READY          0x02U
#define LINK_FLAG_RSSI_VALID     0x04U
#define LINK_FLAG_MTU_VALID      0x08U
#define LINK_FLAG_DLE_REQUESTED  0x10U
#define LINK_FLAG_DLE_VALID      0x20U
#define LINK_FLAG_PHY_VALID      0x40U
#define LINK_DLE_TX_OCTETS       251U
#define LINK_DLE_TX_TIME_US      2120U
#define DEFAULT_CONN_TX_POWER    ESP_PWR_LVL_N6
#define DEFAULT_MAX_CE_LEN_UNITS 8U  /* 5 ms for every connection */
#define BLE_CHANNEL_MAP_SIZE     5U
#define MOTION_CONTROL_VERSION   1U
#define MOTION_CONTROL_HEADER_SIZE 4U
#define MOTION_RESPONSE_HEADER_SIZE 5U
#define MOTION_CONTROL_MAX_SIZE  20U
#define MOTION_RESPONSE_MAX_SIZE 53U
#define MOTION_CMD_START_STREAM  0x01U
#define MOTION_CMD_STOP_STREAM   0x02U
#define MOTION_RESULT_SUCCESS    0U
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

#define TARGET_COUNT GATEWAY_PROFILE_TARGET_COUNT

_Static_assert(TARGET_COUNT <= MAX_MAHJONG_CONNECTIONS,
               "gateway profile exceeds MAX_MAHJONG_CONNECTIONS");

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
    bool connected;
    bool ready;
    uint16_t conn_handle;
    uint16_t conn_interval_units;
    int8_t rssi;
    bool rssi_valid;
    uint16_t last_disconnect_reason;
    uint32_t received_frames;
    uint32_t lost_frames;
    uint32_t invalid_frames;
    uint32_t connect_count;
    uint32_t disconnect_count;
    uint32_t conn_update_count;
    uint16_t att_mtu;
    bool mtu_valid;
    bool dle_requested;
    bool dle_valid;
    uint16_t max_tx_octets;
    uint16_t max_tx_time;
    uint16_t max_rx_octets;
    uint16_t max_rx_time;
    uint8_t tx_phy;
    uint8_t rx_phy;
    bool phy_valid;
    int16_t dle_request_result;
    int16_t phy_read_result;
    uint32_t dle_update_count;
} link_diagnostic_stats_t;

typedef struct {
    uint8_t target_index;
    bool has_board_info;
    uint8_t board_no;
    uint8_t board_type;
} advertisement_match_t;

static uint8_t s_own_addr_type;
static const gateway_profile_t *s_profile;
static bool s_scanning;
static uint8_t s_connecting_peer = INVALID_PEER_INDEX;
static esp_timer_handle_t s_scan_retry_timer;
static mahjong_peer_t s_peers[MAX_MAHJONG_CONNECTIONS];
static QueueHandle_t s_gateway_tx_queue;
static uint32_t s_gateway_sequence;
static volatile uint32_t s_gateway_queue_drops;
static link_diagnostic_stats_t s_link_stats[TARGET_COUNT];
static portMUX_TYPE s_link_stats_mux = portMUX_INITIALIZER_UNLOCKED;

#define TARGETS (s_profile->targets)
#define GATEWAY_ID (s_profile->gateway_id)

typedef struct {
    uint8_t frame_type;
    uint16_t payload_length;
    uint8_t payload[GATEWAY_MAX_PAYLOAD_SIZE];
} gateway_tx_item_t;

static int gap_event_handler(struct ble_gap_event *event, void *arg);
static void start_scan(void);
static void schedule_scan_retry(void);
static void restart_after_discovery_error(mahjong_peer_t *peer,
                                          const char *stage,
                                          int status);
static int service_discovery_cb(uint16_t conn_handle,
                                const struct ble_gatt_error *error,
                                const struct ble_gatt_svc *service,
                                void *arg);
static void gateway_transport_rx_task(void *param);
static void link_diagnostic_task(void *param);

static int configure_colocated_gateway_channels(void)
{
    uint8_t channel_map[BLE_CHANNEL_MAP_SIZE] = {0U};
    static const uint8_t channel_offset_by_gateway[] = {0U, 1U, 2U};
    uint8_t channel;

    if (GATEWAY_ID >= 3U) {
        return BLE_HS_EINVAL;
    }

    /*
     * Give each colocated controller a disjoint interleaved third of the 37
     * BLE data channels. Every profile retains full-band frequency diversity,
     * while two local gateways never use the same data-channel index.
     */
    for (channel = channel_offset_by_gateway[GATEWAY_ID];
         channel < 37U;
         channel = (uint8_t)(channel + 3U)) {
        channel_map[channel / 8U] |=
            (uint8_t)(1U << (channel % 8U));
    }
    return ble_hs_hci_set_chan_class(channel_map);
}

static uint16_t target_max_ce_len_units(uint8_t target_index)
{
    (void)target_index;
    return DEFAULT_MAX_CE_LEN_UNITS;
}

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

static void link_stats_snapshot(uint8_t target_index,
                                link_diagnostic_stats_t *snapshot)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    *snapshot = s_link_stats[target_index];
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_connect(uint8_t target_index,
                                  uint16_t conn_handle,
                                  uint16_t conn_interval_units)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].connected = true;
    s_link_stats[target_index].ready = false;
    s_link_stats[target_index].conn_handle = conn_handle;
    s_link_stats[target_index].conn_interval_units = conn_interval_units;
    s_link_stats[target_index].rssi_valid = false;
    s_link_stats[target_index].att_mtu = 0U;
    s_link_stats[target_index].mtu_valid = false;
    s_link_stats[target_index].dle_requested = false;
    s_link_stats[target_index].dle_valid = false;
    s_link_stats[target_index].max_tx_octets = 0U;
    s_link_stats[target_index].max_tx_time = 0U;
    s_link_stats[target_index].max_rx_octets = 0U;
    s_link_stats[target_index].max_rx_time = 0U;
    s_link_stats[target_index].tx_phy = 0U;
    s_link_stats[target_index].rx_phy = 0U;
    s_link_stats[target_index].phy_valid = false;
    s_link_stats[target_index].dle_request_result = 0;
    s_link_stats[target_index].phy_read_result = 0;
    s_link_stats[target_index].connect_count++;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_ready(uint8_t target_index)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].ready = true;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_disconnect(uint8_t target_index, int reason)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].connected = false;
    s_link_stats[target_index].ready = false;
    s_link_stats[target_index].rssi_valid = false;
    s_link_stats[target_index].last_disconnect_reason = (uint16_t)reason;
    s_link_stats[target_index].disconnect_count++;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_conn_update(uint8_t target_index,
                                      uint16_t conn_interval_units)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].conn_interval_units = conn_interval_units;
    s_link_stats[target_index].conn_update_count++;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_mtu(uint8_t target_index, uint16_t att_mtu)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].att_mtu = att_mtu;
    s_link_stats[target_index].mtu_valid = true;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_dle_request(uint8_t target_index, int result)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].dle_requested = true;
    s_link_stats[target_index].dle_request_result = (int16_t)result;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_dle_update(uint8_t target_index,
                                     uint16_t max_tx_octets,
                                     uint16_t max_tx_time,
                                     uint16_t max_rx_octets,
                                     uint16_t max_rx_time)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].dle_valid = true;
    s_link_stats[target_index].max_tx_octets = max_tx_octets;
    s_link_stats[target_index].max_tx_time = max_tx_time;
    s_link_stats[target_index].max_rx_octets = max_rx_octets;
    s_link_stats[target_index].max_rx_time = max_rx_time;
    s_link_stats[target_index].dle_update_count++;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_phy(uint8_t target_index,
                              int result,
                              uint8_t tx_phy,
                              uint8_t rx_phy)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].phy_read_result = (int16_t)result;
    s_link_stats[target_index].phy_valid = (result == 0);
    if (result == 0) {
        s_link_stats[target_index].tx_phy = tx_phy;
        s_link_stats[target_index].rx_phy = rx_phy;
    }
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_motion(uint8_t target_index,
                                 uint32_t sequence_gap)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].received_frames++;
    s_link_stats[target_index].lost_frames += sequence_gap;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_on_invalid(uint8_t target_index)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].invalid_frames++;
    portEXIT_CRITICAL(&s_link_stats_mux);
}

static void link_stats_set_rssi(uint8_t target_index,
                                bool valid,
                                int8_t rssi)
{
    portENTER_CRITICAL(&s_link_stats_mux);
    s_link_stats[target_index].rssi_valid = valid;
    if (valid) {
        s_link_stats[target_index].rssi = rssi;
    }
    portEXIT_CRITICAL(&s_link_stats_mux);
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

static bool gateway_transport_write_all(const uint8_t *data, size_t length)
{
    size_t offset = 0U;

    while (offset < length) {
        int written = gateway_transport_write(
            data + offset,
            length - offset,
            pdMS_TO_TICKS(GATEWAY_TRANSPORT_WRITE_WAIT_MS));
        if (written <= 0) {
            return false;
        }
        offset += (size_t)written;
    }
    return true;
}

static void gateway_transport_tx_task(void *param)
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
        (void)gateway_transport_write_all(encoded, encoded_length);
    }
}

static uint16_t link_rate_centi_packets(uint32_t frame_count,
                                        uint32_t elapsed_ms)
{
    uint64_t rate;

    if (elapsed_ms == 0U) {
        return 0U;
    }
    rate = ((uint64_t)frame_count * 100000ULL +
            (uint64_t)elapsed_ms / 2ULL) /
           (uint64_t)elapsed_ms;
    return rate > UINT16_MAX ? UINT16_MAX : (uint16_t)rate;
}

static void link_diagnostic_task(void *param)
{
    uint32_t last_received[TARGET_COUNT] = {0};
    uint32_t frame_window[TARGET_COUNT][LINK_RATE_WINDOW_SIZE] = {{0}};
    uint32_t time_window_ms[TARGET_COUNT][LINK_RATE_WINDOW_SIZE] = {{0}};
    uint8_t window_index[TARGET_COUNT] = {0};
    uint8_t window_count[TARGET_COUNT] = {0};
    TickType_t last_tick = xTaskGetTickCount();
    TickType_t wake_tick = last_tick;
    link_diagnostic_stats_t stats;
    uint8_t payload[LINK_DIAGNOSTIC_PAYLOAD_SIZE];
    uint8_t target_index;
    uint8_t slot;
    uint8_t flags;
    uint32_t elapsed_ms;
    uint32_t received_delta;
    uint32_t window_frames;
    uint32_t window_ms;
    uint16_t rate_1s_centi;
    uint16_t rate_10s_centi;
    int8_t rssi;
    int rc;

    (void)param;
    for (;;) {
        vTaskDelayUntil(&wake_tick,
                        pdMS_TO_TICKS(LINK_DIAGNOSTIC_PERIOD_MS));
        TickType_t current_tick = xTaskGetTickCount();
        elapsed_ms = (uint32_t)((current_tick - last_tick) *
                                portTICK_PERIOD_MS);
        if (elapsed_ms == 0U) {
            elapsed_ms = LINK_DIAGNOSTIC_PERIOD_MS;
        }
        last_tick = current_tick;

        for (target_index = 0U; target_index < TARGET_COUNT;
             target_index++) {
            link_stats_snapshot(target_index, &stats);
            if (stats.connected) {
                rssi = 0;
                rc = ble_gap_conn_rssi(stats.conn_handle, &rssi);
                link_stats_set_rssi(target_index, rc == 0, rssi);
            } else {
                link_stats_set_rssi(target_index, false, 0);
            }
            link_stats_snapshot(target_index, &stats);

            received_delta =
                stats.received_frames - last_received[target_index];
            last_received[target_index] = stats.received_frames;
            slot = window_index[target_index];
            frame_window[target_index][slot] = received_delta;
            time_window_ms[target_index][slot] = elapsed_ms;
            window_index[target_index] =
                (uint8_t)((slot + 1U) % LINK_RATE_WINDOW_SIZE);
            if (window_count[target_index] < LINK_RATE_WINDOW_SIZE) {
                window_count[target_index]++;
            }

            window_frames = 0U;
            window_ms = 0U;
            for (slot = 0U; slot < window_count[target_index]; slot++) {
                window_frames += frame_window[target_index][slot];
                window_ms += time_window_ms[target_index][slot];
            }
            rate_1s_centi =
                link_rate_centi_packets(received_delta, elapsed_ms);
            rate_10s_centi =
                link_rate_centi_packets(window_frames, window_ms);

            flags = 0U;
            if (stats.connected) {
                flags |= LINK_FLAG_CONNECTED;
            }
            if (stats.ready) {
                flags |= LINK_FLAG_READY;
            }
            if (stats.rssi_valid) {
                flags |= LINK_FLAG_RSSI_VALID;
            }
            if (stats.mtu_valid) {
                flags |= LINK_FLAG_MTU_VALID;
            }
            if (stats.dle_requested) {
                flags |= LINK_FLAG_DLE_REQUESTED;
            }
            if (stats.dle_valid) {
                flags |= LINK_FLAG_DLE_VALID;
            }
            if (stats.phy_valid) {
                flags |= LINK_FLAG_PHY_VALID;
            }

            memset(payload, 0, sizeof(payload));
            payload[0] = LINK_DIAGNOSTIC_VERSION;
            payload[1] = TARGETS[target_index].expected_device_id;
            payload[2] = flags;
            payload[3] = (uint8_t)stats.rssi;
            gateway_put_le16(&payload[4], stats.conn_interval_units);
            gateway_put_le16(&payload[6], stats.last_disconnect_reason);
            gateway_put_le16(&payload[8], rate_1s_centi);
            gateway_put_le16(&payload[10], rate_10s_centi);
            gateway_put_le32(&payload[12], stats.received_frames);
            gateway_put_le32(&payload[16], stats.lost_frames);
            gateway_put_le32(&payload[20], stats.invalid_frames);
            gateway_put_le32(&payload[24], stats.connect_count);
            gateway_put_le32(&payload[28], stats.disconnect_count);
            gateway_put_le32(&payload[32], stats.conn_update_count);
            gateway_put_le32(&payload[36],
                             (uint32_t)(esp_timer_get_time() / 1000ULL));
            gateway_put_le32(&payload[40], s_gateway_queue_drops);
            gateway_put_le16(&payload[44], stats.att_mtu);
            gateway_put_le16(&payload[46], stats.max_tx_octets);
            gateway_put_le16(&payload[48], stats.max_tx_time);
            gateway_put_le16(&payload[50], stats.max_rx_octets);
            gateway_put_le16(&payload[52], stats.max_rx_time);
            payload[54] = stats.tx_phy;
            payload[55] = stats.rx_phy;
            gateway_put_le16(&payload[56],
                             (uint16_t)stats.dle_request_result);
            gateway_put_le16(&payload[58],
                             (uint16_t)stats.phy_read_result);
            gateway_put_le32(&payload[60], stats.dle_update_count);
            (void)gateway_queue_frame(GATEWAY_FRAME_LINK_DIAGNOSTIC,
                                      payload,
                                      sizeof(payload));
        }
    }
}

static void gateway_transport_tasks_init(void)
{
    const uint8_t delimiter = 0U;

    ESP_ERROR_CHECK(gateway_transport_init());
    (void)gateway_transport_write_all(&delimiter, 1U);

    s_gateway_tx_queue =
        xQueueCreate(GATEWAY_TX_QUEUE_DEPTH, sizeof(gateway_tx_item_t));
    ESP_ERROR_CHECK(s_gateway_tx_queue != NULL
                        ? ESP_OK
                        : ESP_ERR_NO_MEM);
    if (xTaskCreate(gateway_transport_tx_task,
                    "gateway_transport_tx",
                    4096,
                    NULL,
                    8,
                    NULL) != pdPASS) {
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
    if (xTaskCreate(gateway_transport_rx_task,
                    "gateway_transport_rx",
                    4096,
                    NULL,
                    8,
                    NULL) != pdPASS) {
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
    if (xTaskCreate(link_diagnostic_task,
                    "link_diagnostic",
                    4096,
                    NULL,
                    7,
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

static void gateway_transport_rx_task(void *param)
{
    uint8_t read_buffer[64];
    uint8_t encoded[GATEWAY_COBS_MAX_SIZE];
    size_t encoded_length = 0U;
    bool overflow = false;
    int read_length;
    int index;

    (void)param;
    for (;;) {
        read_length = gateway_transport_read(read_buffer,
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
        if (TARGETS[target_index].enabled &&
            !target_is_in_use(target_index)) {
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
    uint32_t link_sequence_gap = 0U;
    uint8_t expected_device_id = TARGETS[peer->target_index].expected_device_id;

    if (packet_len != sizeof(packet_bytes)) {
        peer->invalid_frames++;
        link_stats_on_invalid(peer->target_index);
        ESP_LOGW(TAG, "Invalid notification length: conn=%u length=%u",
                 peer->conn_handle, packet_len);
        return;
    }

    if (os_mbuf_copydata(om, 0, packet_len, packet_bytes) != 0) {
        peer->invalid_frames++;
        link_stats_on_invalid(peer->target_index);
        ESP_LOGE(TAG, "Failed to copy notification payload: conn=%u",
                 peer->conn_handle);
        return;
    }

    if (!motion_packet_decode(packet_bytes, packet_len, &packet)) {
        peer->invalid_frames++;
        link_stats_on_invalid(peer->target_index);
        ESP_LOGW(TAG, "Invalid Motion packet header: conn=%u",
                 peer->conn_handle);
        return;
    }

    if (packet.device_id != expected_device_id) {
        peer->invalid_frames++;
        link_stats_on_invalid(peer->target_index);
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
                link_sequence_gap = sequence_gap;
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
    link_stats_on_motion(peer->target_index, link_sequence_gap);

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
        link_stats_on_invalid(peer->target_index);
        return;
    }
    if (os_mbuf_copydata(om, 0, response_length, &payload[1]) != 0) {
        peer->invalid_frames++;
        link_stats_on_invalid(peer->target_index);
        return;
    }
    if (payload[1] != MOTION_CONTROL_VERSION ||
        response_length !=
            (uint16_t)(MOTION_RESPONSE_HEADER_SIZE + payload[5])) {
        peer->invalid_frames++;
        link_stats_on_invalid(peer->target_index);
        return;
    }

    payload[0] = device_id;
    if (peer->command_in_flight &&
        payload[2] == (uint8_t)(peer->pending_command | 0x80U) &&
        payload[3] == peer->pending_request_id) {
        if (payload[4] == MOTION_RESULT_SUCCESS &&
            (peer->pending_command == MOTION_CMD_START_STREAM ||
             peer->pending_command == MOTION_CMD_STOP_STREAM)) {
            /*
             * Sampling continues while streaming is stopped, so the next
             * transmitted packet legitimately has a later sequence number.
             * Establish a new baseline instead of reporting the intentional
             * pause as an over-the-air sequence gap.
             */
            peer->has_sequence = false;
        }
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

    link_stats_on_mtu(peer->target_index, mtu);
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
    link_stats_on_ready(peer->target_index);
    format_addr(&peer->addr, addr, sizeof(addr));
    ESP_LOGI(TAG,
             "Motion/control notifications enabled: addr=%s conn=%u "
             "id=%u ready=%u/%u",
             addr, conn_handle,
             TARGETS[peer->target_index].expected_device_id,
             ready_peer_count(),
             gateway_profile_enabled_target_count(s_profile));
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
        if (TARGETS[target_index].enabled &&
            disc->addr.type == TARGETS[target_index].addr.type &&
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

static void build_target_conn_params(struct ble_gap_conn_params *params,
                                     uint8_t target_index)
{
    memset(params, 0, sizeof(*params));
    params->scan_itvl = BLE_GAP_SCAN_FAST_INTERVAL_MIN;
    params->scan_window = BLE_GAP_SCAN_FAST_WINDOW;
    params->itvl_min = s_profile->preferred_conn_interval_units;
    params->itvl_max = s_profile->preferred_conn_interval_units;
    params->latency = 0U;
    params->supervision_timeout =
        BLE_GAP_SUPERVISION_TIMEOUT_MS(4000);
    params->min_ce_len = 0U;
    params->max_ce_len = target_max_ce_len_units(target_index);
}

static void connect_target(const struct ble_gap_disc_desc *disc,
                           const advertisement_match_t *match)
{
    struct ble_gap_conn_params conn_params;
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

    build_target_conn_params(&conn_params, match->target_index);
    rc = ble_gap_connect(s_own_addr_type, &peer->addr, CONNECT_TIMEOUT_MS,
                         &conn_params,
                         gap_event_handler, peer);
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

    if (s_scan_retry_timer != NULL) {
        (void)esp_timer_stop(s_scan_retry_timer);
    }

    memset(&params, 0, sizeof(params));
    params.passive = 1;
    params.filter_duplicates = 1;
    params.itvl = BLE_GAP_SCAN_ITVL_MS(SCAN_INTERVAL_MS);
    params.window = BLE_GAP_SCAN_WIN_MS(SCAN_WINDOW_MS);

    rc = ble_gap_disc(s_own_addr_type, SCAN_DURATION_MS, &params,
                      gap_event_handler, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to start scan: rc=%d", rc);
        s_scanning = false;
        schedule_scan_retry();
        return;
    }

    s_scanning = true;
    ESP_LOGI(TAG, "Scanning for %s targets: active=%u configured=%u max=%u",
             TARGET_DEVICE_NAME,
             active_peer_count(),
             gateway_profile_enabled_target_count(s_profile),
             MAX_MAHJONG_CONNECTIONS);
}

static void scan_retry_timer_callback(void *arg)
{
    (void)arg;
    start_scan();
}

static void schedule_scan_retry(void)
{
    esp_err_t err;

    if (s_scan_retry_timer == NULL || s_scanning ||
        s_connecting_peer != INVALID_PEER_INDEX ||
        !has_available_target()) {
        return;
    }

    (void)esp_timer_stop(s_scan_retry_timer);
    err = esp_timer_start_once(
        s_scan_retry_timer,
        (uint64_t)SCAN_RETRY_DELAY_MS * 1000ULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to schedule BLE scan retry: err=%d", err);
    }
}

static int gap_event_handler(struct ble_gap_event *event, void *arg)
{
    advertisement_match_t match;
    struct ble_gap_conn_desc desc;
    mahjong_peer_t *peer;
    char addr[18];
    uint8_t tx_phy;
    uint8_t rx_phy;
    int dle_rc;
    int phy_rc;
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
        link_stats_on_connect(peer->target_index,
                              peer->conn_handle,
                              rc == 0 ? desc.conn_itvl : 0U);
        dle_rc = ble_gap_set_data_len(peer->conn_handle,
                                      LINK_DLE_TX_OCTETS,
                                      LINK_DLE_TX_TIME_US);
        link_stats_on_dle_request(peer->target_index, dle_rc);
        if (dle_rc != 0) {
            ESP_LOGW(TAG,
                     "DLE request failed: conn=%u id=%u rc=%d",
                     peer->conn_handle,
                     TARGETS[peer->target_index].expected_device_id,
                     dle_rc);
        }
        tx_phy = 0U;
        rx_phy = 0U;
        phy_rc = ble_gap_read_le_phy(peer->conn_handle, &tx_phy, &rx_phy);
        link_stats_on_phy(peer->target_index, phy_rc, tx_phy, rx_phy);
        format_addr(&peer->addr, addr, sizeof(addr));
        ESP_LOGI(TAG, "Connected: addr=%s conn=%u id=%u active=%u/%u",
                 addr, peer->conn_handle,
                 TARGETS[peer->target_index].expected_device_id,
                 active_peer_count(),
                 gateway_profile_enabled_target_count(s_profile));

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
        link_stats_on_disconnect(peer->target_index,
                                 event->disconnect.reason);
        reset_peer(peer);
        start_scan();
        return 0;

    case BLE_GAP_EVENT_CONN_UPDATE:
        peer = find_peer_by_conn_handle(event->conn_update.conn_handle);
        if (peer != NULL && event->conn_update.status == 0 &&
            ble_gap_conn_find(event->conn_update.conn_handle, &desc) == 0) {
            link_stats_on_conn_update(peer->target_index, desc.conn_itvl);
        }
        return 0;

    case BLE_GAP_EVENT_CONN_UPDATE_REQ:
        peer = find_peer_by_conn_handle(
            event->conn_update_req.conn_handle);
        if (peer != NULL) {
            struct ble_gap_upd_params *params =
                event->conn_update_req.self_params;

            params->itvl_min =
                s_profile->preferred_conn_interval_units;
            params->itvl_max =
                s_profile->preferred_conn_interval_units;
            params->latency = 0U;
            params->supervision_timeout =
                BLE_GAP_SUPERVISION_TIMEOUT_MS(4000);
            params->min_ce_len = 0U;
            params->max_ce_len =
                target_max_ce_len_units(peer->target_index);
            ESP_LOGI(TAG,
                     "Connection parameter request: conn=%u id=%u "
                     "peer=%u-%u reply=%u ce_max=%u",
                     peer->conn_handle,
                     TARGETS[peer->target_index].expected_device_id,
                     event->conn_update_req.peer_params->itvl_min,
                     event->conn_update_req.peer_params->itvl_max,
                     params->itvl_min,
                     params->max_ce_len);
        }
        return 0;

    case BLE_GAP_EVENT_L2CAP_UPDATE_REQ:
        /*
         * Unlike BLE_GAP_EVENT_CONN_UPDATE_REQ, NimBLE does not provide a
         * writable self_params object for an L2CAP update request.  The
         * peripheral firmware uses this procedure, so dereferencing
         * self_params here would reset the gateway when its five-second
         * connection-parameter timer expires.  Accept the request as-is.
         * Production SmartBlock firmware requests the same fixed interval as
         * its gateway profile, so no interval change is expected.
         */
        peer = find_peer_by_conn_handle(
            event->conn_update_req.conn_handle);
        if (peer != NULL) {
            ESP_LOGI(TAG,
                     "L2CAP connection parameter request: conn=%u id=%u "
                     "peer=%u-%u expected=%u",
                     peer->conn_handle,
                     TARGETS[peer->target_index].expected_device_id,
                     event->conn_update_req.peer_params->itvl_min,
                     event->conn_update_req.peer_params->itvl_max,
                     s_profile->preferred_conn_interval_units);
        }
        return 0;

    case BLE_GAP_EVENT_DATA_LEN_CHG:
        /*
         * ESP-IDF 5.2.3's NimBLE host dispatches the correct connection
         * callback but does not populate data_len_chg.conn_handle for the
         * controller event. Use the per-connection callback argument first.
         */
        peer = (mahjong_peer_t *)arg;
        if (peer == NULL || peer->state == PEER_FREE) {
            peer = find_peer_by_conn_handle(
                event->data_len_chg.conn_handle);
        }
        if (peer != NULL && peer->state != PEER_FREE) {
            link_stats_on_dle_update(peer->target_index,
                                     event->data_len_chg.max_tx_octets,
                                     event->data_len_chg.max_tx_time,
                                     event->data_len_chg.max_rx_octets,
                                     event->data_len_chg.max_rx_time);
            ESP_LOGI(TAG,
                     "DLE ready: conn=%u tx=%u/%uus rx=%u/%uus",
                     peer->conn_handle,
                     event->data_len_chg.max_tx_octets,
                     event->data_len_chg.max_tx_time,
                     event->data_len_chg.max_rx_octets,
                     event->data_len_chg.max_rx_time);
        }
        return 0;

    case BLE_GAP_EVENT_PHY_UPDATE_COMPLETE:
        peer = find_peer_by_conn_handle(event->phy_updated.conn_handle);
        if (peer != NULL) {
            link_stats_on_phy(peer->target_index,
                              event->phy_updated.status,
                              event->phy_updated.tx_phy,
                              event->phy_updated.rx_phy);
        }
        return 0;

    case BLE_GAP_EVENT_DISC_COMPLETE:
        s_scanning = false;
        schedule_scan_retry();
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
        peer = find_peer_by_conn_handle(event->mtu.conn_handle);
        if (peer != NULL) {
            link_stats_on_mtu(peer->target_index, event->mtu.value);
        }
        ESP_LOGI(TAG, "MTU updated: conn=%u mtu=%u",
                 event->mtu.conn_handle, event->mtu.value);
        return 0;

    default:
        return 0;
    }
}

static void ble_on_reset(int reason)
{
    uint8_t target_index;
    link_diagnostic_stats_t stats;

    ESP_LOGE(TAG, "NimBLE host reset: reason=%d", reason);
    for (target_index = 0U; target_index < TARGET_COUNT; target_index++) {
        link_stats_snapshot(target_index, &stats);
        if (stats.connected) {
            link_stats_on_disconnect(target_index, reason);
        }
    }
    if (s_scan_retry_timer != NULL) {
        (void)esp_timer_stop(s_scan_retry_timer);
    }
    reset_all_peers();
}

static void ble_on_sync(void)
{
    esp_err_t power_err;
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

    power_err = esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_DEFAULT,
                                     DEFAULT_CONN_TX_POWER);
    if (power_err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set default BLE TX power: err=%d", power_err);
    }

    rc = configure_colocated_gateway_channels();
    if (rc != 0) {
        ESP_LOGE(TAG,
                 "Failed to configure gateway %s BLE channel class: rc=%d",
                 s_profile->name,
                 rc);
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
    const esp_timer_create_args_t scan_retry_timer_args = {
        .callback = scan_retry_timer_callback,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ble_scan_retry",
        .skip_unhandled_events = true,
    };
    esp_err_t err;
    int rc;

    s_profile = gateway_profile_get();
    ESP_ERROR_CHECK(gateway_profile_validate(s_profile)
                        ? ESP_OK
                        : ESP_ERR_INVALID_ARG);

    esp_log_level_set("*", ESP_LOG_NONE);
    gateway_transport_tasks_init();

    err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES ||
        err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    reset_all_peers();
    ESP_ERROR_CHECK(esp_timer_create(&scan_retry_timer_args,
                                     &s_scan_retry_timer));

    if (s_profile->ble_startup_delay_ms > 0U) {
        vTaskDelay(pdMS_TO_TICKS(s_profile->ble_startup_delay_ms));
    }

    ESP_ERROR_CHECK(nimble_port_init());
    ble_svc_gap_init();

    rc = ble_svc_gap_device_name_set("ESP32S3-Motion-Gateway");
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to set local BLE name: rc=%d", rc);
    }

    ble_hs_cfg.reset_cb = ble_on_reset;
    ble_hs_cfg.sync_cb = ble_on_sync;

    ESP_LOGI(TAG,
             "ESP32-S3 Motion V1 gateway %s started: id=%u "
             "enabled_targets=%u max_links=%u interval=%u units "
             "startup_delay=%u ms",
             s_profile->name, s_profile->gateway_id,
             gateway_profile_enabled_target_count(s_profile),
             MAX_MAHJONG_CONNECTIONS,
             s_profile->preferred_conn_interval_units,
             s_profile->ble_startup_delay_ms);
    nimble_port_freertos_init(nimble_host_task);
}
