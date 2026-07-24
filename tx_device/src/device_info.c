/**
 * @file device_info.c
 * @brief
 * @author
 * @version V1.0
 * @date 2017-06-08
 */
#include "device_info.h"
#include <stdbool.h>
#include <stdio.h>
#include "nrf.h"
#include "bsp.h"
#include "app_error.h"
#include "nordic_common.h"
#include "app_uart.h"
#include "nrf_delay.h"

#define NRF_LOG_MODULE_NAME "DEVICE_INFO"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"

#define DEVICE_INFO_PAGE        (NRF_FICR->CODESIZE -1)//use last page in flash
#define DEVICE_INFO_FLASH_ADDR  (NRF_FICR->CODEPAGESIZE * DEVICE_INFO_PAGE)
/** @brief Function for erasing a page in flash.
 *
 * @param page_address Address of the first word in the page to be erased.
 */
static void flash_page_erase(uint32_t * page_address)
{
    // Turn on flash erase enable and wait until the NVMC is ready:
    NRF_NVMC->CONFIG = (NVMC_CONFIG_WEN_Een << NVMC_CONFIG_WEN_Pos);

    while (NRF_NVMC->READY == NVMC_READY_READY_Busy)
    {
        // Do nothing.
    }

    // Erase page:
    NRF_NVMC->ERASEPAGE = (uint32_t)page_address;

    while (NRF_NVMC->READY == NVMC_READY_READY_Busy)
    {
        // Do nothing.
    }

    // Turn off flash erase enable and wait until the NVMC is ready:
    NRF_NVMC->CONFIG = (NVMC_CONFIG_WEN_Ren << NVMC_CONFIG_WEN_Pos);

    while (NRF_NVMC->READY == NVMC_READY_READY_Busy)
    {
        // Do nothing.
    }
}


/** @brief Function for filling a page in flash with a value.
 *
 * @param[in] address Address of the first word in the page to be filled.
 * @param[in] value Value to be written to flash.
 */
static void flash_word_write(uint32_t * address, uint32_t value)
{
    // Turn on flash write enable and wait until the NVMC is ready:
    NRF_NVMC->CONFIG = (NVMC_CONFIG_WEN_Wen << NVMC_CONFIG_WEN_Pos);

    while (NRF_NVMC->READY == NVMC_READY_READY_Busy)
    {
        // Do nothing.
    }

    *address = value;

    while (NRF_NVMC->READY == NVMC_READY_READY_Busy)
    {
        // Do nothing.
    }

    // Turn off flash write enable and wait until the NVMC is ready:
    NRF_NVMC->CONFIG = (NVMC_CONFIG_WEN_Ren << NVMC_CONFIG_WEN_Pos);

    while (NRF_NVMC->READY == NVMC_READY_READY_Busy)
    {
        // Do nothing.
    }
}


/**
 * @brief device_info_write
 *
 * @param device_info
 */
static void device_info_write(uint32_t sn)
{
        uint32_t err_code;
        while(sd_flash_page_erase(DEVICE_INFO_PAGE)==NRF_SUCCESS);
        nrf_delay_ms(200);
        while(sd_flash_write((uint32_t *)DEVICE_INFO_FLASH_ADDR, &sn, 1)==NRF_SUCCESS);
        nrf_delay_ms(200);
}

void device_info_read(uint32_t *sn)
{
        *sn = *(uint32_t *)DEVICE_INFO_FLASH_ADDR;
}

/**
 * @brief app_uart_put_string
 *
 * @param s
 */
static void app_uart_put_string(uint8_t *s){
    uint16_t index = 0;
    while(s[index++]!='\0'){
        APP_ERROR_CHECK(app_uart_put(s[index-1]));
    }
}

/**@brief   Function for handling app_uart events.
 *
 * @details This function will receive a single character from the app_uart module and append it to
 *          a string. The string will be be sent over BLE when the last character received was a
 *          'new line' i.e '\r\n' (hex 0x0D) or if the string has reached a length of
 *          @ref NUS_MAX_DATA_LENGTH.
 */
void uart_event_handle(app_uart_evt_t * p_event)
{
    static uint8_t data_array[100];
    static uint8_t index = 0;
    static uint32_t sn;
    uint8_t to_string[50];
        
    switch (p_event->evt_type)
    {
        /**@snippet [Handling data from UART] */
        case APP_UART_DATA_READY:

            UNUSED_VARIABLE(app_uart_get(&data_array[index]));

            index++;

		if (data_array[index - 1] == '\n')
                {
                        if(data_array[index - 2] == '\r'){
                                data_array[index - 1] = '\0';
                        }else{
                                data_array[index] = '\0';
                        }

                        NRF_LOG_ERROR("uart rx: %s\r\n",(uint32_t)data_array);
                        sn = atoi((char *)data_array);

                        if(!sn){
                                app_uart_put_string("please input serial number! range from 0 ~ 65535\r\n");
                        }else{
                                app_uart_put_string("writing serial number...\r\n");
                                device_info_write(sn);
                                app_uart_put_string("writing ok!r\n");
                                sprintf((char *)to_string,"read flash serial number: %d\r\n",*(uint32_t *)DEVICE_INFO_FLASH_ADDR);
                                app_uart_put_string(to_string);
                                app_uart_put_string("attention! system restart valid\r\n");
                        }

                        index = 0;
		}

            break;
        case APP_UART_COMMUNICATION_ERROR:
//            APP_ERROR_HANDLER(p_event->data.error_communication);
            break;

        case APP_UART_FIFO_ERROR:
//            APP_ERROR_HANDLER(p_event->data.error_code);
            break;

        default:
            break;
    }
}

#define DEVICE_INFO_UART_TX_BUF_SIZE        256                             /**< UART TX buffer size. */
#define DEVICE_INFO_UART_RX_BUF_SIZE        256                             /**< UART RX buffer size. */

/**@brief Function for initializing the UART.
 */
static void uart_init(void)
{
    uint32_t err_code;

    const app_uart_comm_params_t comm_params =
      {
        .rx_pin_no    = DEVICE_INFO_RX_PIN_NUMBER,
        .tx_pin_no    = DEVICE_INFO_TX_PIN_NUMBER,
        .rts_pin_no   = RTS_PIN_NUMBER,
        .cts_pin_no   = CTS_PIN_NUMBER,
        .flow_control = APP_UART_FLOW_CONTROL_DISABLED,
        .use_parity   = false,
        .baud_rate    = UART_BAUDRATE_BAUDRATE_Baud115200
      };

    APP_UART_FIFO_INIT(&comm_params,
                        DEVICE_INFO_UART_RX_BUF_SIZE,//100 bytes buf
                        DEVICE_INFO_UART_TX_BUF_SIZE,//100 bytes buf
                        uart_event_handle,
                        APP_IRQ_PRIORITY_LOWEST,
                        err_code);

    APP_ERROR_CHECK(err_code);
}


/**
 * @brief device_info_init
 */
void device_info_init(void){
    uart_init();
}
