#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "trajectory_protocol.h"

#define TARGET_DEVICE_NAME       "Mahjong"
#define SCAN_DURATION_MS         BLE_HS_FOREVER
#define CONNECT_TIMEOUT_MS       30000
#define MAX_MAHJONG_CONNECTIONS  4U
#define INVALID_PEER_INDEX       UINT8_MAX

#define MAHJONG_COMPANY_ID_LSB   0xcd
#define MAHJONG_COMPANY_ID_MSB   0xab
#define MAHJONG_CONFIG_MAGIC_LSB 0xba
#define MAHJONG_CONFIG_MAGIC_MSB 0xab
#define MAHJONG_CONFIG_NO_OFFSET 6
#define MAHJONG_CONFIG_TYPE_OFFSET 7

static const char *TAG = "mahjong_nus";
static const char *DATA_TAG = "mahjong_data";

typedef struct {
    ble_addr_t addr;
    uint8_t expected_device_id;
} mahjong_target_t;

/* Add each approved TX address and its unique frame device_id here. */
static const mahjong_target_t TARGETS[] = {
    {
        .addr = {
            .type = BLE_ADDR_RANDOM,
            .val = {0x4b, 0xf7, 0xe7, 0xe0, 0xcf, 0xe0},
        },
        .expected_device_id = 1,
    },
    {
        .addr = {
            .type = BLE_ADDR_RANDOM,
            .val = {0x27, 0x17, 0xe7, 0xf9, 0xf1, 0xff},
        },
        .expected_device_id = 2,
    },
    {
        .addr = {
            .type = BLE_ADDR_RANDOM,
            .val = {0xf1, 0x03, 0x67, 0xf3, 0xcb, 0xdc},
        },
        .expected_device_id = 3,
    },
    {
        .addr = {
            .type = BLE_ADDR_RANDOM,
            .val = {0xf0, 0xe3, 0x27, 0x25, 0x8e, 0xfd},
        },
        .expected_device_id = 4,
    },
};

#define TARGET_COUNT (sizeof(TARGETS) / sizeof(TARGETS[0]))

_Static_assert(TARGET_COUNT <= MAX_MAHJONG_CONNECTIONS,
               "TARGETS exceeds MAX_MAHJONG_CONNECTIONS");

/* Four 50 Hz links fit comfortably at a 20 ms connection interval. */
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
static const ble_uuid_t *NUS_SERVICE_UUID =
    BLE_UUID128_DECLARE(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                        0x93, 0xf3, 0xa3, 0xb5, 0x01, 0x00, 0x40, 0x6e);
/* tx_device sends notifications on its RX-named 0x0003 characteristic. */
static const ble_uuid_t *NUS_NOTIFY_UUID =
    BLE_UUID128_DECLARE(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                        0x93, 0xf3, 0xa3, 0xb5, 0x03, 0x00, 0x40, 0x6e);
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
    uint16_t notify_value_handle;
    uint16_t notify_cccd_handle;
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

static int gap_event_handler(struct ble_gap_event *event, void *arg);
static void start_scan(void);

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
    uint8_t packet[TRAJECTORY_FRAME_SIZE];
    char addr[18];
    uint16_t packet_len = OS_MBUF_PKTLEN(om);
    trajectory_frame_t frame;
    uint16_t expected_sequence;
    uint16_t sequence_gap;
    uint8_t expected_device_id = TARGETS[peer->target_index].expected_device_id;

    if (packet_len != sizeof(packet)) {
        peer->invalid_frames++;
        ESP_LOGW(TAG, "Invalid notification length: conn=%u length=%u",
                 peer->conn_handle, packet_len);
        return;
    }

    if (os_mbuf_copydata(om, 0, packet_len, packet) != 0) {
        ESP_LOGE(TAG, "Failed to copy notification payload: conn=%u",
                 peer->conn_handle);
        return;
    }

    if (!trajectory_frame_decode(packet, packet_len, &frame)) {
        peer->invalid_frames++;
        ESP_LOGW(TAG, "Invalid trajectory frame markers: conn=%u",
                 peer->conn_handle);
        return;
    }

    if (frame.device_id != expected_device_id) {
        peer->invalid_frames++;
        ESP_LOGW(TAG,
                 "Unexpected device id: conn=%u expected=%u actual=%u",
                 peer->conn_handle, expected_device_id, frame.device_id);
        return;
    }

    if (peer->has_sequence) {
        expected_sequence = (uint16_t)(peer->last_sequence + 1U);
        if (frame.sequence != expected_sequence) {
            sequence_gap = (uint16_t)(frame.sequence - expected_sequence);
            if (sequence_gap < 0x8000U) {
                peer->lost_frames += sequence_gap;
                ESP_LOGW(TAG,
                         "Sequence gap: conn=%u id=%u expected=%u actual=%u "
                         "lost=%u total_lost=%lu",
                         peer->conn_handle, frame.device_id, expected_sequence,
                         frame.sequence, sequence_gap,
                         (unsigned long)peer->lost_frames);
            } else {
                ESP_LOGW(TAG,
                         "Sequence restarted: conn=%u id=%u previous=%u actual=%u",
                         peer->conn_handle, frame.device_id,
                         peer->last_sequence, frame.sequence);
            }
        }
    }

    peer->has_sequence = true;
    peer->last_sequence = frame.sequence;
    peer->received_frames++;
    format_addr(&peer->addr, addr, sizeof(addr));
    ESP_LOGI(DATA_TAG,
             "[addr=%s conn=%u id=%u seq=%u tick=%u flags=0x%02X] "
             "x=%ld y=%ld z=%ld",
             addr,
             peer->conn_handle,
             frame.device_id,
             frame.sequence,
             frame.sample_tick_10ms,
             frame.flags,
             (long)frame.position_mm[0],
             (long)frame.position_mm[1],
             (long)frame.position_mm[2]);
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

static int subscribe_write_cb(uint16_t conn_handle,
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
             "NUS notifications enabled: addr=%s conn=%u id=%u ready=%u/%u",
             addr, conn_handle,
             TARGETS[peer->target_index].expected_device_id,
             ready_peer_count(), (unsigned int)TARGET_COUNT);
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
        }
        return 0;
    }

    if (error->status != BLE_HS_EDONE) {
        restart_after_discovery_error(peer, "Descriptor discovery",
                                      error->status);
        return 0;
    }

    if (peer->notify_cccd_handle == 0) {
        restart_after_discovery_error(peer, "NUS notify CCCD not found",
                                      BLE_HS_ENOENT);
        return 0;
    }

    rc = ble_gattc_write_flat(conn_handle, peer->notify_cccd_handle,
                              enable_notifications,
                              sizeof(enable_notifications),
                              subscribe_write_cb, peer);
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
        if (chr != NULL && ble_uuid_cmp(&chr->uuid.u, NUS_NOTIFY_UUID) == 0) {
            peer->notify_value_handle = chr->val_handle;
        }
        return 0;
    }

    if (error->status != BLE_HS_EDONE) {
        restart_after_discovery_error(peer, "Characteristic discovery",
                                      error->status);
        return 0;
    }

    if (peer->notify_value_handle == 0) {
        restart_after_discovery_error(peer,
                                      "NUS notify characteristic not found",
                                      BLE_HS_ENOENT);
        return 0;
    }

    rc = ble_gattc_disc_all_dscs(conn_handle, peer->notify_value_handle,
                                 peer->service_end_handle,
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
        restart_after_discovery_error(peer, "NUS service not found",
                                      BLE_HS_ENOENT);
        return 0;
    }

    rc = ble_gattc_disc_chrs_by_uuid(conn_handle,
                                     peer->service_start_handle,
                                     peer->service_end_handle,
                                     NUS_NOTIFY_UUID,
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
    ESP_LOGI(TAG, "Scanning for Mahjong targets: active=%u configured=%u max=%u",
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
        rc = ble_gattc_disc_svc_by_uuid(peer->conn_handle,
                                        NUS_SERVICE_UUID,
                                        service_discovery_cb, peer);
        if (rc != 0) {
            restart_after_discovery_error(peer,
                                          "Service discovery start", rc);
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
        reset_peer(peer);
        start_scan();
        return 0;

    case BLE_GAP_EVENT_DISC_COMPLETE:
        s_scanning = false;
        start_scan();
        return 0;

    case BLE_GAP_EVENT_NOTIFY_RX:
        peer = find_peer_by_conn_handle(event->notify_rx.conn_handle);
        if (peer != NULL && peer->state == PEER_READY &&
            event->notify_rx.attr_handle == peer->notify_value_handle) {
            consume_notification(peer, event->notify_rx.om);
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

    setvbuf(stdout, NULL, _IONBF, 0);

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

    rc = ble_svc_gap_device_name_set("ESP32S3-Mahjong-Multi-RX");
    if (rc != 0) {
        ESP_LOGE(TAG, "Failed to set local BLE name: rc=%d", rc);
    }

    ble_hs_cfg.reset_cb = ble_on_reset;
    ble_hs_cfg.sync_cb = ble_on_sync;

    ESP_LOGI(TAG,
             "ESP32-S3 multi NUS receiver started: targets=%u max_links=%u",
             (unsigned int)TARGET_COUNT, MAX_MAHJONG_CONNECTIONS);
    nimble_port_freertos_init(nimble_host_task);
}
