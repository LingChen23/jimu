#include "gateway_transport.h"

#include "sdkconfig.h"

#if CONFIG_SMARTBLOCK_GATEWAY_TRANSPORT_UART

#include "driver/uart.h"

static uart_port_t transport_uart_port(void)
{
    return (uart_port_t)CONFIG_SMARTBLOCK_GATEWAY_UART_PORT;
}

esp_err_t gateway_transport_init(void)
{
    const uart_config_t uart_config = {
        .baud_rate = CONFIG_SMARTBLOCK_GATEWAY_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    uart_port_t port = transport_uart_port();
    esp_err_t err;

    err = uart_driver_install(
        port,
        CONFIG_SMARTBLOCK_GATEWAY_UART_RX_BUFFER_SIZE,
        CONFIG_SMARTBLOCK_GATEWAY_UART_TX_BUFFER_SIZE,
        0,
        NULL,
        0);
    if (err != ESP_OK) {
        return err;
    }
    err = uart_param_config(port, &uart_config);
    if (err != ESP_OK) {
        return err;
    }
    return uart_set_pin(port,
                        CONFIG_SMARTBLOCK_GATEWAY_UART_TX_GPIO,
                        CONFIG_SMARTBLOCK_GATEWAY_UART_RX_GPIO,
                        UART_PIN_NO_CHANGE,
                        UART_PIN_NO_CHANGE);
}

int gateway_transport_read(uint8_t *data,
                           size_t length,
                           TickType_t wait_ticks)
{
    return uart_read_bytes(transport_uart_port(),
                           data,
                           (uint32_t)length,
                           wait_ticks);
}

int gateway_transport_write(const uint8_t *data,
                            size_t length,
                            TickType_t wait_ticks)
{
    int written = uart_write_bytes(transport_uart_port(),
                                   data,
                                   length);

    if (written <= 0) {
        return written;
    }
    if (uart_wait_tx_done(transport_uart_port(), wait_ticks) != ESP_OK) {
        return -1;
    }
    return written;
}

const char *gateway_transport_name(void)
{
    return "UART";
}

#else

#include "driver/usb_serial_jtag.h"

esp_err_t gateway_transport_init(void)
{
    usb_serial_jtag_driver_config_t config = {
        .rx_buffer_size = 256U,
        .tx_buffer_size = 32768U,
    };

    return usb_serial_jtag_driver_install(&config);
}

int gateway_transport_read(uint8_t *data,
                           size_t length,
                           TickType_t wait_ticks)
{
    return usb_serial_jtag_read_bytes(data, length, wait_ticks);
}

int gateway_transport_write(const uint8_t *data,
                            size_t length,
                            TickType_t wait_ticks)
{
    return usb_serial_jtag_write_bytes(data, length, wait_ticks);
}

const char *gateway_transport_name(void)
{
    return "USB Serial/JTAG";
}

#endif
