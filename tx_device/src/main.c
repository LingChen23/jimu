#include "app_fifo.h"
#include "app_timer.h"
#include "app_uart.h"
#include "app_util_platform.h"
#include "ble_advertising.h"
#include "ble_conn_params.h"
#include "ble_hci.h"
#include "ble_nus.h"
#include "boards.h"
#include "motion_packet.h"
#include "motion_service.h"
#include "realtime_traj.h"
#include "nordic_common.h"
#include "nrf.h"
#include "nrf52.h"
#include "nrf_delay.h"
#include "nrf_dfu_types.h"
#include "nrf_drv_gpiote.h"
#include "nrf_wdt.h"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"
#include "nrf_power.h"
#include "nrf_pwr_mgmt.h"
#include "nrf_saadc.h"
#include "nrf_soc.h"
#include "sensor_icm_20689.h"
#include "softdevice_handler.h"
#include "sys_config.h"
#include "traj_prot.h"
#include "trajectory_protocol.h"
#include "tx_device_config.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "hal_nfc_t2t.h"
#include "nfc_ndef_msg.h"
#include "nfc_t2t_lib.h"
#include "nfc_text_rec.h"

#undef RF_LOG_MODULE_NAME
#define NRF_LOG_MODULE_NAME "MAIN"

app_fifo_t m_nus_fifo;
uint8_t    nus_fifo[NUS_BUF_SIZE];

APP_TIMER_DEF(m_dfu_timer_id);

volatile uint8_t sync_traj_data_flag;
volatile uint8_t m_ble_tx_complete;

static ble_nus_t  m_nus;
static motion_service_t m_motion_service;
static uint16_t   m_conn_handle = BLE_CONN_HANDLE_INVALID;
static ble_uuid_t m_adv_uuids[] = { { BLE_UUID_NUS_SERVICE,
                                      NUS_SERVICE_UUID_TYPE } };

#define MOTION_QUEUE_CAPACITY         32U
#define TRAJ_POWER_OFF_DISCHARGE_MS   10U
#define TRAJ_POWER_ON_SETTLE_MS       20U

#if (WDT_CONFIG_RELOAD_VALUE < 4000) || (WDT_CONFIG_RELOAD_VALUE > 8000)
#error "WDT_CONFIG_RELOAD_VALUE must stay within the 4-8 second design range"
#endif

typedef struct
{
    uint8_t data[MOTION_PACKET_SIZE];
} motion_queue_entry_t;

static motion_queue_entry_t m_motion_queue[MOTION_QUEUE_CAPACITY];
static uint8_t m_motion_queue_head;
static uint8_t m_motion_queue_tail;
static uint8_t m_motion_queue_count;
static uint32_t m_motion_queue_drops;
static volatile uint8_t m_sample_pending;
static volatile uint32_t m_sample_timestamp_ticks;
static volatile uint32_t m_sample_overruns;
static realtime_traj_t m_realtime_traj;
static volatile uint8_t m_control_request_pending;
static uint8_t m_control_request[
  MOTION_CONTROL_HEADER_SIZE + MOTION_CONTROL_MAX_PAYLOAD];
static uint16_t m_control_request_length;
static uint8_t m_status_response[MOTION_RESPONSE_MAX_SIZE];
static uint16_t m_status_response_length;
static volatile uint8_t m_status_response_pending;
static uint8_t m_stream_enabled = 1U;
static uint8_t m_recalibrate_requested;
static uint8_t m_reboot_requested;
static uint8_t m_reboot_response_accepted;
static uint32_t m_reboot_response_tick;
static uint32_t m_reset_reason;
static uint32_t m_current_session_id;
static uint32_t m_latest_sample_sequence;
static uint32_t m_uptime_sample_count;
static uint32_t m_sensor_error_count;
static uint32_t m_ble_resource_wait_count;
static uint8_t m_sensor_ok = 1U;

static void realtime_sample_interrupt_init(void);
static void sample_clock_init(void);
static uint8_t take_sample_pending(uint32_t* timestamp_ticks);
static void motion_control_write_handler(const uint8_t* data,
                                         uint16_t length);

static void motion_queue_clear(void)
{
    m_motion_queue_head = 0;
    m_motion_queue_tail = 0;
    m_motion_queue_count = 0;
}

static void motion_queue_push(uint8_t packet[MOTION_PACKET_SIZE])
{
    if (m_motion_queue_count == MOTION_QUEUE_CAPACITY) {
        m_motion_queue_head =
          (uint8_t)((m_motion_queue_head + 1U) % MOTION_QUEUE_CAPACITY);
        m_motion_queue_count--;
        m_motion_queue_drops++;
        packet[MOTION_PACKET_FLAGS_OFFSET] |= MOTION_PACKET_FLAG_QUEUE_DROP;
    }

    memcpy(m_motion_queue[m_motion_queue_tail].data,
           packet,
           MOTION_PACKET_SIZE);
    m_motion_queue_tail =
      (uint8_t)((m_motion_queue_tail + 1U) % MOTION_QUEUE_CAPACITY);
    m_motion_queue_count++;
}

static void motion_queue_pop(void)
{
    if (m_motion_queue_count == 0U) {
        return;
    }

    m_motion_queue_head =
      (uint8_t)((m_motion_queue_head + 1U) % MOTION_QUEUE_CAPACITY);
    m_motion_queue_count--;
}

static void motion_send_pump(void)
{
    uint32_t err_code;

    if (m_conn_handle == BLE_CONN_HANDLE_INVALID) {
        motion_queue_clear();
        m_status_response_pending = 0U;
        return;
    }

    if (m_status_response_pending) {
        err_code = motion_service_status_notify(&m_motion_service,
                                                m_status_response,
                                                m_status_response_length);
        if (err_code == NRF_SUCCESS) {
            m_status_response_pending = 0U;
            if (m_reboot_requested) {
                m_reboot_response_accepted = 1U;
                m_reboot_requested = 0U;
                m_reboot_response_tick = app_timer_cnt_get();
            }
        } else if ((err_code == NRF_ERROR_RESOURCES) ||
                   (err_code == BLE_ERROR_NO_TX_PACKETS)) {
            m_ble_resource_wait_count++;
            return;
        } else if (err_code == NRF_ERROR_INVALID_STATE) {
            m_status_response_pending = 0U;
        } else {
            APP_ERROR_CHECK(err_code);
        }
    }

    if (!m_motion_service.notification_enabled) {
        motion_queue_clear();
        return;
    }

    while (m_motion_queue_count > 0U) {
        err_code = motion_service_notify(
          &m_motion_service,
          m_motion_queue[m_motion_queue_head].data);

        if (err_code == NRF_SUCCESS) {
            motion_queue_pop();
            continue;
        }

        if ((err_code == NRF_ERROR_RESOURCES) ||
            (err_code == BLE_ERROR_NO_TX_PACKETS)) {
            m_ble_resource_wait_count++;
            return;
        }

        if (err_code == NRF_ERROR_INVALID_STATE) {
            motion_queue_clear();
            return;
        }

        APP_ERROR_CHECK(err_code);
    }
}

// clang-format off
#define DFU_FLAG_OFFSET 0X00
#define DFU_FLAG_LEN    0X01
#define NO_OFFSET       0X01
#define NO_LEN          0X01
#define TYPE_OFFSET     0X02
#define TYPE_LEN        0X01
#define FW_VER_OFFSET   0X03
#define FW_VER_LEN      0X03

__ALIGN(4)
typedef struct
{
    uint16_t config_valid_flag;
    uint8_t  nfc_flag;
    uint8_t  dfu_flag;
    uint8_t  no;
    uint8_t  type;
    uint8_t  fw_ver[3];
    uint8_t  reserve[3];
}sys_config_t;

sys_config_t sys_config;

/*
*   读取偏移地址数据：
*   FF B0 00 R_OFFSET 00 R_LEN
*   
*   写偏移地址数据:
*   FF D6 00 W_OFFSET W_LEN(4 Bytes) W_DATA MAX_ACK_LEN(default: 0x90)
*   
*   ex: write fw version V1.0.0
*   Send Command
*   << FF D6 00 03 04 01 00 00 00 
*   >> 90 00 
*
*   ex: read fw version V1.0.0
*   Send Command
*   << FF B0 00 03 04 
*   >> 01 00 00 00 90 00
*
*   ex: enter dfu mode
*   Send Command
*   << FF D6 00 00 04 01 00 00 00 
*   >> 90 00 
*/

// clang-format on

void sync_sys_config_to_flash(void)
{
    uint32_t err_code;
    while (sd_flash_page_erase(SYS_CONFIG_PAGE) == NRF_SUCCESS)
        ;
    nrf_delay_ms(200);
    while (sd_flash_write((uint32_t*)SYS_CONFIG_FLASH_ADDR,
                          (uint32_t*)&sys_config,
                          sizeof(sys_config_t) / 4) == NRF_SUCCESS)
        ;
    nrf_delay_ms(200);
}

void sync_sys_config_from_flash(void)
{
    memcpy((uint8_t*)&sys_config,
           (uint32_t*)SYS_CONFIG_FLASH_ADDR,
           sizeof(sys_config_t));
    if (sys_config.config_valid_flag != CONFIG_VALID_MAGIC) {
        NRF_LOG_WARNING("config not valid, init...\r\n");
        memset((uint8_t*)&sys_config, 0, sizeof(sys_config_t));
        sys_config.config_valid_flag = CONFIG_VALID_MAGIC;
        // force update ro data
        sscanf(FW_VER,
               "V%d.%d.%d\r\n",
               sys_config.fw_ver,
               sys_config.fw_ver + 1,
               sys_config.fw_ver + 2);
        sync_sys_config_to_flash();
    } else {
        // force update ro data
        sscanf(FW_VER,
               "V%d.%d.%d\r\n",
               sys_config.fw_ver,
               sys_config.fw_ver + 1,
               sys_config.fw_ver + 2);
        NRF_LOG_WARNING("config is valid\r\n");
        NRF_LOG_WARNING("nfc flag: %d\r\n", sys_config.nfc_flag);
        NRF_LOG_WARNING("dfu flag: %d\r\n", sys_config.dfu_flag);
        NRF_LOG_WARNING("no: %d\r\n", sys_config.no);
        NRF_LOG_WARNING("type: %d\r\n", sys_config.type);
        NRF_LOG_WARNING("fw_ver: V%d.%d.%d\r\n",
                        sys_config.fw_ver[0],
                        sys_config.fw_ver[1],
                        sys_config.fw_ver[2]);
    }
}

static void write_config(uint8_t offset, uint8_t* data)
{
    switch (offset) {
        case DFU_FLAG_OFFSET:
            NRF_LOG_INFO("write dfu flag: %d\r\n", data[0]);
            sys_config.dfu_flag = data[0];
            break;
        case NO_OFFSET:
            NRF_LOG_INFO("write no: %d\r\n", data[0]);
            sys_config.no = data[0];
            break;
        case TYPE_OFFSET:
            NRF_LOG_INFO("write type: %d\r\n", data[0]);
            sys_config.type = data[0];
            break;
        case FW_VER_OFFSET:
            NRF_LOG_INFO(
              "write fw ver ignore: V%d.%d.%d\r\n", data[0], data[1], data[2]);
            break;
        default:
            break;
    }
    sync_sys_config_to_flash();
}

static void read_config(uint8_t offset, uint8_t* data)
{
    //sync_sys_config_from_flash();

    switch (offset) {
        case DFU_FLAG_OFFSET:
            NRF_LOG_INFO("read dfu flag: %d\r\n", data[0]);
            data[0] = sys_config.dfu_flag;
            break;
        case NO_OFFSET:
            NRF_LOG_INFO("read no: %d\r\n", data[0]);
            data[0] = sys_config.no;
            break;
        case TYPE_OFFSET:
            NRF_LOG_INFO("read type: %d\r\n", data[0]);
            data[0] = sys_config.type;
            break;
        case FW_VER_OFFSET:
            NRF_LOG_INFO(
              "read fw ver: V%d.%d.%d\r\n", data[0], data[1], data[2]);
            data[0] = sys_config.fw_ver[0];
            data[1] = sys_config.fw_ver[1];
            data[2] = sys_config.fw_ver[2];
            break;
        default:
            break;
    }
}

static void nfc_callback(void*           p_context,
                         hal_nfc_event_t event,
                         const uint8_t*  p_data,
                         size_t          data_length)
{
    uint8_t offset = p_data[1];
    uint8_t read_data[4];
    uint8_t config_data[4];

    (void)p_context;

    switch (event) {
        case HAL_NFC_EVENT_FIELD_ON:
            if (!sys_config.nfc_flag) {
                NRF_LOG_WARNING("enter nfc config mode, reboot system\r\n");
                // set nfc flag
                sys_config.nfc_flag = 1;
                sync_sys_config_to_flash();
                nrf_delay_ms(500);
                NVIC_SystemReset();
            } else {
                NRF_LOG_WARNING("already enter nfc config mode\r\n");
                nrf_gpio_pin_set(LED_CONFIG_PIN);
            }
            break;
        case HAL_NFC_EVENT_FIELD_OFF:
            NRF_LOG_WARNING("exit nfc config mode, reboot system\r\n");
            // set nfc flag
            sys_config.nfc_flag = 0;
            sync_sys_config_to_flash();
            nrf_delay_ms(500);
            NVIC_SystemReset();
            break;
        case HAL_NFC_EVENT_DATA_RECEIVED:
            // Max Block number
            if (offset < 252) {
                NRF_LOG_INFO("nrf received, block: %d\r\n", offset);
                read_config(offset, read_data);
                hal_nfc_send(read_data, 4);
            } else {
                // NAck
                hal_send_ack_nack(0x0);
            }
            break;
        case HAL_NFC_EVENT_DATA_WRITE:
            if ((offset < 252) && (data_length < 255)) {
                // Ack for write command
                hal_send_ack_nack(0xA);
                write_config(offset, (uint8_t*)p_data + 2);
                // judge whether enter dfu mode
                if (sys_config.dfu_flag) {
                    NRF_LOG_WARNING("enter dfu mode, reboot system\r\n");
                    nrf_delay_ms(500);
                    NVIC_SystemReset();
                }
            } else {
                // NAck for write command : should be 0, 1, 4 or 5
                hal_send_ack_nack(0x0);
            }
            break;
        default:
            break;
    }
}

void assert_nrf_callback(uint16_t line_num, const uint8_t* p_file_name)
{
    app_error_handler(DEAD_BEEF, line_num, p_file_name);
}

static void gap_params_init(void)
{
    uint32_t                err_code;
    ble_gap_conn_params_t   gap_conn_params;
    ble_gap_conn_sec_mode_t sec_mode;

    BLE_GAP_CONN_SEC_MODE_SET_OPEN(&sec_mode);

    err_code = sd_ble_gap_device_name_set(
      &sec_mode, (const uint8_t*)DEVICE_NAME, strlen(DEVICE_NAME));
    APP_ERROR_CHECK(err_code);

    memset(&gap_conn_params, 0, sizeof(gap_conn_params));

    gap_conn_params.min_conn_interval = MIN_CONN_INTERVAL;
    gap_conn_params.max_conn_interval = MAX_CONN_INTERVAL;
    gap_conn_params.slave_latency = SLAVE_LATENCY;
    gap_conn_params.conn_sup_timeout = CONN_SUP_TIMEOUT;

    err_code = sd_ble_gap_ppcp_set(&gap_conn_params);
    APP_ERROR_CHECK(err_code);

    err_code = sd_ble_gap_tx_power_set(BLE_TX_POWER_DBM);
    APP_ERROR_CHECK(err_code);
}

static void nus_data_handler(ble_nus_t* p_evt)
{
    // if (p_evt->rx_handles ==) {
    //    // uint32_t err_code;
    //    NRF_LOG_DEBUG("Received data from BLE NUS. Writing data on
    //                  UART
    //                    .");
    //                    NRF_LOG_HEXDUMP_DEBUG(p_evt->params.rx_data.p_data,
    //                  p_evt->params.rx_data.length);
    //    if (p_evt->params.rx_data.p_data[0] == '1') {
    //        NRF_LOG_INFO("enter config mode.");
    //    }
    //    if (p_evt->params.rx_data.p_data[0] == '2') {
    //        NRF_LOG_INFO("enter normal mode.");
    //    }
    //    for (uint32_t i = 0; i < p_evt->params.rx_data.length; i++) {
    //            NRF_LOG_RAW_INFO("-------------->the data from mobile phone is
    //            %c\r\n", p_evt->params.rx_data.p_data[i]);
    //    }
    //}
}

static void services_init(void)
{
    uint32_t       err_code;
    ble_nus_init_t nus_init;

    memset(&nus_init, 0, sizeof(nus_init));

    nus_init.data_handler = nus_data_handler;

    err_code = ble_nus_init(&m_nus, &nus_init);
    APP_ERROR_CHECK(err_code);

    err_code = motion_service_init(&m_motion_service,
                                   motion_control_write_handler);
    APP_ERROR_CHECK(err_code);
}

#if TX_CONN_PARAMS_UPDATE_ENABLED
static void on_conn_params_evt(ble_conn_params_evt_t* p_evt)
{
    uint32_t err_code;

    if (p_evt->evt_type == BLE_CONN_PARAMS_EVT_FAILED) {
        err_code = sd_ble_gap_disconnect(m_conn_handle,
                                         BLE_HCI_CONN_INTERVAL_UNACCEPTABLE);
        APP_ERROR_CHECK(err_code);
    }
}

static void conn_params_error_handler(uint32_t nrf_error)
{
    APP_ERROR_HANDLER(nrf_error);
}

static void conn_params_init(void)
{
    uint32_t               err_code;
    ble_conn_params_init_t cp_init;

    memset(&cp_init, 0, sizeof(cp_init));

    cp_init.p_conn_params = NULL;
    cp_init.first_conn_params_update_delay = FIRST_CONN_PARAMS_UPDATE_DELAY;
    cp_init.next_conn_params_update_delay = NEXT_CONN_PARAMS_UPDATE_DELAY;
    cp_init.max_conn_params_update_count = MAX_CONN_PARAMS_UPDATE_COUNT;
    cp_init.start_on_notify_cccd_handle = BLE_GATT_HANDLE_INVALID;
    cp_init.disconnect_on_fail = false;
    cp_init.evt_handler = on_conn_params_evt;
    cp_init.error_handler = conn_params_error_handler;

    err_code = ble_conn_params_init(&cp_init);
    APP_ERROR_CHECK(err_code);
}
#else
static void conn_params_init(void)
{
    /* Optional diagnostic build: retain the interval chosen by the ESP32. */
}
#endif

static void on_adv_evt(ble_adv_evt_t ble_adv_evt)
{
    uint32_t err_code;
    (void)err_code;

    switch (ble_adv_evt) {
        case BLE_ADV_EVT_FAST:
            break;
        case BLE_ADV_EVT_IDLE:
            break;
        default:
            break;
    }
}

static void on_ble_evt(ble_evt_t* p_ble_evt)
{
    uint32_t err_code;

    switch (p_ble_evt->header.evt_id) {
        case BLE_GAP_EVT_CONNECTED:
            NRF_LOG_DEBUG("connected!\r\n");
            m_conn_handle = p_ble_evt->evt.gap_evt.conn_handle;
            sync_traj_data_flag = 1;
            break; // BLE_GAP_EVT_CONNECTED

        case BLE_GAP_EVT_DISCONNECTED:
            NRF_LOG_DEBUG("disconnected!\r\n");
            m_conn_handle = BLE_CONN_HANDLE_INVALID;
            sync_traj_data_flag = 0;
            m_nus.is_notification_enabled = false;
            motion_queue_clear();
            m_control_request_pending = 0U;
            m_status_response_pending = 0U;
            m_reboot_requested = 0U;
            m_reboot_response_accepted = 0U;
            break; // BLE_GAP_EVT_DISCONNECTED

        case BLE_EVT_TX_COMPLETE:
            m_ble_tx_complete = 1;
            break;

        case BLE_GAP_EVT_SEC_PARAMS_REQUEST:
            // Pairing not supported
            err_code = sd_ble_gap_sec_params_reply(
              m_conn_handle, BLE_GAP_SEC_STATUS_PAIRING_NOT_SUPP, NULL, NULL);
            APP_ERROR_CHECK(err_code);
            break; // BLE_GAP_EVT_SEC_PARAMS_REQUEST

        case BLE_GATTS_EVT_SYS_ATTR_MISSING:
            // No system attributes have been stored.
            err_code = sd_ble_gatts_sys_attr_set(m_conn_handle, NULL, 0, 0);
            APP_ERROR_CHECK(err_code);
            break; // BLE_GATTS_EVT_SYS_ATTR_MISSING

        case BLE_GATTC_EVT_TIMEOUT:
            // Disconnect on GATT Client timeout event.
            err_code =
              sd_ble_gap_disconnect(p_ble_evt->evt.gattc_evt.conn_handle,
                                    BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
            break; // BLE_GATTC_EVT_TIMEOUT

        case BLE_GATTS_EVT_TIMEOUT:
            // Disconnect on GATT Server timeout event.
            err_code =
              sd_ble_gap_disconnect(p_ble_evt->evt.gatts_evt.conn_handle,
                                    BLE_HCI_REMOTE_USER_TERMINATED_CONNECTION);
            APP_ERROR_CHECK(err_code);
            break; // BLE_GATTS_EVT_TIMEOUT

        case BLE_EVT_USER_MEM_REQUEST:
            err_code =
              sd_ble_user_mem_reply(p_ble_evt->evt.gattc_evt.conn_handle, NULL);
            APP_ERROR_CHECK(err_code);
            break; // BLE_EVT_USER_MEM_REQUEST

        case BLE_GATTS_EVT_RW_AUTHORIZE_REQUEST: {
            ble_gatts_evt_rw_authorize_request_t  req;
            ble_gatts_rw_authorize_reply_params_t auth_reply;

            req = p_ble_evt->evt.gatts_evt.params.authorize_request;

            if (req.type != BLE_GATTS_AUTHORIZE_TYPE_INVALID) {
                if ((req.request.write.op == BLE_GATTS_OP_PREP_WRITE_REQ) ||
                    (req.request.write.op == BLE_GATTS_OP_EXEC_WRITE_REQ_NOW) ||
                    (req.request.write.op ==
                     BLE_GATTS_OP_EXEC_WRITE_REQ_CANCEL)) {
                    if (req.type == BLE_GATTS_AUTHORIZE_TYPE_WRITE) {
                        auth_reply.type = BLE_GATTS_AUTHORIZE_TYPE_WRITE;
                    } else {
                        auth_reply.type = BLE_GATTS_AUTHORIZE_TYPE_READ;
                    }
                    auth_reply.params.write.gatt_status =
                      APP_FEATURE_NOT_SUPPORTED;
                    err_code = sd_ble_gatts_rw_authorize_reply(
                      p_ble_evt->evt.gatts_evt.conn_handle, &auth_reply);
                    APP_ERROR_CHECK(err_code);
                }
            }
        } break; // BLE_GATTS_EVT_RW_AUTHORIZE_REQUEST

#if (NRF_SD_BLE_API_VERSION == 3)
        case BLE_GATTS_EVT_EXCHANGE_MTU_REQUEST:
            err_code = sd_ble_gatts_exchange_mtu_reply(
              p_ble_evt->evt.gatts_evt.conn_handle, NRF_BLE_MAX_MTU_SIZE);
            APP_ERROR_CHECK(err_code);
            break; // BLE_GATTS_EVT_EXCHANGE_MTU_REQUEST
#endif

        default:
            // No implementation needed.
            break;
    }
}

static void ble_evt_dispatch(ble_evt_t* p_ble_evt)
{
#if TX_CONN_PARAMS_UPDATE_ENABLED
    ble_conn_params_on_ble_evt(p_ble_evt);
#endif
    ble_nus_on_ble_evt(&m_nus, p_ble_evt);
    motion_service_on_ble_evt(&m_motion_service, p_ble_evt);
    on_ble_evt(p_ble_evt);
    ble_advertising_on_ble_evt(p_ble_evt);
}

static void sys_evt_dispatch(uint32_t sys_evt)
{
    fs_sys_event_handler(sys_evt);
    ble_advertising_on_sys_evt(sys_evt);
}

static void ble_stack_init(void)
{
    uint32_t             err_code;
    ble_opt_t            bw_opt;
    ble_conn_bw_counts_t ble_conn_bw_counts;
    ble_enable_params_t  ble_enable_params;
    nrf_clock_lf_cfg_t   clock_lf_cfg = NRF_CLOCK_LFCLKSRC;

    SOFTDEVICE_HANDLER_INIT(&clock_lf_cfg, NULL);

    err_code = softdevice_enable_get_default_config(
      CENTRAL_LINK_COUNT, PERIPHERAL_LINK_COUNT, &ble_enable_params);
    APP_ERROR_CHECK(err_code);

    memset(&ble_conn_bw_counts, 0, sizeof(ble_conn_bw_counts_t));
    ble_conn_bw_counts.tx_counts.high_count = PERIPHERAL_LINK_COUNT;
    ble_conn_bw_counts.rx_counts.high_count = PERIPHERAL_LINK_COUNT;
    ble_enable_params.common_enable_params.p_conn_bw_counts =
      &ble_conn_bw_counts;
    /*Check the ram settings against the used number of links*/
    CHECK_RAM_START_ADDR(CENTRAL_LINK_COUNT, PERIPHERAL_LINK_COUNT);
    /*Enable BLE stack.*/
#if (NRF_SD_BLE_API_VERSION == 3)
    ble_enable_params.gatt_enable_params.att_mtu = NRF_BLE_MAX_MTU_SIZE;
#endif
    err_code = softdevice_enable(&ble_enable_params);
    APP_ERROR_CHECK(err_code);

    /*Register with the SoftDevice handler module for BLE events.*/
    err_code = softdevice_ble_evt_handler_set(ble_evt_dispatch);
    APP_ERROR_CHECK(err_code);

    /*config slave peripheral bandwidth low:1 mid:3 high:6 packets per con
     * interval*/
    bw_opt.common_opt.conn_bw.role = BLE_GAP_ROLE_PERIPH;
    bw_opt.common_opt.conn_bw.conn_bw.conn_bw_rx = BLE_CONN_BW_HIGH;
    bw_opt.common_opt.conn_bw.conn_bw.conn_bw_tx = BLE_CONN_BW_HIGH;
    err_code = sd_ble_opt_set(BLE_COMMON_OPT_CONN_BW, &bw_opt);
    APP_ERROR_CHECK(err_code);

    err_code = softdevice_sys_evt_handler_set(sys_evt_dispatch);
    APP_ERROR_CHECK(err_code);

    /*use dc/dc mode to reduce power */
    // err_code = sd_power_dcdc_mode_set(NRF_POWER_DCDC_ENABLE);
    // APP_ERROR_CHECK(err_code);
}

/**@brief Function for initializing the Advertising functionality.
 */
static void advertising_init(void)
{
    uint32_t                 err_code;
    ble_advdata_t            advdata;
    ble_advdata_t            scanrsp;
    ble_advdata_manuf_data_t manuf_data;
    ble_adv_modes_config_t   options;

    memset(&manuf_data, 0, sizeof(manuf_data));
    manuf_data.company_identifier = 0xabcd;
    manuf_data.data.size = sizeof(sys_config);
    manuf_data.data.p_data = (uint8_t*)&sys_config;

    // Build advertising data struct to pass into @ref ble_advertising_init.
    memset(&advdata, 0, sizeof(advdata));
    advdata.name_type = BLE_ADVDATA_FULL_NAME;
    advdata.include_appearance = false;
    advdata.flags = BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE;
    advdata.p_manuf_specific_data = &manuf_data;

    memset(&scanrsp, 0, sizeof(scanrsp));
    scanrsp.uuids_complete.uuid_cnt =
      sizeof(m_adv_uuids) / sizeof(m_adv_uuids[0]);
    scanrsp.uuids_complete.p_uuids = m_adv_uuids;

    memset(&options, 0, sizeof(options));
    options.ble_adv_fast_enabled = true;
    options.ble_adv_fast_interval = APP_ADV_INTERVAL;
    options.ble_adv_fast_timeout = APP_ADV_TIMEOUT_IN_SECONDS;

    err_code =
      ble_advertising_init(&advdata, &scanrsp, &options, on_adv_evt, NULL);
    APP_ERROR_CHECK(err_code);
}

static void dfu_handler(void* p_context)
{
    NRF_LOG_WARNING("wait dfu timeout, reboot\r\n");
    NVIC_SystemReset();
}

static void timers_init(void)
{
    uint32_t err_code;

    /*Initialize timer module.*/
    APP_TIMER_INIT(APP_TIMER_PRESCALER, APP_TIMER_OP_QUEUE_SIZE, false);

    /*Create timers.*/
    err_code = app_timer_create(
      &m_dfu_timer_id, APP_TIMER_MODE_SINGLE_SHOT, dfu_handler);
    APP_ERROR_CHECK(err_code);

}

static void power_manage(void)
{
    /*pan87_workaround*/
#if (__FPU_USED == 1U)
// Set bit 7 and bits 4..0 in the mask to one (0x ...00 1001 1111)
#define FPU_EXCEPTION_MASK 0x0000009F
    /* Clear exceptions and PendingIRQ from the FPU unit */
    __set_FPSCR(__get_FPSCR() & ~(FPU_EXCEPTION_MASK));
    (void)__get_FPSCR();
    NVIC_ClearPendingIRQ(FPU_IRQn);
#endif
    uint32_t err_code = sd_app_evt_wait();
    APP_ERROR_CHECK(err_code);
}

#if 0
/* Legacy capture-then-send implementation retained for reference. */
static void traj_int_deinit(void);

int16_t          read_buf[6];
uint32_t         read_buf_size = sizeof(read_buf);
volatile uint8_t traj_stop_sampe_flag;

// traj algo para
float*          data_buffer;
int16_t         data_index = -1;
data_paras_traj p;
traj_state      data_state = TRAJ_PRAPARE;
float           data_output_realtime[3] = { 0 };
float*          data_output_cali;
int32_t         data_output_cali_len = 0;
float           amp_vector = 0;
float           amp_sum = 0;

void in_pin_handler(nrf_drv_gpiote_pin_t pin, nrf_gpiote_polarity_t action)
{
    if (traj_stop_sampe_flag) {
        return;
    }
    ret_code_t err_code;
    icm_20689_int_read_data(sizeof(read_buf), read_buf);
#if 0
    NRF_LOG_DEBUG(
      "ax: % 6d, ay: % 6d, az: % 6d, gx: % 6d, gy: % 6d, gz: % 6d\r\n",
      read_buf[0],
      read_buf[1],
      read_buf[2],
      read_buf[3],
      read_buf[4],
      read_buf[5]);
#endif
    data_index++;
    if (data_index >= p.num_point_judge) {
        data_index -= p.num_point_judge;
        data_state = TRAJ_STABLE;
    }
    for (int j = 0; j < 3; j++) {
        *(data_buffer + data_index * 6 + j) =
          read_buf[j] * 1.0f * p.a_range / p.max_value * GRAVITY;
    }
    for (int j = 3; j < 6; j++) {
        *(data_buffer + data_index * 6 + j) =
          read_buf[j] * 1.0f * p.w_range / p.max_value * MATH_PI / 180;
    }
    if (data_state != TRAJ_PRAPARE) {
        get_traj(data_buffer,
                 data_index,
                 &p,
                 &data_state,
                 data_output_realtime,
                 data_output_cali,
                 &data_output_cali_len,
                 &amp_vector,
                 &amp_sum);
#if 1
        NRF_LOG_DEBUG("% 6d, % 2d, x:% 6d, y:% 6d, z:% 6d\n",
                      data_index,
                      data_state,
                      (int32_t)(data_output_realtime[0] * 1000.),
                      (int32_t)(data_output_realtime[1] * 1000.),
                      (int32_t)(data_output_realtime[2] * 1000.));
#endif
        if (data_state == TRAJ_OUTPUT) {
            for (int j = 0; j < data_output_cali_len; j++) {
                NRF_LOG_DEBUG(
                  "--------------Calied Data-> %d/%d, x:% 6d, "
                  "y:% 6d, z:% 6d\n",
                  j,
                  data_output_cali_len,
                  (int32_t)(*(data_output_cali + j * 3 + 0) * 1000.),
                  (int32_t)(*(data_output_cali + j * 3 + 1) * 1000.),
                  (int32_t)(*(data_output_cali + j * 3 + 2) * 1000.));
            }
            NRF_LOG_DEBUG("--------------Calied Data-> amp_sum:% 6d, "
                          "amp_vector:% 6d\n",
                          (int32_t)(amp_sum * 1000.),
                          (int32_t)(amp_vector * 1000.));
        }
    }
    if (data_state == TRAJ_OUTPUT) {
        NRF_LOG_WARNING("read done.\r\n");
        traj_stop_sampe_flag = 1;
        icm_20689_deinit();
        traj_int_deinit();
    }
}

void wakeup_pin_handler(nrf_drv_gpiote_pin_t pin, nrf_gpiote_polarity_t action)
{
    // NVIC_SystemReset();
}

static void traj_int_init(void)
{
    ret_code_t err_code;

    err_code = nrf_drv_gpiote_init();
    APP_ERROR_CHECK(err_code);

    nrf_drv_gpiote_in_config_t in_config = GPIOTE_CONFIG_IN_SENSE_LOTOHI(true);
    in_config.pull = NRF_GPIO_PIN_PULLDOWN;

    err_code = nrf_drv_gpiote_in_init(TRAJ_INT_PIN, &in_config, in_pin_handler);
    APP_ERROR_CHECK(err_code);

    nrf_drv_gpiote_in_event_enable(TRAJ_INT_PIN, true);
}

static void traj_int_deinit(void)
{
    ret_code_t err_code;

    nrf_drv_gpiote_in_event_enable(TRAJ_INT_PIN, false);

    nrf_drv_gpiote_in_uninit(TRAJ_INT_PIN);

    nrf_drv_gpiote_uninit();
}

static void traj_wakeup_init(void)
{
    ret_code_t err_code;

    err_code = nrf_drv_gpiote_init();
    APP_ERROR_CHECK(err_code);

    nrf_drv_gpiote_in_config_t in_config = GPIOTE_CONFIG_IN_SENSE_LOTOHI(false);
    in_config.pull = NRF_GPIO_PIN_PULLDOWN;

    err_code =
      nrf_drv_gpiote_in_init(TRAJ_INT_PIN, &in_config, wakeup_pin_handler);
    APP_ERROR_CHECK(err_code);

    nrf_drv_gpiote_in_event_enable(TRAJ_INT_PIN, true);
}

/**
 * @brief chip bug fixed func
 */
void sys_workaround(void)
{
    /*pan87_workaround*/
#if (__FPU_USED == 1U)
    /*Set bit 7 and bits 4..0 in the mask to one (0x ...00 1001 1111)*/
#define FPU_EXCEPTION_MASK 0x0000009F
    /* Clear exceptions and PendingIRQ from the FPU unit */
    __set_FPSCR(__get_FPSCR() & ~(FPU_EXCEPTION_MASK));
    (void)__get_FPSCR();
    NVIC_ClearPendingIRQ(FPU_IRQn);
#endif
}

static void sleep_mode_enter(void)
{
    uint32_t err_code;

    // Go to system-off mode (this function will not return; wakeup will cause a
    // reset).
    ret_code_t ret_code = sd_power_system_off();
    if (ret_code == NRF_ERROR_SOFTDEVICE_NOT_ENABLED) {
        NRF_POWER->SYSTEMOFF = POWER_SYSTEMOFF_SYSTEMOFF_Enter;
    }
}

void EnterDFU(void)
{
#define BOOTLOADER_DFU_START 0XB1
    sd_power_gpregret_clr(0, 0xffffffff);
    sd_power_gpregret_set(0, BOOTLOADER_DFU_START);
    NRF_POWER->RAMON |=
      (POWER_RAMON_OFFRAM0_RAM0On << POWER_RAMON_OFFRAM0_Pos) |
      (POWER_RAMON_OFFRAM1_RAM1On << POWER_RAMON_OFFRAM1_Pos);
    nrf_delay_ms(10);
    NVIC_SystemReset();
}

extern nrf_dfu_settings_t s_dfu_settings;

int main(void)
{
    uint32_t err_code;

    err_code = NRF_LOG_INIT(NULL);
    APP_ERROR_CHECK(err_code);

    timers_init();
    sys_workaround();
    ble_stack_init();
    gap_params_init();
    services_init();

#if 1
    sync_sys_config_from_flash();

    advertising_init();
    conn_params_init();
    err_code = ble_advertising_start(BLE_ADV_MODE_FAST);
    APP_ERROR_CHECK(err_code);

    nrf_gpio_cfg_output(LED_OTA_PIN);
    nrf_gpio_cfg_output(LED_CONFIG_PIN);
    nrf_gpio_cfg_output(TRAJ_EN_PIN);
    nrf_gpio_pin_clear(LED_OTA_PIN);
    nrf_gpio_pin_clear(LED_CONFIG_PIN);
    nrf_gpio_pin_set(TRAJ_EN_PIN);

    err_code = hal_nfc_setup(nfc_callback, NULL);
    APP_ERROR_CHECK(err_code);

    // Start sensing NFC field
    err_code = hal_nfc_start();
    APP_ERROR_CHECK(err_code);

    uint32_t reg;
    sd_power_gpregret_get(0, &reg);
    NRF_LOG_ERROR("gpregret flag: 0x%x\r\n", reg);
    // judge whether dfu flag is set and enter dfu mode
    if (sys_config.dfu_flag) {
        NRF_LOG_WARNING("enter dfu mode, stop adv, stop nfc\r\n");
        sd_ble_gap_adv_stop();
        hal_nfc_stop();
        sys_config.dfu_flag = 0;
        sys_config.nfc_flag = 0;
        sync_sys_config_to_flash();

        EnterDFU();
    }

    // judge whether nfc flag is set and enter nfc config mode
    if (sys_config.nfc_flag) {
        NRF_LOG_WARNING("enter nfc config mode, stop adv\r\n");
        sd_ble_gap_adv_stop();
        for (;;) {
            power_manage();
        }
    }

    // Configure WDT.
    nrf_drv_wdt_config_t config = NRF_DRV_WDT_DEAFULT_CONFIG;
    err_code = nrf_drv_wdt_init(&config, NULL);
    APP_ERROR_CHECK(err_code);
    err_code = nrf_drv_wdt_channel_alloc(&m_channel_id);
    APP_ERROR_CHECK(err_code);
    nrf_drv_wdt_enable();
    err_code = app_timer_start(m_wdt_feed_timer_id, WDT_FEED_INTERVAL, NULL);
    APP_ERROR_CHECK(err_code);

#endif

    p.max_value = 32768;
    p.a_range = 4;
    p.w_range = 2000;
    p.fs = 250;
    p.ts = 8;
    p.num_point_cali = (int32_t)(p.fs * p.ts);
    p.num_point_judge = p.fs / 3;
    p.var_acc = 0.05f;
    p.var_gyro = 0.0001f;
    p.kp_stable = 5;
    p.kp_instable = 1;

    data_buffer = (float*)(malloc(p.num_point_judge * 6 * sizeof(float)));
    APP_ERROR_CHECK_BOOL(data_buffer != NULL);
    data_output_cali = (float*)(malloc((p.num_point_cali * 3 * sizeof(float))));
    APP_ERROR_CHECK_BOOL(data_output_cali != NULL);
    APP_ERROR_CHECK_BOOL(data_output_cali != NULL);
    memset(data_buffer, 0, p.num_point_judge * 6 * sizeof(float));
    memset(data_output_cali, 0, p.num_point_cali * 3 * sizeof(float));

    NRF_LOG_DEBUG("traj start sample\r\n");
    nrf_gpio_pin_clear(TRAJ_EN_PIN);
    traj_int_init();
    icm_20689_init();
    if (icm_20689_set_mode(ICM_20689_WORK_MODE_INT) < 0) {
        NRF_LOG_WARNING("set mode failed");
    }

    uint32_t wait_time = 0;
    while ((!traj_stop_sampe_flag) && (wait_time < 30000)) {
        nrf_delay_ms(100);
        wait_time += 100;
    }

    if (wait_time >= 30000) {
        icm_20689_deinit();
        traj_int_deinit();
        NRF_LOG_DEBUG("traj sample timeout\r\n");
        goto __sleep;
    }

    NRF_LOG_DEBUG("traj stop sample\r\n");

    if (data_buffer) {
        free(data_buffer);
    }

    // wait connect
    wait_time = 0;
    while ((!sync_traj_data_flag) && (wait_time < 30000)) {
        nrf_delay_ms(100);
        wait_time += 100;
    }

    if (wait_time >= 30000) {
        NRF_LOG_DEBUG("wait connect time\r\n");
        goto __sleep;
    }

// todo: use nfc to config sn, model, fw_ver
#if 0
    uint32_t     traj_prot_len;
    traj_prot_t* traj_prot;
    traj_prot_len =
        sizeof(traj_prot_t) + data_output_cali_len * 3 * sizeof(float);
    traj_prot = (traj_prot_t*)malloc(traj_prot_len);
    strcpy(traj_prot->sn, "123456789");
    strcpy(traj_prot->model, "santong");
    strcpy(traj_prot->fw_ver, FW_VER);
    traj_prot->bat_level = 100;
    traj_prot->utc_time = 0;
    traj_prot->traj_amp_sum = amp_sum;
    traj_prot->traj_amp_vector = amp_vector;
    traj_prot->raw_wave_sample_rate = p.fs;
    traj_prot->raw_wave_total_size = data_output_cali_len * 3 * sizeof(float);
    memcpy(((uint8_t*)traj_prot) + sizeof(traj_prot_t),
           (uint8_t*)data_output_cali,
           data_output_cali_len * 3 * sizeof(float));

    NRF_LOG_WARNING("master connect, wait notify\r\n");

    // wait notify
    wait_time = 0;
    while ((!m_nus.is_notification_enabled) && (wait_time < 30000)) {
        nrf_delay_ms(100);
        wait_time += 100;
    }

    if (wait_time >= 30000) {
        goto __sleep;
    }

    uint32_t retry_count = 5;
__retry:
    // send data
    uint32_t send_pos = 0;
    uint32_t send_len = 0;
    uint8_t* send_data = (uint8_t*)traj_prot;
    if (retry_count) {
        while (send_pos < traj_prot_len) {
            if ((traj_prot_len - send_pos) < 20) {
                send_len = traj_prot_len - send_pos;
            } else {
                send_len = 20;
            }
            err_code =
                ble_nus_string_send(&m_nus, send_data + send_pos, send_len);
            if (err_code != NRF_SUCCESS) {
                retry_count--;
                NRF_LOG_ERROR(
                    "send failed, retry: %d/%d\r\n", 5 - retry_count, 5);
                nrf_delay_ms(1000);
                goto __retry;
            } else if (err_code == NRF_SUCCESS) {
                send_pos += send_len;
                NRF_LOG_WARNING("send ok, %d/%d\r\n", send_pos, traj_prot_len);
                // nrf_delay_ms(50);
            }
        }
    }

    if (traj_prot) {
        free(traj_prot);
    }
#else
    NRF_LOG_WARNING("master connect, wait notify\r\n");

    // wait notify
    wait_time = 0;
    while ((!m_nus.is_notification_enabled) && (wait_time < 30000)) {
        nrf_delay_ms(100);
        wait_time += 100;
    }

    if (wait_time >= 30000) {
        goto __sleep;
    }
    uint8_t send_buf[20];
    for (int i = 0; i < data_output_cali_len; i++) {
        memset(send_buf, 0, sizeof(send_buf));
        sprintf(send_buf,
                "%04d,%04d,%04d\r\n",
                (int32_t)(data_output_cali[i * 3] * 1000.),
                (int32_t)(data_output_cali[i * 3 + 1] * 1000.),
                (int32_t)(data_output_cali[i * 3 + 2] * 1000.));
        NRF_LOG_WARNING("%s", (uint32_t)send_buf);

        err_code = ble_nus_string_send(&m_nus, send_buf, strlen(send_buf));

        if (err_code != NRF_SUCCESS) {
            NRF_LOG_ERROR("send failed\r\n");
            nrf_delay_ms(100);
        } else if (err_code == NRF_SUCCESS) {
            // NRF_LOG_WARNING("send ok, %d\r\n", strlen(send_buf));
            nrf_delay_ms(20);
        }
    }
#endif

__sleep:
    if (data_output_cali) {
        free(data_output_cali);
    }
    icm_20689_init();
    icm_20689_set_mode(ICM_20689_WORK_MODE_WAKEUP);
    traj_wakeup_init();
    NRF_LOG_DEBUG("sys ready to off\r\n");
    NRF_LOG_FLUSH();
    sleep_mode_enter();

    for (;;) {
        power_manage();
    }
}
#endif

static void watchdog_feed_enabled_channels(void)
{
    uint32_t channel;

    if (NRF_WDT->RUNSTATUS == 0) {
        return;
    }

    for (channel = 0; channel < 8; channel++) {
        if ((NRF_WDT->RREN & (1UL << channel)) != 0) {
            NRF_WDT->RR[channel] = WDT_RR_RR_Reload;
        }
    }
}

static void watchdog_init_or_adopt(void)
{
    /*
     * The nRF52 WDT cannot be stopped or reconfigured after it has started.
     * A soft reset can therefore enter this application with a WDT that was
     * started by an earlier image. Feed every enabled reload channel
     * immediately and keep using that configuration until the next power-on
     * reset.
     */
    if (NRF_WDT->RUNSTATUS != 0U) {
        watchdog_feed_enabled_channels();
        return;
    }

    nrf_wdt_behaviour_set(NRF_WDT_BEHAVIOUR_RUN_SLEEP);
    nrf_wdt_reload_value_set(
      (WDT_CONFIG_RELOAD_VALUE * 32768UL) / 1000UL);
    nrf_wdt_reload_request_enable(NRF_WDT_RR0);
    nrf_wdt_reload_request_set(NRF_WDT_RR0);
    nrf_wdt_task_trigger(NRF_WDT_TASK_START);
}

static void motion_put_le16(uint8_t* dst, uint16_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
}

static void motion_put_le32(uint8_t* dst, uint32_t value)
{
    dst[0] = (uint8_t)value;
    dst[1] = (uint8_t)(value >> 8);
    dst[2] = (uint8_t)(value >> 16);
    dst[3] = (uint8_t)(value >> 24);
}

static uint32_t motion_session_id_generate(uint32_t domain)
{
    uint8_t random_bytes[4] = {0U, 0U, 0U, 0U};
    uint8_t bytes_available = 0U;
    uint32_t random_word = 0U;
    uint32_t session_id;
    uint32_t err_code;
    uint32_t attempts;

    /*
     * app_timer_cnt_get() is almost deterministic during boot and can repeat
     * after a reset.  Mix four bytes from the SoftDevice RNG so that a reboot
     * always starts a genuinely new host-visible stream session.
     */
    for (attempts = 0U; attempts < 100U; attempts++) {
        err_code =
          sd_rand_application_bytes_available_get(&bytes_available);
        if ((err_code == NRF_SUCCESS) &&
            (bytes_available >= sizeof(random_bytes))) {
            err_code = sd_rand_application_vector_get(
              random_bytes, (uint8_t)sizeof(random_bytes));
            if (err_code == NRF_SUCCESS) {
                break;
            }
        }
        nrf_delay_ms(1U);
        watchdog_feed_enabled_channels();
    }

    random_word =
      ((uint32_t)random_bytes[0]) |
      ((uint32_t)random_bytes[1] << 8U) |
      ((uint32_t)random_bytes[2] << 16U) |
      ((uint32_t)random_bytes[3] << 24U);
    session_id =
      NRF_FICR->DEVICEID[0] ^ NRF_FICR->DEVICEID[1] ^
      app_timer_cnt_get() ^ domain ^ random_word;
    if (session_id == 0U) {
        session_id = domain ^ 0x53455353UL;
    }
    return session_id;
}

static uint16_t motion_saturate_u16(uint32_t value)
{
    return value > 0xffffUL ? 0xffffU : (uint16_t)value;
}

static uint8_t battery_saadc_wait(nrf_saadc_event_t event)
{
    uint32_t timeout = 100000UL;

    while ((!nrf_saadc_event_check(event)) && (timeout > 0U)) {
        timeout--;
    }
    return timeout > 0U ? 1U : 0U;
}

static uint16_t battery_read_mv(void)
{
    nrf_saadc_value_t sample = 0;
    uint32_t config;
    uint32_t battery_mv;
    uint8_t success = 1U;

    config =
      (SAADC_CH_CONFIG_RESP_Bypass << SAADC_CH_CONFIG_RESP_Pos) |
      (SAADC_CH_CONFIG_RESN_Bypass << SAADC_CH_CONFIG_RESN_Pos) |
      (SAADC_CH_CONFIG_GAIN_Gain1_4 << SAADC_CH_CONFIG_GAIN_Pos) |
      (SAADC_CH_CONFIG_REFSEL_Internal << SAADC_CH_CONFIG_REFSEL_Pos) |
      (SAADC_CH_CONFIG_TACQ_40us << SAADC_CH_CONFIG_TACQ_Pos) |
      (SAADC_CH_CONFIG_MODE_SE << SAADC_CH_CONFIG_MODE_Pos) |
      (SAADC_CH_CONFIG_BURST_Disabled << SAADC_CH_CONFIG_BURST_Pos);

    NRF_SAADC->CH[0].PSELP = SAADC_CH_PSELP_PSELP_AnalogInput2;
    NRF_SAADC->CH[0].PSELN = SAADC_CH_PSELN_PSELN_NC;
    NRF_SAADC->CH[0].CONFIG = config;
    NRF_SAADC->RESOLUTION = SAADC_RESOLUTION_VAL_12bit;
    NRF_SAADC->OVERSAMPLE = SAADC_OVERSAMPLE_OVERSAMPLE_Bypass;
    NRF_SAADC->RESULT.PTR = (uint32_t)&sample;
    NRF_SAADC->RESULT.MAXCNT = 1U;

    nrf_saadc_event_clear(NRF_SAADC_EVENT_STARTED);
    nrf_saadc_event_clear(NRF_SAADC_EVENT_END);
    nrf_saadc_event_clear(NRF_SAADC_EVENT_STOPPED);
    nrf_saadc_enable();
    nrf_saadc_task_trigger(NRF_SAADC_TASK_START);
    success = battery_saadc_wait(NRF_SAADC_EVENT_STARTED);
    if (success) {
        nrf_saadc_task_trigger(NRF_SAADC_TASK_SAMPLE);
        success = battery_saadc_wait(NRF_SAADC_EVENT_END);
    }
    nrf_saadc_task_trigger(NRF_SAADC_TASK_STOP);
    (void)battery_saadc_wait(NRF_SAADC_EVENT_STOPPED);
    nrf_saadc_disable();
    NRF_SAADC->CH[0].PSELP = SAADC_CH_PSELP_PSELP_NC;

    if (!success) {
        return 0xffffU;
    }
    if (sample < 0) {
        sample = 0;
    }

    /*
     * Internal 0.6 V reference and gain 1/4 give a 2.4 V ADC full scale.
     * R17/R18 divide VBAT by two, so VBAT full scale is 4.8 V.
     */
    battery_mv =
      (((uint32_t)sample * 4800UL) + 2048UL) / 4096UL;
    return motion_saturate_u16(battery_mv);
}

static void motion_control_write_handler(const uint8_t* data,
                                         uint16_t length)
{
    if ((data == NULL) ||
        (length < MOTION_CONTROL_HEADER_SIZE) ||
        (length > sizeof(m_control_request))) {
        return;
    }

    CRITICAL_REGION_ENTER();
    if (!m_control_request_pending) {
        memcpy(m_control_request, data, length);
        m_control_request_length = length;
        m_control_request_pending = 1U;
    }
    CRITICAL_REGION_EXIT();
}

static void motion_queue_response(uint8_t command,
                                  uint8_t request_id,
                                  uint8_t result,
                                  const uint8_t* payload,
                                  uint8_t payload_length)
{
    if (payload_length > MOTION_RESPONSE_MAX_PAYLOAD) {
        payload_length = 0U;
        result = MOTION_RESULT_BAD_PARAMETER;
    }

    m_status_response[0] = MOTION_CONTROL_VERSION;
    m_status_response[1] = (uint8_t)(command | 0x80U);
    m_status_response[2] = request_id;
    m_status_response[3] = result;
    m_status_response[4] = payload_length;
    if ((payload != NULL) && (payload_length > 0U)) {
        memcpy(&m_status_response[MOTION_RESPONSE_HEADER_SIZE],
               payload,
               payload_length);
    }
    m_status_response_length =
      (uint16_t)(MOTION_RESPONSE_HEADER_SIZE + payload_length);
    m_status_response_pending = 1U;
}

static void motion_build_info_payload(uint8_t payload[MOTION_INFO_PAYLOAD_SIZE])
{
    memset(payload, 0, MOTION_INFO_PAYLOAD_SIZE);
    payload[0] = (uint8_t)TX_DEVICE_ID;
    payload[1] = (uint8_t)FW_VER_MAJOR;
    payload[2] = (uint8_t)FW_VER_MINOR;
    payload[3] = (uint8_t)FW_VER_PATCH;
    payload[4] = MOTION_PROTOCOL_VERSION;
    payload[5] = 8U;
    motion_put_le16(&payload[6], 1000U);
    motion_put_le16(&payload[8], REALTIME_TRAJ_SAMPLE_HZ);
    payload[10] = REALTIME_TRAJ_OUTPUT_HZ;
    motion_put_le16(&payload[11], 0x007fU);
}

static void motion_build_status_payload(
  uint8_t payload[MOTION_STATUS_PAYLOAD_SIZE])
{
    uint8_t flags = 0U;
    uint16_t battery_mv = battery_read_mv();

    memset(payload, 0, MOTION_STATUS_PAYLOAD_SIZE);
    if (m_stream_enabled) {
        flags |= MOTION_STATUS_FLAG_STREAMING;
    }
    if (m_realtime_traj.state != REALTIME_TRAJ_CALIBRATING) {
        flags |= MOTION_STATUS_FLAG_CALIBRATED;
    }
    if (m_sensor_ok) {
        flags |= MOTION_STATUS_FLAG_SENSOR_OK;
    }
    if ((battery_mv != 0xffffU) && (battery_mv <= 3400U)) {
        flags |= MOTION_STATUS_FLAG_LOW_BATTERY;
    }

    payload[0] = flags;
    motion_put_le32(&payload[2], m_reset_reason);
    motion_put_le32(&payload[6],
                    m_uptime_sample_count / REALTIME_TRAJ_SAMPLE_HZ);
    motion_put_le16(&payload[10], battery_mv);
    motion_put_le32(&payload[12], m_current_session_id);
    motion_put_le32(&payload[16], m_latest_sample_sequence);
    motion_put_le16(&payload[20],
                    motion_saturate_u16(m_sample_overruns));
    motion_put_le16(&payload[22],
                    motion_saturate_u16(m_sensor_error_count));
    motion_put_le16(&payload[24],
                    motion_saturate_u16(m_motion_queue_drops));
    motion_put_le16(&payload[26],
                    motion_saturate_u16(m_ble_resource_wait_count));
    payload[28] = m_motion_queue_count;
}

static void motion_process_control_request(void)
{
    uint8_t request[
      MOTION_CONTROL_HEADER_SIZE + MOTION_CONTROL_MAX_PAYLOAD];
    uint16_t request_length;
    uint8_t command;
    uint8_t request_id;
    uint8_t payload_length;
    uint8_t result = MOTION_RESULT_OK;
    uint8_t response_payload[MOTION_RESPONSE_MAX_PAYLOAD];
    uint8_t response_length = 0U;

    if ((!m_control_request_pending) || m_status_response_pending) {
        return;
    }

    CRITICAL_REGION_ENTER();
    request_length = m_control_request_length;
    memcpy(request, m_control_request, request_length);
    m_control_request_pending = 0U;
    CRITICAL_REGION_EXIT();

    command = request[1];
    request_id = request[2];
    payload_length = request[3];
    if ((request[0] != MOTION_CONTROL_VERSION) ||
        (request_length !=
         (uint16_t)(MOTION_CONTROL_HEADER_SIZE + payload_length))) {
        motion_queue_response(command,
                              request_id,
                              MOTION_RESULT_BAD_PARAMETER,
                              NULL,
                              0U);
        return;
    }

    switch (command) {
        case MOTION_CMD_START_STREAM:
            if ((payload_length != 4U) ||
                ((((uint16_t)request[5] << 8) | request[4]) !=
                 REALTIME_TRAJ_SAMPLE_HZ) ||
                (request[6] != REALTIME_TRAJ_OUTPUT_HZ) ||
                (request[7] != MOTION_STREAM_ALL)) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                m_stream_enabled = 1U;
            }
            break;

        case MOTION_CMD_STOP_STREAM:
            if (payload_length != 0U) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                m_stream_enabled = 0U;
                motion_queue_clear();
            }
            break;

        case MOTION_CMD_GET_INFO:
            if (payload_length != 0U) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                motion_build_info_payload(response_payload);
                response_length = MOTION_INFO_PAYLOAD_SIZE;
            }
            break;

        case MOTION_CMD_GET_STATUS:
            if (payload_length != 0U) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                motion_build_status_payload(response_payload);
                response_length = MOTION_STATUS_PAYLOAD_SIZE;
            }
            break;

        case MOTION_CMD_RESET_STATS:
            if (payload_length != 0U) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                m_sample_overruns = 0U;
                m_sensor_error_count = 0U;
                m_motion_queue_drops = 0U;
                m_ble_resource_wait_count = 0U;
            }
            break;

        case MOTION_CMD_RECALIBRATE:
            if (payload_length != 0U) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                m_recalibrate_requested = 1U;
            }
            break;

        case MOTION_CMD_REBOOT:
            if ((payload_length != 1U) ||
                (request[4] != MOTION_REBOOT_CONFIRM)) {
                result = MOTION_RESULT_BAD_PARAMETER;
            } else {
                m_reboot_requested = 1U;
            }
            break;

        default:
            result = MOTION_RESULT_UNSUPPORTED;
            break;
    }

    motion_queue_response(command,
                          request_id,
                          result,
                          response_payload,
                          response_length);
}

static int32_t position_m_to_mm(float position_m)
{
    float position_mm = position_m * 1000.0f;

    if (position_mm > 2147483000.0f) {
        return 2147483647;
    }
    if (position_mm < -2147483000.0f) {
        return (int32_t)0x80000000;
    }
    if (position_mm >= 0.0f) {
        return (int32_t)(position_mm + 0.5f);
    }
    return (int32_t)(position_mm - 0.5f);
}

int main(void)
{
    uint32_t err_code;
    uint32_t sample_sequence = 0U;
    uint16_t packet_sequence = 0U;
    uint16_t sample_tick_10ms = 0;
    uint16_t output_tick_10ms = 0;
    uint8_t packet[MOTION_PACKET_SIZE];
    uint8_t trajectory_flags;
    uint8_t device_id;
    int16_t raw[6];
    int32_t position_mm[3] = { 0, 0, 0 };
    float position_m[3] = { 0.0f, 0.0f, 0.0f };
    float dt_seconds = 1.0f / (float)REALTIME_TRAJ_SAMPLE_HZ;
    uint32_t sample_timestamp_ticks;
    uint32_t last_sample_timestamp_ticks = 0U;
    uint32_t elapsed_ticks;
    uint32_t elapsed_10ms;
    uint32_t observed_sample_overruns = 0U;
    uint32_t coordinate_source_sequence = 0U;
    uint32_t session_id;
    uint64_t sample_tick_32k = 0U;
    uint32_t rtc_frequency =
      APP_TIMER_CLOCK_FREQ / (APP_TIMER_PRESCALER + 1U);
    uint8_t has_sample_timestamp = 0U;
    uint8_t batch_sample_count = 0U;
    uint8_t sensor_error_pending = 0U;
    uint8_t coordinate_valid = 0U;
    uint8_t axis;
    uint16_t coordinate_flags = MOTION_COORD_FLAG_CALIBRATING;
    uint16_t coordinate_lag;
    motion_packet_fields_t packet_fields;
    realtime_traj_event_t trajectory_event;

    /*
     * Take ownership of an inherited watchdog before any peripheral or
     * SoftDevice initialization can consume the remaining timeout.
     */
    watchdog_init_or_adopt();
    m_reset_reason = NRF_POWER->RESETREAS;
    NRF_POWER->RESETREAS = m_reset_reason;

    /*
     * TRAJ_EN drives the gate of the ICM-20689 high-side P-MOSFET and is
     * active low. Drive it high first so the sensor starts from a known
     * powered-off state instead of the GPIO reset-state high impedance.
     */
    nrf_gpio_pin_set(TRAJ_EN_PIN);
    nrf_gpio_cfg_output(TRAJ_EN_PIN);
    nrf_delay_ms(TRAJ_POWER_OFF_DISCHARGE_MS);
    watchdog_feed_enabled_channels();

    timers_init();
    watchdog_feed_enabled_channels();
    ble_stack_init();
    watchdog_feed_enabled_channels();
    gap_params_init();
    services_init();
    watchdog_feed_enabled_channels();
    sync_sys_config_from_flash();
    if (sys_config.no != (uint8_t)TX_DEVICE_ID) {
        sys_config.no = (uint8_t)TX_DEVICE_ID;
        sync_sys_config_to_flash();
    }
    device_id = (uint8_t)TX_DEVICE_ID;
    memset(&packet_fields, 0, sizeof(packet_fields));
    packet_fields.device_id = device_id;
    session_id = motion_session_id_generate(0x4d4f544eUL);
    m_current_session_id = session_id;
    advertising_init();
    conn_params_init();
    watchdog_feed_enabled_channels();

    err_code = ble_advertising_start(BLE_ADV_MODE_FAST);
    APP_ERROR_CHECK(err_code);
    watchdog_feed_enabled_channels();

    /* Power the sensor and wait for VCC_ICM to settle before the first SPI access. */
    nrf_gpio_pin_clear(TRAJ_EN_PIN);
    nrf_delay_ms(TRAJ_POWER_ON_SETTLE_MS);

    realtime_traj_init(&m_realtime_traj);
    if (icm_20689_init() < 0) {
        APP_ERROR_HANDLER(NRF_ERROR_INTERNAL);
    }
    watchdog_feed_enabled_channels();
    sample_clock_init();
    realtime_sample_interrupt_init();
    if (icm_20689_set_mode(ICM_20689_WORK_MODE_INT) < 0) {
        APP_ERROR_HANDLER(NRF_ERROR_INTERNAL);
    }
    watchdog_feed_enabled_channels();

    for (;;) {
        motion_process_control_request();

        if (m_recalibrate_requested) {
            m_recalibrate_requested = 0U;
            realtime_traj_init(&m_realtime_traj);
            memset(position_mm, 0, sizeof(position_mm));
            memset(position_m, 0, sizeof(position_m));
            coordinate_flags = MOTION_COORD_FLAG_CALIBRATING;
            coordinate_source_sequence = 0U;
            coordinate_valid = 0U;
            batch_sample_count = 0U;
            sample_sequence = 0U;
            m_latest_sample_sequence = 0U;
            motion_queue_clear();
            session_id = motion_session_id_generate(0x52454341UL);
            m_current_session_id = session_id;
        }

        motion_send_pump();
        if (m_reboot_response_accepted) {
            err_code = app_timer_cnt_diff_compute(app_timer_cnt_get(),
                                                  m_reboot_response_tick,
                                                  &elapsed_ticks);
            APP_ERROR_CHECK(err_code);
            if (elapsed_ticks >=
                APP_TIMER_TICKS(100, APP_TIMER_PRESCALER)) {
                NVIC_SystemReset();
            }
        }

        if (take_sample_pending(&sample_timestamp_ticks)) {
            if (icm_20689_int_read_data(sizeof(raw), raw) != sizeof(raw)) {
                sensor_error_pending = 1U;
                m_sensor_error_count++;
                m_sensor_ok = 0U;
                continue;
            }
            m_sensor_ok = 1U;

            if (has_sample_timestamp) {
                err_code = app_timer_cnt_diff_compute(
                  sample_timestamp_ticks,
                  last_sample_timestamp_ticks,
                  &elapsed_ticks);
                APP_ERROR_CHECK(err_code);
                dt_seconds = (float)elapsed_ticks / (float)rtc_frequency;
                sample_tick_32k += elapsed_ticks;
                elapsed_10ms =
                  (elapsed_ticks * 100U + rtc_frequency / 2U) /
                  rtc_frequency;
                if (elapsed_10ms == 0U) {
                    elapsed_10ms = 1U;
                }
                sample_tick_10ms =
                  (uint16_t)(sample_tick_10ms + elapsed_10ms);
            } else {
                has_sample_timestamp = 1U;
                sample_tick_32k = sample_timestamp_ticks;
            }
            last_sample_timestamp_ticks = sample_timestamp_ticks;

            trajectory_event =
              realtime_traj_process(&m_realtime_traj,
                                     raw,
                                     dt_seconds,
                                     sample_tick_10ms,
                                     position_m,
                                     &output_tick_10ms);

            if (trajectory_event == REALTIME_TRAJ_NO_OUTPUT) {
                if (m_realtime_traj.state == REALTIME_TRAJ_CALIBRATING) {
                    coordinate_flags = MOTION_COORD_FLAG_CALIBRATING;
                } else if (m_realtime_traj.state ==
                           REALTIME_TRAJ_STATIONARY) {
                    coordinate_flags = MOTION_COORD_FLAG_STATIONARY;
                    if (coordinate_valid) {
                        coordinate_flags |= MOTION_COORD_FLAG_HELD;
                    }
                } else {
                    coordinate_flags = MOTION_COORD_FLAG_MOVING;
                }
            }

            if (trajectory_event != REALTIME_TRAJ_NO_OUTPUT) {
                trajectory_flags =
                  realtime_traj_take_quality_flags(&m_realtime_traj);
                coordinate_flags = 0U;
                if (trajectory_event == REALTIME_TRAJ_ABORT) {
                    coordinate_flags = MOTION_COORD_FLAG_STATIONARY |
                                       MOTION_COORD_FLAG_STOP |
                                       MOTION_COORD_FLAG_UNRELIABLE |
                                       MOTION_COORD_FLAG_ABORTED;
                } else if (trajectory_event == REALTIME_TRAJ_STOP) {
                    coordinate_flags = MOTION_COORD_FLAG_STATIONARY |
                                       MOTION_COORD_FLAG_STOP;
                } else {
                    coordinate_flags = MOTION_COORD_FLAG_MOVING;
                    if (trajectory_event == REALTIME_TRAJ_ORIGIN) {
                        coordinate_flags |= MOTION_COORD_FLAG_ORIGIN;
                    }
                }
                if ((trajectory_flags & TRAJECTORY_FLAG_UNRELIABLE) != 0U) {
                    coordinate_flags |= MOTION_COORD_FLAG_UNRELIABLE;
                }

                for (axis = 0; axis < 3; axis++) {
                    position_mm[axis] = position_m_to_mm(position_m[axis]);
                }
                coordinate_lag =
                  (uint16_t)(sample_tick_10ms - output_tick_10ms);
                if (sample_sequence >= coordinate_lag) {
                    coordinate_source_sequence =
                      sample_sequence - coordinate_lag;
                } else {
                    coordinate_source_sequence = 0U;
                }
                coordinate_valid = 1U;
            }

            if (batch_sample_count == 0U) {
                memset(&packet_fields, 0, sizeof(packet_fields));
                packet_fields.device_id = device_id;
                packet_fields.session_id = session_id;
                packet_fields.first_sample_sequence = sample_sequence;
                packet_fields.first_sample_tick_32k = sample_tick_32k;
                packet_fields.packet_sequence = packet_sequence;
            }

            packet_fields.samples[batch_sample_count].tick_offset_32k =
              (uint16_t)(sample_tick_32k -
                         packet_fields.first_sample_tick_32k);
            for (axis = 0; axis < 6U; axis++) {
                packet_fields.samples[batch_sample_count].axes[axis] =
                  raw[axis];
            }

            if (m_realtime_traj.state != REALTIME_TRAJ_CALIBRATING) {
                packet_fields.flags |= MOTION_PACKET_FLAG_CALIBRATED;
            }
            if (m_sample_overruns != observed_sample_overruns) {
                packet_fields.flags |= MOTION_PACKET_FLAG_SAMPLE_GAP;
                observed_sample_overruns = m_sample_overruns;
            }
            if (sensor_error_pending) {
                packet_fields.flags |= MOTION_PACKET_FLAG_SENSOR_ERROR;
                sensor_error_pending = 0U;
            }
            for (axis = 0; axis < 3U; axis++) {
                if ((raw[axis] > 32000) || (raw[axis] < -32000)) {
                    packet_fields.flags |=
                      MOTION_PACKET_FLAG_ACCEL_SATURATED;
                }
                if ((raw[axis + 3U] > 32000) ||
                    (raw[axis + 3U] < -32000)) {
                    packet_fields.flags |=
                      MOTION_PACKET_FLAG_GYRO_SATURATED;
                }
            }

            batch_sample_count++;
            m_latest_sample_sequence = sample_sequence;
            m_uptime_sample_count++;
            sample_sequence++;

            if (batch_sample_count == MOTION_SAMPLE_COUNT) {
                if (coordinate_valid) {
                    packet_fields.flags |= MOTION_PACKET_FLAG_COORD_VALID;
                }
                if ((coordinate_flags & MOTION_COORD_FLAG_UNRELIABLE) != 0U) {
                    packet_fields.flags |= MOTION_PACKET_FLAG_UNRELIABLE;
                }
                packet_fields.coordinate_source_sequence =
                  coordinate_source_sequence;
                for (axis = 0; axis < 3U; axis++) {
                    packet_fields.position_mm[axis] = position_mm[axis];
                }
                packet_fields.coordinate_flags = coordinate_flags;
                motion_packet_encode(packet, &packet_fields);
                packet_sequence++;
                batch_sample_count = 0U;

                if (m_stream_enabled &&
                    (m_conn_handle != BLE_CONN_HANDLE_INVALID) &&
                    m_motion_service.notification_enabled) {
                    motion_queue_push(packet);
                }
            }

            motion_send_pump();
            /*
             * Feed only after the main loop has read and processed a valid
             * IMU sample. A stalled sample interrupt, persistent SPI failure,
             * or blocked processing path will therefore reset the device.
             */
            watchdog_feed_enabled_channels();
        } else {
            if (m_ble_tx_complete) {
                m_ble_tx_complete = 0;
                motion_send_pump();
            }
            power_manage();
        }
    }
}

static void realtime_sample_handler(nrf_drv_gpiote_pin_t pin,
                                    nrf_gpiote_polarity_t action)
{
    (void)pin;
    (void)action;

    m_sample_timestamp_ticks = NRF_RTC2->COUNTER;
    if (!m_sample_pending) {
        m_sample_pending = 1;
    } else {
        m_sample_overruns++;
    }
}

static void sample_clock_init(void)
{
    NRF_RTC2->TASKS_STOP = 1U;
    NRF_RTC2->PRESCALER = 0U;
    NRF_RTC2->TASKS_CLEAR = 1U;
    NRF_RTC2->EVENTS_OVRFLW = 0U;
    NRF_RTC2->TASKS_START = 1U;
}

static uint8_t take_sample_pending(uint32_t* timestamp_ticks)
{
    uint8_t pending = 0;

    CRITICAL_REGION_ENTER();
    if (m_sample_pending) {
        *timestamp_ticks = m_sample_timestamp_ticks;
        m_sample_pending = 0;
        pending = 1;
    }
    CRITICAL_REGION_EXIT();

    return pending;
}

static void realtime_sample_interrupt_init(void)
{
    ret_code_t err_code;
    nrf_drv_gpiote_in_config_t in_config =
      GPIOTE_CONFIG_IN_SENSE_LOTOHI(true);

    err_code = nrf_drv_gpiote_init();
    APP_ERROR_CHECK(err_code);

    in_config.pull = NRF_GPIO_PIN_PULLDOWN;
    err_code = nrf_drv_gpiote_in_init(TRAJ_INT_PIN,
                                      &in_config,
                                      realtime_sample_handler);
    APP_ERROR_CHECK(err_code);
    nrf_drv_gpiote_in_event_enable(TRAJ_INT_PIN, true);
}

static void realtime_sys_workaround(void)
{
#if (__FPU_USED == 1U)
#define REALTIME_FPU_EXCEPTION_MASK 0x0000009F
    __set_FPSCR(__get_FPSCR() & ~(REALTIME_FPU_EXCEPTION_MASK));
    (void)__get_FPSCR();
    NVIC_ClearPendingIRQ(FPU_IRQn);
#endif
}

static void realtime_enter_dfu(void)
{
#define BOOTLOADER_DFU_START 0XB1
    sd_power_gpregret_clr(0, 0xffffffff);
    sd_power_gpregret_set(0, BOOTLOADER_DFU_START);
    NRF_POWER->RAMON |=
      (POWER_RAMON_OFFRAM0_RAM0On << POWER_RAMON_OFFRAM0_Pos) |
      (POWER_RAMON_OFFRAM1_RAM1On << POWER_RAMON_OFFRAM1_Pos);
    nrf_delay_ms(10);
    NVIC_SystemReset();
}

#if 0
int main(void)
{
    uint32_t err_code;
    int16_t read_buf[6];
    float position_m[3];
    realtime_traj_state_t previous_state;
    realtime_traj_event_t trajectory_event;
    uint32_t reg;
    uint32_t status_tick;
    uint32_t now;
    uint32_t status_diff;

    err_code = NRF_LOG_INIT(NULL);
    APP_ERROR_CHECK(err_code);

    timers_init();
    realtime_sys_workaround();
    ble_stack_init();
    gap_params_init();
    services_init();
    sync_sys_config_from_flash();
    advertising_init();
    conn_params_init();

    err_code = ble_advertising_start(BLE_ADV_MODE_FAST);
    APP_ERROR_CHECK(err_code);

    nrf_gpio_cfg_output(LED_OTA_PIN);
    nrf_gpio_cfg_output(LED_CONFIG_PIN);
    nrf_gpio_cfg_output(TRAJ_EN_PIN);
    nrf_gpio_pin_clear(LED_OTA_PIN);
    nrf_gpio_pin_clear(LED_CONFIG_PIN);
    nrf_gpio_pin_clear(TRAJ_EN_PIN);

    err_code = hal_nfc_setup(nfc_callback, NULL);
    APP_ERROR_CHECK(err_code);
    err_code = hal_nfc_start();
    APP_ERROR_CHECK(err_code);

    sd_power_gpregret_get(0, &reg);
    NRF_LOG_INFO("gpregret flag: 0x%x\r\n", reg);
    if (sys_config.dfu_flag) {
        sd_ble_gap_adv_stop();
        hal_nfc_stop();
        sys_config.dfu_flag = 0;
        sys_config.nfc_flag = 0;
        sync_sys_config_to_flash();
        realtime_enter_dfu();
    }

    if (sys_config.nfc_flag) {
        NRF_LOG_WARNING("enter nfc config mode, stop advertising\r\n");
        sd_ble_gap_adv_stop();
        for (;;) {
            power_manage();
        }
    }

    {
        nrf_drv_wdt_config_t config = NRF_DRV_WDT_DEAFULT_CONFIG;
        err_code = nrf_drv_wdt_init(&config, NULL);
        APP_ERROR_CHECK(err_code);
        err_code = nrf_drv_wdt_channel_alloc(&m_channel_id);
        APP_ERROR_CHECK(err_code);
        nrf_drv_wdt_enable();
        err_code = app_timer_start(m_wdt_feed_timer_id,
                                   WDT_FEED_INTERVAL,
                                   NULL);
        APP_ERROR_CHECK(err_code);
    }

    realtime_traj_init(&m_realtime_traj);
    realtime_sample_interrupt_init();
    if (icm_20689_init() < 0) {
        NRF_LOG_ERROR("ICM-20689 initialization failed\r\n");
        APP_ERROR_HANDLER(NRF_ERROR_INTERNAL);
    }
    if (icm_20689_set_mode(ICM_20689_WORK_MODE_INT) < 0) {
        NRF_LOG_ERROR("ICM-20689 interrupt mode failed\r\n");
        APP_ERROR_HANDLER(NRF_ERROR_INTERNAL);
    }

    NRF_LOG_INFO("realtime trajectory: sample=%dHz output=%dHz\r\n",
                 REALTIME_TRAJ_SAMPLE_HZ,
                 REALTIME_TRAJ_OUTPUT_HZ);
    NRF_LOG_INFO("keep the device stationary for startup calibration\r\n");
    status_tick = app_timer_cnt_get();

    for (;;) {
        now = app_timer_cnt_get();
        APP_ERROR_CHECK(
          app_timer_cnt_diff_compute(now, status_tick, &status_diff));
        if (status_diff >= APP_TIMER_TICKS(1000, APP_TIMER_PRESCALER)) {
            status_tick = now;
            NRF_LOG_INFO("heartbeat: state=%d overruns=%u connected=%d notify=%d\r\n",
                         m_realtime_traj.state,
                         m_sample_overruns,
                         m_conn_handle != BLE_CONN_HANDLE_INVALID,
                         m_nus.is_notification_enabled);
        }

        if (take_sample_pending()) {
            if (icm_20689_int_read_data(sizeof(read_buf), read_buf) !=
                sizeof(read_buf)) {
                NRF_LOG_WARNING("ICM-20689 sample read failed\r\n");
                continue;
            }

            previous_state = m_realtime_traj.state;
            trajectory_event =
              realtime_traj_process(&m_realtime_traj, read_buf, position_m);

            if ((previous_state == REALTIME_TRAJ_CALIBRATING) &&
                (m_realtime_traj.state == REALTIME_TRAJ_STATIONARY)) {
                NRF_LOG_INFO("startup calibration complete\r\n");
            }
            if (trajectory_event == REALTIME_TRAJ_ORIGIN) {
                NRF_LOG_INFO("motion started at origin 0,0,0\r\n");
            } else if (trajectory_event == REALTIME_TRAJ_STOP) {
                NRF_LOG_INFO("motion stopped, velocity reset to zero\r\n");
            }

            if (trajectory_event != REALTIME_TRAJ_NO_OUTPUT) {
                queue_trajectory_point(position_m);
            }
            try_send_trajectory_point();
        } else {
            if (m_ble_tx_complete) {
                m_ble_tx_complete = 0;
                try_send_trajectory_point();
            }
            power_manage();
        }
    }
}
#endif
