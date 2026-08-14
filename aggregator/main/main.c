#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#define GATEWAY_FRAME_VERSION                  1U
#define GATEWAY_FRAME_MOTION                   0x10U
#define GATEWAY_FRAME_CONTROL_REQUEST          0x20U
#define GATEWAY_FRAME_CONTROL_RESPONSE         0x21U
#define GATEWAY_FRAME_LINK_DIAGNOSTIC          0x30U
#define GATEWAY_FRAME_AGGREGATOR_DIAGNOSTIC    0x31U
#define GATEWAY_FRAME_HOST_CONTROL             0x32U
#define GATEWAY_FRAME_HOST_CONTROL_RESPONSE    0x33U
#define AGGREGATOR_DIAGNOSTIC_GATEWAY_ID        0xffU
#define AGGREGATOR_DIAGNOSTIC_VERSION           1U
#define AGGREGATOR_HOST_CONTROL_VERSION          1U
#define AGGREGATOR_HOST_CONTROL_START            1U
#define AGGREGATOR_HOST_CONTROL_STOP             2U
#define AGGREGATOR_HOST_CONTROL_PAYLOAD_SIZE     8U

#define GATEWAY_HEADER_SIZE                    10U
#define GATEWAY_CRC_SIZE                       4U
#define GATEWAY_MAX_PAYLOAD_SIZE               100U
#define GATEWAY_MAX_RAW_FRAME_SIZE             \
    (GATEWAY_HEADER_SIZE + GATEWAY_MAX_PAYLOAD_SIZE + GATEWAY_CRC_SIZE)
#define GATEWAY_MAX_ENCODED_SIZE               (GATEWAY_MAX_RAW_FRAME_SIZE + 1U)
#define GATEWAY_MAX_WIRE_SIZE                  (GATEWAY_MAX_ENCODED_SIZE + 1U)

#define MOTION_PACKET_SIZE                     100U
#define MOTION_PROTOCOL_VERSION_V1             1U
#define MOTION_PROTOCOL_VERSION_V2             2U
#define MOTION_PACKET_TYPE_DATA                0x10U
#define MOTION_CONTROL_VERSION                 1U
#define MOTION_CONTROL_HEADER_SIZE             4U
#define MOTION_CONTROL_MAX_PAYLOAD_SIZE        16U
#define LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE        44U
#define LINK_DIAGNOSTIC_V2_PAYLOAD_SIZE        64U

#define AGGREGATOR_LINK_COUNT                  3U
#define DEVICE_ID_MIN                          1U
#define DEVICE_ID_MAX                          10U
#define NO_SOURCE_LINK                         0xffU
#define HOST_CONTROL_SOURCE                    0xfeU

#define USB_RX_DRIVER_BUFFER_SIZE              512U
#define USB_TX_DRIVER_BUFFER_SIZE              32768U
#define UART_COMMAND_QUEUE_DEPTH               16U
#define TRANSPORT_WRITE_WAIT_MS                1000U
#define UART_READ_WAIT_MS                      100U
#define MERGE_IDLE_WAIT_MS                     100U
#define AGGREGATOR_HEALTH_PERIOD_MS            1000U
#define LINK_ACTIVE_TIMEOUT_MS                 2500U
#define AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE     100U
#define AGGREGATOR_DIAGNOSTIC_HEADER_SIZE      28U
#define AGGREGATOR_DIAGNOSTIC_LINK_SIZE        24U

#if (CONFIG_SMARTBLOCK_AGGREGATOR_A_UART_PORT == \
     CONFIG_SMARTBLOCK_AGGREGATOR_B_UART_PORT) || \
    (CONFIG_SMARTBLOCK_AGGREGATOR_A_UART_PORT == \
     CONFIG_SMARTBLOCK_AGGREGATOR_C_UART_PORT) || \
    (CONFIG_SMARTBLOCK_AGGREGATOR_B_UART_PORT == \
     CONFIG_SMARTBLOCK_AGGREGATOR_C_UART_PORT)
#error "The three aggregator links must use different UART controllers"
#endif

typedef struct {
    uint16_t length;
    uint8_t source_link;
    uint8_t reserved;
    uint32_t host_session_id;
    uint8_t bytes[GATEWAY_MAX_WIRE_SIZE];
} wire_frame_t;

typedef struct {
    uint32_t uart_rx_bytes;
    uint32_t uart_tx_bytes;
    uint32_t valid_frames;
    uint32_t cobs_errors;
    uint32_t crc_errors;
    uint32_t frame_errors;
    uint32_t rx_queue_drops;
    uint32_t usb_output_drops;
    uint32_t valid_commands;
    uint32_t uart_output_drops;
    TickType_t last_valid_frame_tick;
    uint8_t rx_queue_high_watermark;
    bool saw_valid_frame;
} link_stats_t;

typedef struct {
    const char *name;
    uint8_t gateway_id;
    uint8_t device_id_min;
    uint8_t device_id_max;
    uart_port_t uart_port;
    int tx_gpio;
    int rx_gpio;
    QueueHandle_t rx_frame_queue;
    QueueHandle_t tx_command_queue;
    link_stats_t stats;
} link_context_t;

typedef struct {
    uint32_t usb_rx_bytes;
    uint32_t usb_tx_bytes;
    uint32_t usb_valid_commands;
    uint32_t usb_cobs_errors;
    uint32_t usb_crc_errors;
    uint32_t usb_frame_errors;
    uint32_t usb_output_drops;
    uint32_t usb_write_failures;
    uint16_t usb_queue_high_watermark;
} aggregator_stats_t;

typedef enum {
    FRAME_CHECK_OK = 0,
    FRAME_CHECK_COBS_ERROR,
    FRAME_CHECK_CRC_ERROR,
    FRAME_CHECK_FORMAT_ERROR,
} frame_check_result_t;

static link_context_t s_links[AGGREGATOR_LINK_COUNT] = {
    {
        .name = "A",
        .gateway_id = 0U,
        .device_id_min = 1U,
        .device_id_max = 4U,
        .uart_port =
            (uart_port_t)CONFIG_SMARTBLOCK_AGGREGATOR_A_UART_PORT,
        .tx_gpio = CONFIG_SMARTBLOCK_AGGREGATOR_A_TX_GPIO,
        .rx_gpio = CONFIG_SMARTBLOCK_AGGREGATOR_A_RX_GPIO,
    },
    {
        .name = "B",
        .gateway_id = 1U,
        .device_id_min = 5U,
        .device_id_max = 7U,
        .uart_port =
            (uart_port_t)CONFIG_SMARTBLOCK_AGGREGATOR_B_UART_PORT,
        .tx_gpio = CONFIG_SMARTBLOCK_AGGREGATOR_B_TX_GPIO,
        .rx_gpio = CONFIG_SMARTBLOCK_AGGREGATOR_B_RX_GPIO,
    },
    {
        .name = "C",
        .gateway_id = 2U,
        .device_id_min = 8U,
        .device_id_max = 10U,
        .uart_port =
            (uart_port_t)CONFIG_SMARTBLOCK_AGGREGATOR_C_UART_PORT,
        .tx_gpio = CONFIG_SMARTBLOCK_AGGREGATOR_C_TX_GPIO,
        .rx_gpio = CONFIG_SMARTBLOCK_AGGREGATOR_C_RX_GPIO,
    },
};

static const char *const s_uart_rx_task_names[AGGREGATOR_LINK_COUNT] = {
    "agg_A_uart_rx",
    "agg_B_uart_rx",
    "agg_C_uart_rx",
};

static const char *const s_uart_tx_task_names[AGGREGATOR_LINK_COUNT] = {
    "agg_A_uart_tx",
    "agg_B_uart_tx",
    "agg_C_uart_tx",
};

static QueueHandle_t s_usb_tx_queue;
static TaskHandle_t s_merge_task_handle;
static aggregator_stats_t s_stats;
static uint32_t s_health_sequence;
static uint32_t s_host_session_id;
static bool s_host_stream_active;
static portMUX_TYPE s_stats_mux = portMUX_INITIALIZER_UNLOCKED;

static uint16_t get_le16(const uint8_t *src)
{
    return (uint16_t)src[0] | ((uint16_t)src[1] << 8);
}

static uint32_t get_le32(const uint8_t *src)
{
    return (uint32_t)src[0] |
           ((uint32_t)src[1] << 8) |
           ((uint32_t)src[2] << 16) |
           ((uint32_t)src[3] << 24);
}

static void put_le16(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
}

static void put_le32(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
    dst[2] = (uint8_t)(value >> 16);
    dst[3] = (uint8_t)(value >> 24);
}

static uint16_t saturate_u16(uint32_t value)
{
    return value > UINT16_MAX ? UINT16_MAX : (uint16_t)value;
}

static uint8_t saturate_u8(uint32_t value)
{
    return value > UINT8_MAX ? UINT8_MAX : (uint8_t)value;
}

static uint32_t ticks_to_ms(TickType_t ticks)
{
    return (uint32_t)ticks * (uint32_t)portTICK_PERIOD_MS;
}

static bool host_stream_snapshot(uint32_t *session_id)
{
    bool active;

    portENTER_CRITICAL(&s_stats_mux);
    active = s_host_stream_active;
    *session_id = s_host_session_id;
    portEXIT_CRITICAL(&s_stats_mux);
    return active;
}

static void set_host_stream_state(bool active, uint32_t session_id)
{
    portENTER_CRITICAL(&s_stats_mux);
    s_host_stream_active = active;
    s_host_session_id = session_id;
    portEXIT_CRITICAL(&s_stats_mux);
}

static uint32_t crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xffffffffU;
    size_t index;
    uint8_t bit;

    for (index = 0U; index < length; index++) {
        crc ^= data[index];
        for (bit = 0U; bit < 8U; bit++) {
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
            code_index = write_index++;
            code = 1U;
            read_index++;
        } else {
            output[write_index++] = input[read_index++];
            code++;
            if (code == 0xffU) {
                output[code_index] = code;
                code_index = write_index++;
                code = 1U;
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

static frame_check_result_t validate_common_frame(
    const uint8_t *encoded,
    size_t encoded_length,
    uint8_t *raw,
    size_t *raw_length)
{
    uint16_t payload_length;
    uint32_t expected_crc;

    if (!cobs_decode(encoded,
                     encoded_length,
                     raw,
                     GATEWAY_MAX_RAW_FRAME_SIZE,
                     raw_length)) {
        return FRAME_CHECK_COBS_ERROR;
    }
    if (*raw_length < GATEWAY_HEADER_SIZE + GATEWAY_CRC_SIZE) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    payload_length = get_le16(&raw[8]);
    if (raw[0] != GATEWAY_FRAME_VERSION ||
        raw[3] != 0U ||
        payload_length > GATEWAY_MAX_PAYLOAD_SIZE ||
        *raw_length != GATEWAY_HEADER_SIZE + payload_length +
                       GATEWAY_CRC_SIZE) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    expected_crc = get_le32(&raw[*raw_length - GATEWAY_CRC_SIZE]);
    if (crc32(raw, *raw_length - GATEWAY_CRC_SIZE) != expected_crc) {
        return FRAME_CHECK_CRC_ERROR;
    }
    return FRAME_CHECK_OK;
}

static bool device_belongs_to_link(const link_context_t *link,
                                   uint8_t device_id)
{
    return device_id >= link->device_id_min &&
           device_id <= link->device_id_max;
}

static bool find_link_for_device(uint8_t device_id, uint8_t *link_index)
{
    uint8_t index;

    for (index = 0U; index < AGGREGATOR_LINK_COUNT; index++) {
        if (device_belongs_to_link(&s_links[index], device_id)) {
            *link_index = index;
            return true;
        }
    }
    return false;
}

static frame_check_result_t validate_gateway_output(
    const link_context_t *link,
    const uint8_t *encoded,
    size_t encoded_length)
{
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    const uint8_t *payload;
    size_t raw_length;
    uint16_t payload_length;
    uint8_t device_id;
    frame_check_result_t result;

    result = validate_common_frame(encoded,
                                   encoded_length,
                                   raw,
                                   &raw_length);
    if (result != FRAME_CHECK_OK) {
        return result;
    }
    if (raw[1] != link->gateway_id) {
        return FRAME_CHECK_FORMAT_ERROR;
    }

    payload_length = get_le16(&raw[8]);
    payload = &raw[GATEWAY_HEADER_SIZE];
    if (raw[2] == GATEWAY_FRAME_MOTION) {
        if (payload_length != MOTION_PACKET_SIZE ||
            (payload[0] != MOTION_PROTOCOL_VERSION_V1 &&
             payload[0] != MOTION_PROTOCOL_VERSION_V2) ||
            payload[1] != MOTION_PACKET_TYPE_DATA) {
            return FRAME_CHECK_FORMAT_ERROR;
        }
        device_id = payload[2];
    } else if (raw[2] == GATEWAY_FRAME_CONTROL_RESPONSE) {
        if (payload_length < 6U ||
            payload[1] != MOTION_CONTROL_VERSION ||
            (payload[2] & 0x80U) == 0U ||
            payload_length != (uint16_t)(6U + payload[5])) {
            return FRAME_CHECK_FORMAT_ERROR;
        }
        device_id = payload[0];
    } else if (raw[2] == GATEWAY_FRAME_LINK_DIAGNOSTIC) {
        if ((payload_length != LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE &&
             payload_length != LINK_DIAGNOSTIC_V2_PAYLOAD_SIZE) ||
            ((payload[0] == 1U) !=
             (payload_length == LINK_DIAGNOSTIC_V1_PAYLOAD_SIZE)) ||
            ((payload[0] == 2U) !=
             (payload_length == LINK_DIAGNOSTIC_V2_PAYLOAD_SIZE))) {
            return FRAME_CHECK_FORMAT_ERROR;
        }
        device_id = payload[1];
    } else {
        return FRAME_CHECK_FORMAT_ERROR;
    }

    if (!device_belongs_to_link(link, device_id)) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    return FRAME_CHECK_OK;
}

static frame_check_result_t prepare_pc_command(
    const uint8_t *encoded,
    size_t encoded_length,
    uint8_t *route_index,
    uint8_t *routed_encoded,
    size_t *routed_encoded_length)
{
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    const uint8_t *request;
    size_t raw_length;
    uint16_t payload_length;
    uint16_t request_length;
    uint8_t device_id;
    uint8_t route;
    frame_check_result_t result;

    result = validate_common_frame(encoded,
                                   encoded_length,
                                   raw,
                                   &raw_length);
    if (result != FRAME_CHECK_OK) {
        return result;
    }
    payload_length = get_le16(&raw[8]);
    if (raw[2] != GATEWAY_FRAME_CONTROL_REQUEST ||
        payload_length < 1U + MOTION_CONTROL_HEADER_SIZE) {
        return FRAME_CHECK_FORMAT_ERROR;
    }

    device_id = raw[GATEWAY_HEADER_SIZE];
    if (device_id < DEVICE_ID_MIN || device_id > DEVICE_ID_MAX) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    request = &raw[GATEWAY_HEADER_SIZE + 1U];
    request_length = (uint16_t)(payload_length - 1U);
    if (request[0] != MOTION_CONTROL_VERSION ||
        request[3] > MOTION_CONTROL_MAX_PAYLOAD_SIZE ||
        request_length !=
            (uint16_t)(MOTION_CONTROL_HEADER_SIZE + request[3])) {
        return FRAME_CHECK_FORMAT_ERROR;
    }

    if (!find_link_for_device(device_id, &route)) {
        return FRAME_CHECK_FORMAT_ERROR;
    }

    /*
     * The current PC command encoder uses gateway ID 0. Route by the globally
     * unique device ID, then normalize the header so gateways B and C also
     * receive a self-consistent frame if they later enforce gateway_id.
     */
    raw[1] = s_links[route].gateway_id;
    put_le32(&raw[raw_length - GATEWAY_CRC_SIZE],
             crc32(raw, raw_length - GATEWAY_CRC_SIZE));
    *routed_encoded_length =
        cobs_encode(raw, raw_length, routed_encoded);
    *route_index = route;
    return FRAME_CHECK_OK;
}

static bool queue_wire_frame(QueueHandle_t queue,
                             const uint8_t *encoded,
                             size_t encoded_length,
                             uint8_t source_link,
                             uint32_t host_session_id)
{
    wire_frame_t frame;

    if (encoded_length == 0U ||
        encoded_length + 1U > sizeof(frame.bytes)) {
        return false;
    }
    frame.length = (uint16_t)(encoded_length + 1U);
    frame.source_link = source_link;
    frame.reserved = 0U;
    frame.host_session_id = host_session_id;
    memcpy(frame.bytes, encoded, encoded_length);
    frame.bytes[encoded_length] = 0U;
    return xQueueSend(queue, &frame, 0) == pdTRUE;
}

static void reset_host_session_queues(void)
{
    uint8_t link_index;

    (void)xQueueReset(s_usb_tx_queue);
    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        (void)xQueueReset(s_links[link_index].rx_frame_queue);
        (void)xQueueReset(s_links[link_index].tx_command_queue);
    }
}

static void reset_host_session_queue_stats(void)
{
    uint8_t link_index;

    portENTER_CRITICAL(&s_stats_mux);
    s_stats.usb_output_drops = 0U;
    s_stats.usb_write_failures = 0U;
    s_stats.usb_queue_high_watermark = 0U;
    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        s_links[link_index].stats.rx_queue_drops = 0U;
        s_links[link_index].stats.usb_output_drops = 0U;
        s_links[link_index].stats.rx_queue_high_watermark = 0U;
    }
    portEXIT_CRITICAL(&s_stats_mux);
}

static bool queue_host_control_response(uint8_t command,
                                        uint32_t session_id)
{
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    uint8_t encoded[GATEWAY_MAX_ENCODED_SIZE];
    uint8_t *payload = &raw[GATEWAY_HEADER_SIZE];
    size_t raw_length;
    size_t encoded_length;

    raw[0] = GATEWAY_FRAME_VERSION;
    raw[1] = AGGREGATOR_DIAGNOSTIC_GATEWAY_ID;
    raw[2] = GATEWAY_FRAME_HOST_CONTROL_RESPONSE;
    raw[3] = 0U;
    put_le32(&raw[4], session_id);
    put_le16(&raw[8], AGGREGATOR_HOST_CONTROL_PAYLOAD_SIZE);
    payload[0] = AGGREGATOR_HOST_CONTROL_VERSION;
    payload[1] = command;
    payload[2] = 0U;
    payload[3] = 0U;
    put_le32(&payload[4], session_id);
    raw_length = GATEWAY_HEADER_SIZE +
                 AGGREGATOR_HOST_CONTROL_PAYLOAD_SIZE +
                 GATEWAY_CRC_SIZE;
    put_le32(&raw[raw_length - GATEWAY_CRC_SIZE],
             crc32(raw, raw_length - GATEWAY_CRC_SIZE));
    encoded_length = cobs_encode(raw, raw_length, encoded);
    return queue_wire_frame(s_usb_tx_queue,
                            encoded,
                            encoded_length,
                            HOST_CONTROL_SOURCE,
                            session_id);
}

static bool begin_host_session(uint32_t session_id)
{
    /*
     * Mark the old session inactive before touching queues. Any frame already
     * held by merge/USB tasks carries its old session ID and is discarded by
     * usb_tx_task. The response marker is queued before new gateway traffic is
     * enabled, so the PC can ignore every stale byte preceding that marker.
     */
    set_host_stream_state(false, session_id);
    reset_host_session_queues();
    reset_host_session_queue_stats();
    if (!queue_host_control_response(AGGREGATOR_HOST_CONTROL_START,
                                     session_id)) {
        return false;
    }
    set_host_stream_state(true, session_id);
    if (s_merge_task_handle != NULL) {
        xTaskNotifyGive(s_merge_task_handle);
    }
    return true;
}

static void end_host_session(uint32_t session_id)
{
    set_host_stream_state(false, session_id);
    reset_host_session_queues();
}

static frame_check_result_t handle_host_control(
    const uint8_t *raw,
    size_t raw_length)
{
    const uint8_t *payload = &raw[GATEWAY_HEADER_SIZE];
    uint16_t payload_length = get_le16(&raw[8]);
    uint32_t session_id;
    uint8_t command;

    (void)raw_length;
    if (raw[1] != AGGREGATOR_DIAGNOSTIC_GATEWAY_ID ||
        raw[2] != GATEWAY_FRAME_HOST_CONTROL ||
        payload_length != AGGREGATOR_HOST_CONTROL_PAYLOAD_SIZE ||
        payload[0] != AGGREGATOR_HOST_CONTROL_VERSION ||
        get_le16(&payload[2]) != 0U) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    command = payload[1];
    session_id = get_le32(&payload[4]);
    if (command == AGGREGATOR_HOST_CONTROL_START) {
        return begin_host_session(session_id)
                   ? FRAME_CHECK_OK
                   : FRAME_CHECK_FORMAT_ERROR;
    }
    if (command == AGGREGATOR_HOST_CONTROL_STOP) {
        end_host_session(session_id);
        return FRAME_CHECK_OK;
    }
    return FRAME_CHECK_FORMAT_ERROR;
}

static void record_link_validation(link_context_t *link,
                                   frame_check_result_t result)
{
    portENTER_CRITICAL(&s_stats_mux);
    if (result == FRAME_CHECK_COBS_ERROR) {
        link->stats.cobs_errors++;
    } else if (result == FRAME_CHECK_CRC_ERROR) {
        link->stats.crc_errors++;
    } else {
        link->stats.frame_errors++;
    }
    portEXIT_CRITICAL(&s_stats_mux);
}

static void record_usb_validation(frame_check_result_t result)
{
    portENTER_CRITICAL(&s_stats_mux);
    if (result == FRAME_CHECK_COBS_ERROR) {
        s_stats.usb_cobs_errors++;
    } else if (result == FRAME_CHECK_CRC_ERROR) {
        s_stats.usb_crc_errors++;
    } else {
        s_stats.usb_frame_errors++;
    }
    portEXIT_CRITICAL(&s_stats_mux);
}

static void update_link_rx_queue_high_watermark(link_context_t *link)
{
    UBaseType_t waiting = uxQueueMessagesWaiting(link->rx_frame_queue);

    portENTER_CRITICAL(&s_stats_mux);
    if (waiting > link->stats.rx_queue_high_watermark) {
        link->stats.rx_queue_high_watermark = saturate_u8(waiting);
    }
    portEXIT_CRITICAL(&s_stats_mux);
}

static void update_usb_queue_high_watermark(void)
{
    UBaseType_t waiting = uxQueueMessagesWaiting(s_usb_tx_queue);

    portENTER_CRITICAL(&s_stats_mux);
    if (waiting > s_stats.usb_queue_high_watermark) {
        s_stats.usb_queue_high_watermark =
            waiting > UINT16_MAX ? UINT16_MAX : (uint16_t)waiting;
    }
    portEXIT_CRITICAL(&s_stats_mux);
}

static bool usb_write_all(const uint8_t *data, size_t length)
{
    size_t offset = 0U;

    while (offset < length) {
        int written = usb_serial_jtag_write_bytes(
            data + offset,
            length - offset,
            pdMS_TO_TICKS(TRANSPORT_WRITE_WAIT_MS));
        if (written <= 0) {
            return false;
        }
        offset += (size_t)written;
        portENTER_CRITICAL(&s_stats_mux);
        s_stats.usb_tx_bytes += (uint32_t)written;
        portEXIT_CRITICAL(&s_stats_mux);
    }
    return true;
}

static bool uart_write_all(link_context_t *link,
                           const uint8_t *data,
                           size_t length)
{
    size_t offset = 0U;

    while (offset < length) {
        int written = uart_write_bytes(link->uart_port,
                                       data + offset,
                                       length - offset);
        if (written <= 0) {
            return false;
        }
        offset += (size_t)written;
        portENTER_CRITICAL(&s_stats_mux);
        link->stats.uart_tx_bytes += (uint32_t)written;
        portEXIT_CRITICAL(&s_stats_mux);
    }
    return uart_wait_tx_done(
               link->uart_port,
               pdMS_TO_TICKS(TRANSPORT_WRITE_WAIT_MS)) == ESP_OK;
}

static void usb_tx_task(void *param)
{
    wire_frame_t frame;
    uint32_t current_session_id;

    (void)param;
    for (;;) {
        if (xQueueReceive(s_usb_tx_queue,
                          &frame,
                          portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (frame.source_link != HOST_CONTROL_SOURCE &&
            (!host_stream_snapshot(&current_session_id) ||
             frame.host_session_id != current_session_id)) {
            continue;
        }
        if (!usb_write_all(frame.bytes, frame.length)) {
            portENTER_CRITICAL(&s_stats_mux);
            s_stats.usb_write_failures++;
            s_stats.usb_output_drops++;
            if (frame.source_link < AGGREGATOR_LINK_COUNT) {
                s_links[frame.source_link].stats.usb_output_drops++;
            }
            portEXIT_CRITICAL(&s_stats_mux);
        }
    }
}

static void uart_tx_task(void *param)
{
    link_context_t *link = (link_context_t *)param;
    wire_frame_t frame;

    for (;;) {
        if (xQueueReceive(link->tx_command_queue,
                          &frame,
                          portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!uart_write_all(link, frame.bytes, frame.length)) {
            portENTER_CRITICAL(&s_stats_mux);
            link->stats.uart_output_drops++;
            portEXIT_CRITICAL(&s_stats_mux);
        }
    }
}

static void merge_task(void *param)
{
    wire_frame_t frame;
    uint8_t next_link = 0U;
    uint8_t offset;
    uint8_t burst;
    uint8_t link_index;
    bool moved;
    bool usb_full;

    (void)param;
    for (;;) {
        moved = false;
        usb_full = false;
        for (offset = 0U; offset < AGGREGATOR_LINK_COUNT; offset++) {
            link_index =
                (uint8_t)((next_link + offset) % AGGREGATOR_LINK_COUNT);
            for (burst = 0U;
                 burst < CONFIG_SMARTBLOCK_AGGREGATOR_MERGE_BURST;
                 burst++) {
                /*
                 * Preserve the per-link queues as additional back-pressure
                 * storage. Do not drain and discard them merely because the
                 * common USB queue is temporarily full.
                 */
                if (uxQueueSpacesAvailable(s_usb_tx_queue) == 0U) {
                    usb_full = true;
                    break;
                }
                if (xQueueReceive(s_links[link_index].rx_frame_queue,
                                  &frame,
                                  0) != pdTRUE) {
                    break;
                }
                moved = true;
                if (xQueueSend(s_usb_tx_queue, &frame, 0) == pdTRUE) {
                    update_usb_queue_high_watermark();
                } else {
                    portENTER_CRITICAL(&s_stats_mux);
                    s_stats.usb_output_drops++;
                    s_links[link_index].stats.usb_output_drops++;
                    portEXIT_CRITICAL(&s_stats_mux);
                }
            }
            if (usb_full) {
                break;
            }
        }
        next_link =
            (uint8_t)((next_link + 1U) % AGGREGATOR_LINK_COUNT);

        if (usb_full) {
            vTaskDelay(1U);
        } else if (moved) {
            (void)ulTaskNotifyTake(pdTRUE, 0);
            taskYIELD();
        } else {
            (void)ulTaskNotifyTake(
                pdTRUE,
                pdMS_TO_TICKS(MERGE_IDLE_WAIT_MS));
        }
    }
}

static void uart_rx_task(void *param)
{
    link_context_t *link = (link_context_t *)param;
    uint8_t read_buffer[256];
    uint8_t encoded[GATEWAY_MAX_ENCODED_SIZE];
    size_t encoded_length = 0U;
    bool overflow = false;
    TickType_t valid_frame_tick;
    uint32_t host_session_id;
    int read_length;
    int index;

    for (;;) {
        read_length = uart_read_bytes(link->uart_port,
                                      read_buffer,
                                      sizeof(read_buffer),
                                      pdMS_TO_TICKS(UART_READ_WAIT_MS));
        if (read_length <= 0) {
            continue;
        }
        portENTER_CRITICAL(&s_stats_mux);
        link->stats.uart_rx_bytes += (uint32_t)read_length;
        portEXIT_CRITICAL(&s_stats_mux);

        for (index = 0; index < read_length; index++) {
            if (read_buffer[index] == 0U) {
                if (!overflow && encoded_length > 0U) {
                    frame_check_result_t result =
                        validate_gateway_output(link,
                                                encoded,
                                                encoded_length);
                    if (result == FRAME_CHECK_OK) {
                        valid_frame_tick = xTaskGetTickCount();
                        portENTER_CRITICAL(&s_stats_mux);
                        link->stats.valid_frames++;
                        link->stats.last_valid_frame_tick =
                            valid_frame_tick;
                        link->stats.saw_valid_frame = true;
                        portEXIT_CRITICAL(&s_stats_mux);

                        if (host_stream_snapshot(&host_session_id)) {
                            if (queue_wire_frame(
                                    link->rx_frame_queue,
                                    encoded,
                                    encoded_length,
                                    link->gateway_id,
                                    host_session_id)) {
                                update_link_rx_queue_high_watermark(link);
                                if (s_merge_task_handle != NULL) {
                                    xTaskNotifyGive(s_merge_task_handle);
                                }
                            } else {
                                portENTER_CRITICAL(&s_stats_mux);
                                link->stats.rx_queue_drops++;
                                portEXIT_CRITICAL(&s_stats_mux);
                            }
                        }
                    } else {
                        record_link_validation(link, result);
                    }
                } else if (overflow) {
                    record_link_validation(
                        link,
                        FRAME_CHECK_FORMAT_ERROR);
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

static void usb_rx_task(void *param)
{
    uint8_t read_buffer[128];
    uint8_t encoded[GATEWAY_MAX_ENCODED_SIZE];
    uint8_t routed_encoded[GATEWAY_MAX_ENCODED_SIZE];
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    size_t encoded_length = 0U;
    size_t routed_encoded_length;
    size_t raw_length;
    uint8_t route_index;
    uint32_t host_session_id;
    bool overflow = false;
    int read_length;
    int index;

    (void)param;
    for (;;) {
        read_length = usb_serial_jtag_read_bytes(
            read_buffer,
            sizeof(read_buffer),
            pdMS_TO_TICKS(UART_READ_WAIT_MS));
        if (read_length <= 0) {
            continue;
        }
        portENTER_CRITICAL(&s_stats_mux);
        s_stats.usb_rx_bytes += (uint32_t)read_length;
        portEXIT_CRITICAL(&s_stats_mux);

        for (index = 0; index < read_length; index++) {
            if (read_buffer[index] == 0U) {
                if (!overflow && encoded_length > 0U) {
                    frame_check_result_t result = validate_common_frame(
                        encoded,
                        encoded_length,
                        raw,
                        &raw_length);
                    bool host_control =
                        result == FRAME_CHECK_OK &&
                        raw[2] == GATEWAY_FRAME_HOST_CONTROL;

                    if (host_control) {
                        result = handle_host_control(raw, raw_length);
                    } else if (result == FRAME_CHECK_OK &&
                               host_stream_snapshot(&host_session_id)) {
                        result = prepare_pc_command(
                            encoded,
                            encoded_length,
                            &route_index,
                            routed_encoded,
                            &routed_encoded_length);
                    } else if (result == FRAME_CHECK_OK) {
                        result = FRAME_CHECK_FORMAT_ERROR;
                    }
                    if (result == FRAME_CHECK_OK) {
                        portENTER_CRITICAL(&s_stats_mux);
                        s_stats.usb_valid_commands++;
                        if (!host_control) {
                            s_links[route_index].stats.valid_commands++;
                        }
                        portEXIT_CRITICAL(&s_stats_mux);

                        if (!host_control && !queue_wire_frame(
                                s_links[route_index].tx_command_queue,
                                routed_encoded,
                                routed_encoded_length,
                                route_index,
                                host_session_id)) {
                            portENTER_CRITICAL(&s_stats_mux);
                            s_links[route_index].stats.uart_output_drops++;
                            portEXIT_CRITICAL(&s_stats_mux);
                        }
                    } else {
                        record_usb_validation(result);
                    }
                } else if (overflow) {
                    record_usb_validation(FRAME_CHECK_FORMAT_ERROR);
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

static void build_aggregator_diagnostic(
    uint8_t payload[AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE])
{
    aggregator_stats_t aggregator_stats;
    link_stats_t link_stats[AGGREGATOR_LINK_COUNT];
    TickType_t now = xTaskGetTickCount();
    uint32_t usb_input_errors;
    uint32_t age_ticks;
    uint32_t age_100ms;
    uint32_t frame_drops;
    uint8_t link_index;
    uint8_t flags;
    uint32_t host_session_id;
    bool host_active;
    size_t base;

    portENTER_CRITICAL(&s_stats_mux);
    aggregator_stats = s_stats;
    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        link_stats[link_index] = s_links[link_index].stats;
    }
    portEXIT_CRITICAL(&s_stats_mux);
    host_active = host_stream_snapshot(&host_session_id);
    (void)host_session_id;

    memset(payload, 0, AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE);
    payload[0] = AGGREGATOR_DIAGNOSTIC_VERSION;
    payload[1] = AGGREGATOR_LINK_COUNT;
    payload[2] = (host_active ? 0x01U : 0U) |
                 (aggregator_stats.usb_output_drops != 0U ? 0x02U : 0U);
    payload[3] =
        saturate_u8(aggregator_stats.usb_queue_high_watermark);
    put_le32(&payload[4], ticks_to_ms(now));
    put_le32(&payload[8], aggregator_stats.usb_rx_bytes);
    put_le32(&payload[12], aggregator_stats.usb_tx_bytes);
    put_le32(&payload[16], aggregator_stats.usb_valid_commands);
    usb_input_errors = aggregator_stats.usb_cobs_errors +
                       aggregator_stats.usb_crc_errors +
                       aggregator_stats.usb_frame_errors;
    put_le32(&payload[20], usb_input_errors);
    put_le32(&payload[24], aggregator_stats.usb_output_drops);

    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        base = AGGREGATOR_DIAGNOSTIC_HEADER_SIZE +
               (size_t)link_index *
                   AGGREGATOR_DIAGNOSTIC_LINK_SIZE;
        flags = 0U;
        if (link_stats[link_index].saw_valid_frame) {
            age_ticks =
                (uint32_t)(now -
                           link_stats[link_index].last_valid_frame_tick);
            age_100ms = ticks_to_ms(age_ticks) / 100U;
            if (ticks_to_ms(age_ticks) <= LINK_ACTIVE_TIMEOUT_MS) {
                flags |= 0x01U;
            }
        } else {
            age_100ms = UINT16_MAX;
        }
        if (uxQueueMessagesWaiting(
                s_links[link_index].rx_frame_queue) != 0U) {
            flags |= 0x02U;
        }
        if (uxQueueMessagesWaiting(
                s_links[link_index].tx_command_queue) != 0U) {
            flags |= 0x04U;
        }

        payload[base] = s_links[link_index].gateway_id;
        payload[base + 1U] = flags;
        payload[base + 2U] =
            (uint8_t)s_links[link_index].uart_port;
        payload[base + 3U] =
            link_stats[link_index].rx_queue_high_watermark;
        put_le32(&payload[base + 4U],
                 link_stats[link_index].uart_rx_bytes);
        put_le32(&payload[base + 8U],
                 link_stats[link_index].valid_frames);
        put_le16(&payload[base + 12U],
                 saturate_u16(link_stats[link_index].cobs_errors));
        put_le16(&payload[base + 14U],
                 saturate_u16(link_stats[link_index].crc_errors));
        put_le16(&payload[base + 16U],
                 saturate_u16(link_stats[link_index].frame_errors));
        frame_drops =
            link_stats[link_index].rx_queue_drops +
            link_stats[link_index].usb_output_drops;
        put_le16(&payload[base + 18U],
                 saturate_u16(frame_drops));
        put_le16(&payload[base + 20U],
                 saturate_u16(
                     link_stats[link_index].uart_output_drops));
        put_le16(&payload[base + 22U],
                 age_100ms >= UINT16_MAX
                     ? UINT16_MAX
                     : (uint16_t)age_100ms);
    }
}

static void health_task(void *param)
{
    uint8_t payload[AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE];
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    uint8_t encoded[GATEWAY_MAX_ENCODED_SIZE];
    size_t raw_length;
    size_t encoded_length;
    uint32_t host_session_id;
    TickType_t wake_tick = xTaskGetTickCount();

    (void)param;
    for (;;) {
        vTaskDelayUntil(
            &wake_tick,
            pdMS_TO_TICKS(AGGREGATOR_HEALTH_PERIOD_MS));

        if (!host_stream_snapshot(&host_session_id)) {
            continue;
        }

        build_aggregator_diagnostic(payload);
        raw[0] = GATEWAY_FRAME_VERSION;
        raw[1] = AGGREGATOR_DIAGNOSTIC_GATEWAY_ID;
        raw[2] = GATEWAY_FRAME_AGGREGATOR_DIAGNOSTIC;
        raw[3] = 0U;
        put_le32(&raw[4], s_health_sequence++);
        put_le16(&raw[8], AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE);
        memcpy(&raw[GATEWAY_HEADER_SIZE],
               payload,
               AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE);
        raw_length = GATEWAY_HEADER_SIZE +
                     AGGREGATOR_DIAGNOSTIC_PAYLOAD_SIZE +
                     GATEWAY_CRC_SIZE;
        put_le32(&raw[raw_length - GATEWAY_CRC_SIZE],
                 crc32(raw, raw_length - GATEWAY_CRC_SIZE));
        encoded_length = cobs_encode(raw, raw_length, encoded);

        if (queue_wire_frame(s_usb_tx_queue,
                             encoded,
                             encoded_length,
                             NO_SOURCE_LINK,
                             host_session_id)) {
            update_usb_queue_high_watermark();
        } else {
            portENTER_CRITICAL(&s_stats_mux);
            s_stats.usb_output_drops++;
            portEXIT_CRITICAL(&s_stats_mux);
        }
    }
}

static bool link_configuration_is_valid(void)
{
    uint8_t first;
    uint8_t second;

    for (first = 0U; first < AGGREGATOR_LINK_COUNT; first++) {
        if (s_links[first].tx_gpio == s_links[first].rx_gpio ||
            s_links[first].tx_gpio == 19 ||
            s_links[first].tx_gpio == 20 ||
            s_links[first].rx_gpio == 19 ||
            s_links[first].rx_gpio == 20) {
            return false;
        }
        for (second = (uint8_t)(first + 1U);
             second < AGGREGATOR_LINK_COUNT;
             second++) {
            if (s_links[first].uart_port ==
                    s_links[second].uart_port ||
                s_links[first].tx_gpio ==
                    s_links[second].tx_gpio ||
                s_links[first].tx_gpio ==
                    s_links[second].rx_gpio ||
                s_links[first].rx_gpio ==
                    s_links[second].tx_gpio ||
                s_links[first].rx_gpio ==
                    s_links[second].rx_gpio) {
                return false;
            }
        }
    }
    return true;
}

static void create_queues(void)
{
    uint8_t link_index;

    s_usb_tx_queue =
        xQueueCreate(CONFIG_SMARTBLOCK_AGGREGATOR_USB_QUEUE_DEPTH,
                     sizeof(wire_frame_t));
    ESP_ERROR_CHECK(
        s_usb_tx_queue != NULL ? ESP_OK : ESP_ERR_NO_MEM);

    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        s_links[link_index].rx_frame_queue =
            xQueueCreate(
                CONFIG_SMARTBLOCK_AGGREGATOR_LINK_QUEUE_DEPTH,
                sizeof(wire_frame_t));
        s_links[link_index].tx_command_queue =
            xQueueCreate(UART_COMMAND_QUEUE_DEPTH,
                         sizeof(wire_frame_t));
        ESP_ERROR_CHECK(
            s_links[link_index].rx_frame_queue != NULL &&
                    s_links[link_index].tx_command_queue != NULL
                ? ESP_OK
                : ESP_ERR_NO_MEM);
    }
}

static void transport_init(void)
{
    usb_serial_jtag_driver_config_t usb_config = {
        .rx_buffer_size = USB_RX_DRIVER_BUFFER_SIZE,
        .tx_buffer_size = USB_TX_DRIVER_BUFFER_SIZE,
    };
    const uart_config_t uart_config = {
        .baud_rate = CONFIG_SMARTBLOCK_AGGREGATOR_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    const uint8_t delimiter = 0U;
    uint8_t link_index;

    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        ESP_ERROR_CHECK(
            uart_driver_install(
                s_links[link_index].uart_port,
                CONFIG_SMARTBLOCK_AGGREGATOR_UART_RX_BUFFER_SIZE,
                CONFIG_SMARTBLOCK_AGGREGATOR_UART_TX_BUFFER_SIZE,
                0,
                NULL,
                0));
        ESP_ERROR_CHECK(
            uart_param_config(s_links[link_index].uart_port,
                              &uart_config));
        ESP_ERROR_CHECK(
            uart_set_pin(s_links[link_index].uart_port,
                         s_links[link_index].tx_gpio,
                         s_links[link_index].rx_gpio,
                         UART_PIN_NO_CHANGE,
                         UART_PIN_NO_CHANGE));
    }

    (void)usb_write_all(&delimiter, 1U);
    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        (void)uart_write_all(&s_links[link_index],
                             &delimiter,
                             1U);
    }
}

static void create_tasks(void)
{
    uint8_t link_index;

    ESP_ERROR_CHECK(
        xTaskCreate(usb_tx_task,
                    "aggregator_usb_tx",
                    4096,
                    NULL,
                    9,
                    NULL) == pdPASS
            ? ESP_OK
            : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(
        xTaskCreate(merge_task,
                    "aggregator_merge",
                    4096,
                    NULL,
                    9,
                    &s_merge_task_handle) == pdPASS
            ? ESP_OK
            : ESP_ERR_NO_MEM);

    for (link_index = 0U;
         link_index < AGGREGATOR_LINK_COUNT;
         link_index++) {
        ESP_ERROR_CHECK(
            xTaskCreate(uart_tx_task,
                        s_uart_tx_task_names[link_index],
                        4096,
                        &s_links[link_index],
                        8,
                        NULL) == pdPASS
                ? ESP_OK
                : ESP_ERR_NO_MEM);
        ESP_ERROR_CHECK(
            xTaskCreate(uart_rx_task,
                        s_uart_rx_task_names[link_index],
                        4096,
                        &s_links[link_index],
                        10,
                        NULL) == pdPASS
                ? ESP_OK
                : ESP_ERR_NO_MEM);
    }

    ESP_ERROR_CHECK(
        xTaskCreate(usb_rx_task,
                    "aggregator_usb_rx",
                    4096,
                    NULL,
                    9,
                    NULL) == pdPASS
            ? ESP_OK
            : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(
        xTaskCreate(health_task,
                    "aggregator_health",
                    4096,
                    NULL,
                    5,
                    NULL) == pdPASS
            ? ESP_OK
            : ESP_ERR_NO_MEM);
}

void app_main(void)
{
    ESP_ERROR_CHECK(
        link_configuration_is_valid()
            ? ESP_OK
            : ESP_ERR_INVALID_ARG);
    create_queues();
    transport_init();
    create_tasks();
}
