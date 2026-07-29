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

#define GATEWAY_FRAME_VERSION             1U
#define GATEWAY_FRAME_MOTION              0x10U
#define GATEWAY_FRAME_CONTROL_REQUEST     0x20U
#define GATEWAY_FRAME_CONTROL_RESPONSE    0x21U
#define GATEWAY_FRAME_LINK_DIAGNOSTIC     0x30U
#define GATEWAY_ID_SINGLE_LINK            0U

#define GATEWAY_HEADER_SIZE               10U
#define GATEWAY_CRC_SIZE                  4U
#define GATEWAY_MAX_PAYLOAD_SIZE          100U
#define GATEWAY_MAX_RAW_FRAME_SIZE        \
    (GATEWAY_HEADER_SIZE + GATEWAY_MAX_PAYLOAD_SIZE + GATEWAY_CRC_SIZE)
#define GATEWAY_MAX_ENCODED_SIZE          (GATEWAY_MAX_RAW_FRAME_SIZE + 1U)
#define GATEWAY_MAX_WIRE_SIZE             (GATEWAY_MAX_ENCODED_SIZE + 1U)

#define SINGLE_LINK_DEVICE_ID_MIN          1U
#define SINGLE_LINK_DEVICE_ID_MAX          4U

#define USB_RX_DRIVER_BUFFER_SIZE          512U
#define USB_TX_DRIVER_BUFFER_SIZE          32768U
#define UART_RX_DRIVER_BUFFER_SIZE         16384U
#define UART_TX_DRIVER_BUFFER_SIZE         32768U
#define USB_TX_QUEUE_DEPTH                 256U
#define UART_TX_QUEUE_DEPTH                16U
#define TRANSPORT_WRITE_WAIT_MS            1000U

typedef struct {
    uint16_t length;
    uint8_t bytes[GATEWAY_MAX_WIRE_SIZE];
} wire_frame_t;

typedef struct {
    uint32_t uart_rx_bytes;
    uint32_t uart_valid_frames;
    uint32_t uart_cobs_errors;
    uint32_t uart_crc_errors;
    uint32_t uart_frame_errors;
    uint32_t usb_output_drops;
    uint32_t usb_rx_bytes;
    uint32_t usb_valid_commands;
    uint32_t usb_cobs_errors;
    uint32_t usb_crc_errors;
    uint32_t usb_frame_errors;
    uint32_t uart_output_drops;
} aggregator_stats_t;

typedef enum {
    FRAME_CHECK_OK = 0,
    FRAME_CHECK_COBS_ERROR,
    FRAME_CHECK_CRC_ERROR,
    FRAME_CHECK_FORMAT_ERROR,
} frame_check_result_t;

static QueueHandle_t s_usb_tx_queue;
static QueueHandle_t s_uart_tx_queue;
static aggregator_stats_t s_stats;
static portMUX_TYPE s_stats_mux = portMUX_INITIALIZER_UNLOCKED;

static uart_port_t gateway_uart_port(void)
{
    return (uart_port_t)CONFIG_SMARTBLOCK_AGGREGATOR_UART_PORT;
}

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

static frame_check_result_t validate_gateway_output(
    const uint8_t *encoded,
    size_t encoded_length)
{
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
    size_t raw_length;
    frame_check_result_t result;

    result = validate_common_frame(encoded,
                                   encoded_length,
                                   raw,
                                   &raw_length);
    if (result != FRAME_CHECK_OK) {
        return result;
    }
    if (raw[1] != GATEWAY_ID_SINGLE_LINK ||
        (raw[2] != GATEWAY_FRAME_MOTION &&
         raw[2] != GATEWAY_FRAME_CONTROL_RESPONSE &&
         raw[2] != GATEWAY_FRAME_LINK_DIAGNOSTIC)) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    return FRAME_CHECK_OK;
}

static frame_check_result_t validate_pc_command(
    const uint8_t *encoded,
    size_t encoded_length)
{
    uint8_t raw[GATEWAY_MAX_RAW_FRAME_SIZE];
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
    payload_length = get_le16(&raw[8]);
    if (raw[2] != GATEWAY_FRAME_CONTROL_REQUEST ||
        payload_length < 1U) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    device_id = raw[GATEWAY_HEADER_SIZE];
    if (device_id < SINGLE_LINK_DEVICE_ID_MIN ||
        device_id > SINGLE_LINK_DEVICE_ID_MAX) {
        return FRAME_CHECK_FORMAT_ERROR;
    }
    return FRAME_CHECK_OK;
}

static bool queue_wire_frame(QueueHandle_t queue,
                             const uint8_t *encoded,
                             size_t encoded_length)
{
    wire_frame_t frame;

    if (encoded_length == 0U ||
        encoded_length + 1U > sizeof(frame.bytes)) {
        return false;
    }
    frame.length = (uint16_t)(encoded_length + 1U);
    memcpy(frame.bytes, encoded, encoded_length);
    frame.bytes[encoded_length] = 0U;
    return xQueueSend(queue, &frame, 0) == pdTRUE;
}

static void record_uart_validation(frame_check_result_t result)
{
    portENTER_CRITICAL(&s_stats_mux);
    if (result == FRAME_CHECK_COBS_ERROR) {
        s_stats.uart_cobs_errors++;
    } else if (result == FRAME_CHECK_CRC_ERROR) {
        s_stats.uart_crc_errors++;
    } else {
        s_stats.uart_frame_errors++;
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
    }
    return true;
}

static bool uart_write_all(const uint8_t *data, size_t length)
{
    size_t offset = 0U;
    uart_port_t port = gateway_uart_port();

    while (offset < length) {
        int written = uart_write_bytes(port,
                                       data + offset,
                                       length - offset);
        if (written <= 0) {
            return false;
        }
        offset += (size_t)written;
    }
    return uart_wait_tx_done(
               port,
               pdMS_TO_TICKS(TRANSPORT_WRITE_WAIT_MS)) == ESP_OK;
}

static void usb_tx_task(void *param)
{
    wire_frame_t frame;

    (void)param;
    for (;;) {
        if (xQueueReceive(s_usb_tx_queue,
                          &frame,
                          portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!usb_write_all(frame.bytes, frame.length)) {
            portENTER_CRITICAL(&s_stats_mux);
            s_stats.usb_output_drops++;
            portEXIT_CRITICAL(&s_stats_mux);
        }
    }
}

static void uart_tx_task(void *param)
{
    wire_frame_t frame;

    (void)param;
    for (;;) {
        if (xQueueReceive(s_uart_tx_queue,
                          &frame,
                          portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!uart_write_all(frame.bytes, frame.length)) {
            portENTER_CRITICAL(&s_stats_mux);
            s_stats.uart_output_drops++;
            portEXIT_CRITICAL(&s_stats_mux);
        }
    }
}

static void uart_rx_task(void *param)
{
    uint8_t read_buffer[256];
    uint8_t encoded[GATEWAY_MAX_ENCODED_SIZE];
    size_t encoded_length = 0U;
    bool overflow = false;
    int read_length;
    int index;

    (void)param;
    for (;;) {
        read_length = uart_read_bytes(gateway_uart_port(),
                                      read_buffer,
                                      sizeof(read_buffer),
                                      pdMS_TO_TICKS(100));
        if (read_length <= 0) {
            continue;
        }
        portENTER_CRITICAL(&s_stats_mux);
        s_stats.uart_rx_bytes += (uint32_t)read_length;
        portEXIT_CRITICAL(&s_stats_mux);

        for (index = 0; index < read_length; index++) {
            if (read_buffer[index] == 0U) {
                if (!overflow && encoded_length > 0U) {
                    frame_check_result_t result =
                        validate_gateway_output(encoded, encoded_length);
                    if (result == FRAME_CHECK_OK) {
                        if (queue_wire_frame(s_usb_tx_queue,
                                             encoded,
                                             encoded_length)) {
                            portENTER_CRITICAL(&s_stats_mux);
                            s_stats.uart_valid_frames++;
                            portEXIT_CRITICAL(&s_stats_mux);
                        } else {
                            portENTER_CRITICAL(&s_stats_mux);
                            s_stats.usb_output_drops++;
                            portEXIT_CRITICAL(&s_stats_mux);
                        }
                    } else {
                        record_uart_validation(result);
                    }
                } else if (overflow) {
                    record_uart_validation(FRAME_CHECK_FORMAT_ERROR);
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
    size_t encoded_length = 0U;
    bool overflow = false;
    int read_length;
    int index;

    (void)param;
    for (;;) {
        read_length = usb_serial_jtag_read_bytes(
            read_buffer,
            sizeof(read_buffer),
            pdMS_TO_TICKS(100));
        if (read_length <= 0) {
            continue;
        }
        portENTER_CRITICAL(&s_stats_mux);
        s_stats.usb_rx_bytes += (uint32_t)read_length;
        portEXIT_CRITICAL(&s_stats_mux);

        for (index = 0; index < read_length; index++) {
            if (read_buffer[index] == 0U) {
                if (!overflow && encoded_length > 0U) {
                    frame_check_result_t result =
                        validate_pc_command(encoded, encoded_length);
                    if (result == FRAME_CHECK_OK) {
                        if (queue_wire_frame(s_uart_tx_queue,
                                             encoded,
                                             encoded_length)) {
                            portENTER_CRITICAL(&s_stats_mux);
                            s_stats.usb_valid_commands++;
                            portEXIT_CRITICAL(&s_stats_mux);
                        } else {
                            portENTER_CRITICAL(&s_stats_mux);
                            s_stats.uart_output_drops++;
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
    uart_port_t port = gateway_uart_port();
    const uint8_t delimiter = 0U;

    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
    ESP_ERROR_CHECK(uart_driver_install(port,
                                        UART_RX_DRIVER_BUFFER_SIZE,
                                        UART_TX_DRIVER_BUFFER_SIZE,
                                        0,
                                        NULL,
                                        0));
    ESP_ERROR_CHECK(uart_param_config(port, &uart_config));
    ESP_ERROR_CHECK(uart_set_pin(
        port,
        CONFIG_SMARTBLOCK_AGGREGATOR_UART_TX_GPIO,
        CONFIG_SMARTBLOCK_AGGREGATOR_UART_RX_GPIO,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE));

    (void)usb_write_all(&delimiter, 1U);
    (void)uart_write_all(&delimiter, 1U);
}

void app_main(void)
{
    transport_init();

    s_usb_tx_queue =
        xQueueCreate(USB_TX_QUEUE_DEPTH, sizeof(wire_frame_t));
    s_uart_tx_queue =
        xQueueCreate(UART_TX_QUEUE_DEPTH, sizeof(wire_frame_t));
    ESP_ERROR_CHECK(
        s_usb_tx_queue != NULL && s_uart_tx_queue != NULL
            ? ESP_OK
            : ESP_ERR_NO_MEM);

    if (xTaskCreate(usb_tx_task,
                    "aggregator_usb_tx",
                    4096,
                    NULL,
                    8,
                    NULL) != pdPASS ||
        xTaskCreate(uart_tx_task,
                    "aggregator_uart_tx",
                    4096,
                    NULL,
                    8,
                    NULL) != pdPASS ||
        xTaskCreate(uart_rx_task,
                    "aggregator_uart_rx",
                    4096,
                    NULL,
                    9,
                    NULL) != pdPASS ||
        xTaskCreate(usb_rx_task,
                    "aggregator_usb_rx",
                    4096,
                    NULL,
                    9,
                    NULL) != pdPASS) {
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
}
