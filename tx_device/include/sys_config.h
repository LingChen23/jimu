#ifndef SYS_CONFIG_H
#define SYS_CONFIG_H

#include "tx_device_config.h"

#define IS_SRVC_CHANGED_CHARACT_PRESENT 0                                           /**<   Include   the   service_changed   characteristic.   If   not   enabled,   the   server's   database   cannot   be   changed   for   the   lifetime   of   the   device.   */

#if (NRF_SD_BLE_API_VERSION == 3)
#define NRF_BLE_MAX_MTU_SIZE            185U                                        /**< MTU size used in the softdevice enabling and to reply to a BLE_GATTS_EVT_EXCHANGE_MTU_REQUEST event. */
#endif

#define APP_FEATURE_NOT_SUPPORTED       BLE_GATT_STATUS_ATTERR_APP_BEGIN + 2        /**< Reply when unsupported features are requested. */

#define CENTRAL_LINK_COUNT              0                                           /**< Number of central links used by the application. When changing this number remember to adjust the RAM settings*/
#define PERIPHERAL_LINK_COUNT           1                                           /**< Number of peripheral links used by the application. When changing this number remember to adjust the RAM settings*/

#define DEVICE_NAME                     "SmartBlock"                                    /**< Name of device. Will be included in the advertising data. */
#define NUS_SERVICE_UUID_TYPE           BLE_UUID_TYPE_VENDOR_BEGIN                  /**< UUID type for the Nordic UART Service (vendor specific). */

#define APP_ADV_INTERVAL                100                                          /**< The advertising interval (in units of 0.625 ms. This value corresponds to 40 ms). */
#define APP_ADV_TIMEOUT_IN_SECONDS      0                                            /**< The advertising timeout (in units of seconds). */

#define APP_TIMER_PRESCALER             0                                           /**< Value of the RTC1 PRESCALER register. */
#define APP_TIMER_OP_QUEUE_SIZE         4                                           /**< Size of timer operation queues. */

#define BLE_TX_POWER_DBM                4                                           /**< Explicit nRF52832 BLE transmit power (+4 dBm maximum). */

/*
 * Keep independent gateways on different steady periods so their connection
 * events do not remain phase-locked when the gateway antennas are colocated.
 * BLE connection intervals use 1.25 ms units.
 */
#if (TX_DEVICE_ID <= 4U)
#define SMARTBLOCK_CONN_INTERVAL_UNITS  24U                                         /**< Gateway A: 30 ms. */
#elif (TX_DEVICE_ID <= 8U)
#define SMARTBLOCK_CONN_INTERVAL_UNITS  26U                                         /**< Gateway B: 32.5 ms. */
#else
#define SMARTBLOCK_CONN_INTERVAL_UNITS  28U                                         /**< Gateway C: 35 ms. */
#endif

#define MIN_CONN_INTERVAL               SMARTBLOCK_CONN_INTERVAL_UNITS
#define MAX_CONN_INTERVAL               SMARTBLOCK_CONN_INTERVAL_UNITS
#define SLAVE_LATENCY                   0                                           /**< Slave latency. */
#define CONN_SUP_TIMEOUT                MSEC_TO_UNITS(4000, UNIT_10_MS)             /**< Connection supervisory timeout (4 seconds), Supervision Timeout uses 10 ms units. */
#define FIRST_CONN_PARAMS_UPDATE_DELAY  APP_TIMER_TICKS(5000, APP_TIMER_PRESCALER)  /**< Time from initiating event (connect or start of notification) to first time sd_ble_gap_conn_param_update is called (5 seconds). */
#define NEXT_CONN_PARAMS_UPDATE_DELAY   APP_TIMER_TICKS(30000, APP_TIMER_PRESCALER) /**< Time between each call to sd_ble_gap_conn_param_update after the first call (30 seconds). */
#define MAX_CONN_PARAMS_UPDATE_COUNT    3                                           /**< Number of attempts before giving up the connection parameter negotiation. */

/*
 * Every SmartBlock requests its gateway group's preferred interval about five
 * seconds after connecting. This also restores the intended interval after a
 * reconnect without collapsing all three gateway groups back to 30 ms.
 */
#define TX_CONN_PARAMS_UPDATE_ENABLED   1U

#define DEAD_BEEF                       0xDEADBEEF                                  /**< Value used as error code on stack dump, can be used to identify stack location on stack unwind. */

#define UART_TX_BUF_SIZE                256                                         /**< UART TX buffer size. */
#define UART_RX_BUF_SIZE                256                                         /**< UART RX buffer size. */

#define NUS_BUF_SIZE                    (4096)

#define DFU_TIMEOUT_INTERVAL            APP_TIMER_TICKS(60000, APP_TIMER_PRESCALER)

#define SYS_CONFIG_PAGE                 (NRF_FICR->CODESIZE - 31)//use last-31 page in flash
#define SYS_CONFIG_FLASH_ADDR           (NRF_FICR->CODEPAGESIZE * SYS_CONFIG_PAGE)
#define CONFIG_VALID_MAGIC              0XABBA

#define FW_VER                          "V0.1.0"

#endif
