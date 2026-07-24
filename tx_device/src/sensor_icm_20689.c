#include "sensor_icm_20689.h"
#include "custom_board.h"
#include "nrf_delay.h"
#include "nrf_drv_spi.h"

#define NRF_LOG_MODULE_NAME "icm_20689"
#include "nrf_log.h"
#include "nrf_log_ctrl.h"

// clang-format off
#define MIN_FREQ_RATE_HZ  3.91
#define MAX_FREQ_RATE_HZ  500
#define LAST_FREQ_RATE_HZ 500

#define ACCE_MIN_BW_HZ    5.1
#define ACCE_MAX_BW_HZ    420
#define ACCE_LAST_BW_HZ   420

#define GYRO_MIN_BW_HZ    5
#define GYRO_MAX_BW_HZ    3281
#define GYRO_LAST_BW_HZ   3281

static const nrf_drv_spi_t spi = NRF_DRV_SPI_INSTANCE(SPI_INSTANCE);
static volatile bool spi_xfer_done;

static icm_20689_dev_data_t dev_data = {.x_map = 0, .y_map = 1, .z_map = 2, .x_negate = 0, .y_negate = 0, .z_negate = 0};

static const struct
{
    float   freq;
    uint8_t div;
} odr_table[] = {
    { 3.91, 255 }, 
    { 7.81, 127 }, 
    { 15.63, 63 }, 
    { 31.25, 31 },
    { 62.5, 15 },  
    { 100, 9 },
    { 125, 7 },    
    { 250, 3 },    
    { 500, 1 },
};

static const struct
{
    float   bw;
    uint8_t cfg;
} acce_bw_table[] = { 
    { 5.1, 6 }, 
    { 10.2, 5 },  
    { 21.2, 4 }, 
    { 44.8, 3 },
    { 99, 2 },  
    { 218.1, 1 }, 
    { 420, 7 } 
};

static const struct
{
    float   bw;
    uint8_t cfg;
} gyro_bw_table[] = {
    { 5, 6 },  
    { 10, 5 },  
    { 20, 4 },  
    { 41, 3 },
    { 92, 2 }, 
    { 176, 1 }, 
    { 250, 0 }, 
    { 3281, 7 } 
};
// clang-format on

/**
 * @brief SPI user event handler.
 * @param event
 */
void spi_event_handler(nrf_drv_spi_evt_t const* p_event)
{
    spi_xfer_done = true;
}

static uint8_t write_reg(uint8_t reg, uint8_t value)
{
    uint8_t send_buf[2];
    uint8_t recv_buf[2];
    int32_t timeout = 0x0fffffff;

    send_buf[0] = reg;
    send_buf[1] = value;

    spi_xfer_done = false;

    APP_ERROR_CHECK(nrf_drv_spi_transfer(
      &spi, (const uint8_t*)send_buf, 2, (uint8_t*)recv_buf, 2));

    while ((!spi_xfer_done) && (timeout > 0)) {
        timeout--;
        __WFE();
    }

    return 0;
}

static uint8_t read_reg(uint8_t reg)
{
    int32_t reg_val;
    uint8_t send_buf[2];
    uint8_t recv_buf[2];
    int32_t timeout = 0x0fffffff;

    send_buf[0] = 0x80 | reg;

    spi_xfer_done = false;

    APP_ERROR_CHECK(nrf_drv_spi_transfer(
      &spi, (const uint8_t*)send_buf, 2, (uint8_t*)recv_buf, 2));

    while ((!spi_xfer_done) && (timeout > 0)) {
        timeout--;
        __WFE();
    }

    reg_val = recv_buf[1];
    return reg_val;
}

static void read_int_data(uint16_t* int_data)
{
    int32_t reg_val;
    uint8_t send_buf[15];
    uint8_t read_buf[15];
    int32_t timeout = 0x0fffffff;

    send_buf[0] = 0x80 | ACCEL_XOUT_H;

    spi_xfer_done = false;

    APP_ERROR_CHECK(nrf_drv_spi_transfer(
      &spi, (const uint8_t*)send_buf, 15, (uint8_t*)read_buf, 15));

    while ((!spi_xfer_done) && (timeout > 0)) {
        timeout--;
        __WFE();
    }

    int_data[0] = (read_buf[1] << 8) | read_buf[2];
    int_data[1] = (read_buf[3] << 8) | read_buf[4];
    int_data[2] = (read_buf[5] << 8) | read_buf[6];
    int_data[3] = (read_buf[9] << 8) | read_buf[10];
    int_data[4] = (read_buf[11] << 8) | read_buf[12];
    int_data[5] = (read_buf[13] << 8) | read_buf[14];
}

static int set_reg_bit_pattern(uint8_t reg, uint8_t bit_pattern, uint8_t mask)
{
    uint8_t reg_val;

    reg_val = read_reg(reg);

    if (reg_val < 0) {
        NRF_LOG_INFO("read reg error\r\n");
        return reg_val;
    }

    reg_val &= ~mask;
    reg_val |= bit_pattern;

    reg_val = write_reg(reg, reg_val);

    if (reg_val < 0) {
        NRF_LOG_INFO("write reg error\r\n");
        return reg_val;
    }

    return 0;
}

static uint8_t icm_20689_acce_gyro_freq_set(uint32_t freq)
{
    int     ret = 0;
    uint8_t smplrt_div_bits;
    uint8_t acce_bw_bits;
    uint8_t gyro_bw_bits;

    if (freq < MIN_FREQ_RATE_HZ) {
        freq = MIN_FREQ_RATE_HZ;
    } else if (freq > MAX_FREQ_RATE_HZ) {
        freq = MAX_FREQ_RATE_HZ;
    }

    /* map delay to odr */
    for (int i = 0; i < sizeof(odr_table) / sizeof(odr_table[0]); i++) {
        if (freq <= odr_table[i].freq ||
            odr_table[i].freq == LAST_FREQ_RATE_HZ) {
            dev_data.sample_freq = odr_table[i].freq;
            smplrt_div_bits = odr_table[i].div;
            ret = 0;
            break;
        }
    }

    /* set acce bw */
    for (int i = 0; i < sizeof(acce_bw_table) / sizeof(acce_bw_table[0]); i++) {
        if ((dev_data.sample_freq / 5) <= acce_bw_table[i].bw ||
            acce_bw_table[i].bw == ACCE_LAST_BW_HZ) {
            dev_data.acce_bw = acce_bw_table[i].bw;
            acce_bw_bits = acce_bw_table[i].cfg;
            ret = 0;
            break;
        }
    }

    /* set gyro bw */
    for (int i = 0; i < sizeof(gyro_bw_table) / sizeof(gyro_bw_table[0]); i++) {
        if ((dev_data.sample_freq / 5) <= gyro_bw_table[i].bw ||
            gyro_bw_table[i].bw == GYRO_LAST_BW_HZ) {
            dev_data.gyro_bw = gyro_bw_table[i].bw;
            gyro_bw_bits = gyro_bw_table[i].cfg;
            ret = 0;
            break;
        }
    }

    // set acce bw
    NRF_LOG_INFO("set gyro bw: %d\r\n", gyro_bw_bits);
    ret += set_reg_bit_pattern(ICM_CONFIG, gyro_bw_bits << 0, 0x07);
    // enable gyro bw
    ret += set_reg_bit_pattern(GYRO_CONFIG, 0x00 << 0, 0x03);
    // enable acce bw and set acce bw
    ret += set_reg_bit_pattern(ACCEL_CONFIG_2, acce_bw_bits << 0, 0x0F);
    // set acce and gyro freq
    ret += set_reg_bit_pattern(SMPLRT_DIV, smplrt_div_bits << 0, 0xff);

    NRF_LOG_INFO("div: %d, set freq = %uHz actual freq = %.1f acce bw = %.1f "
                 "gyro bw "
                 "= %.1f\r\n",
                 smplrt_div_bits,
                 freq,
                 dev_data.sample_freq,
                 dev_data.acce_bw,
                 dev_data.gyro_bw);

    return ret;
}

static uint8_t icm_20689_acce_range_set(uint32_t range)
{
    int     ret = 0;
    uint8_t range_bits;

    if (range == 2) {
        range_bits = MPU_FS_2G;
    } else if (range == 4) {
        range_bits = MPU_FS_4G;
    } else if (range == 8) {
        range_bits = MPU_FS_8G;
    } else if (range == 16) {
        range_bits = MPU_FS_16G;
    } else {
        return -1;
    }

    ret = set_reg_bit_pattern(ACCEL_CONFIG, range_bits << 3, 0x18);

    NRF_LOG_INFO(
      "set acce range: %d, reg: 0x%0x\r\n", range, read_reg(ACCEL_CONFIG));

    return ret;
}

static uint8_t icm_20689_gyro_range_set(uint32_t range)
{
    int     ret = 0;
    uint8_t range_bits;

    if (range == 250) {
        range_bits = MPU_FS_250dps;
    } else if (range == 500) {
        range_bits = MPU_FS_500dps;
    } else if (range == 1000) {
        range_bits = MPU_FS_1000dps;
    } else if (range == 2000) {
        range_bits = MPU_FS_2000dps;
    } else {
        return -1;
    }

    ret = set_reg_bit_pattern(GYRO_CONFIG, range_bits << 3, 0x18);

    NRF_LOG_INFO(
      "set gyro range: %d, reg: 0x%0x\r\n", range, read_reg(GYRO_CONFIG));

    return ret;
}

int icm_20689_wakeup_enable()
{
    int     ret = 0;
    uint8_t bit;

    // ensure acce is running
    // bit = 0x18;
    // ret += set_reg_bit_pattern(PWR_MGMT_1, bit, 0x78);
    // bit = 0x00;
    // ret += set_reg_bit_pattern(PWR_MGMT_2, bit, 0x0f);

    // ICM_CONFIG acce
    bit = 0x01;
    ret += set_reg_bit_pattern(ACCEL_CONFIG_2, bit, 0x0f);

    // en motion int
    bit = 0xe0;
    ret += set_reg_bit_pattern(INT_ENABLE, bit, 0xe0);

    // set x y z wakeup up threshold: 1/256 g
    bit = 10;
    ret += set_reg_bit_pattern(ACCEL_WOM_X_THR, bit, 0xff);
    ret += set_reg_bit_pattern(ACCEL_WOM_Y_THR, bit, 0xff);
    ret += set_reg_bit_pattern(ACCEL_WOM_Z_THR, bit, 0xff);

    // en acc intelligence
    bit = 0xc0;
    ret += set_reg_bit_pattern(ACCEL_INTEL_CTRL, bit, 0xff);

    // set sample rate 7.81Hz
    bit = 127;
    ret += set_reg_bit_pattern(SMPLRT_DIV, bit, 0xff);

    // en acc cycle mode
    bit = 0x30;
    ret += set_reg_bit_pattern(PWR_MGMT_1, bit, 0x20);

    // en rising edge, push-pull, read int status clear
    // bit = 0x00;
    // ret += set_reg_bit_pattern(INT_PIN_CFG, bit, 0xfc);

    return ret;
}

int icm_20689_int_enable()
{
    int     ret = 0;
    uint8_t bit;

    // ensure acce is running
    // bit = 0x01;
    // ret += set_reg_bit_pattern(PWR_MGMT_1, bit, 0xff);
    // bit = 0x00;
    // ret += set_reg_bit_pattern(PWR_MGMT_2, bit, 0xff);

    // en data ready int, rising edge, push-pull, read int status clear
    bit = 0x00;
    ret += set_reg_bit_pattern(INT_PIN_CFG, bit, 0xfc);

    // en int
    bit = 0x01;
    ret += set_reg_bit_pattern(INT_ENABLE, bit, 0x01);

    return ret;
}

static void icm_20689_convert_raw_data(int16_t* xyz, int16_t* raw_xyz)
{
    xyz[0] = ((dev_data.x_negate) ? (-raw_xyz[dev_data.x_map])
                                  : (raw_xyz[dev_data.x_map]));
    xyz[1] = ((dev_data.y_negate) ? (-raw_xyz[dev_data.y_map])
                                  : (raw_xyz[dev_data.y_map]));
    xyz[2] = ((dev_data.z_negate) ? (-raw_xyz[dev_data.z_map])
                                  : (raw_xyz[dev_data.z_map]));
    xyz[3] = ((dev_data.x_negate) ? (-raw_xyz[3 + dev_data.x_map])
                                  : (raw_xyz[3 + dev_data.x_map]));
    xyz[4] = ((dev_data.y_negate) ? (-raw_xyz[3 + dev_data.y_map])
                                  : (raw_xyz[3 + dev_data.y_map]));
    xyz[5] = ((dev_data.z_negate) ? (-raw_xyz[3 + dev_data.z_map])
                                  : (raw_xyz[3 + dev_data.z_map]));
}

static void icm_20689_calibrate_xyz(int16_t* xyz)
{
    xyz[0] -= dev_data.accel_cali[0];
    xyz[1] -= dev_data.accel_cali[1];
    xyz[2] -= dev_data.accel_cali[2];
    xyz[3] -= dev_data.gyro_cali[0];
    xyz[4] -= dev_data.gyro_cali[1];
    xyz[5] -= dev_data.gyro_cali[2];
}

int icm_20689_int_read_data(size_t size, int16_t* xyz)
{
    uint8_t bit;
    uint8_t reg;
    int16_t read_buf[6];

    // read int status
    // reg = read_reg(INT_STATUS);
    // if (!(reg & 0x01)) {
    //    NRF_LOG_INFO("data not ready, int status: 0x%0x\r\n", reg);
    //    return 0;
    //}
    // NRF_LOG_INFO("data ready, int status: 0x%0x\r\n", reg);

    read_int_data(read_buf);

    // convert fifo
    icm_20689_convert_raw_data((int16_t*)(xyz), (int16_t*)(read_buf));
    icm_20689_calibrate_xyz((int16_t*)(xyz));

    return 12;
}

uint8_t icm_20689_set_mode(uint8_t mode)
{
    int ret = 0;

    if (mode == ICM_20689_WORK_MODE_POLLING) {
        NRF_LOG_INFO("set mode to POLLING\r\n");
    } else if (mode == ICM_20689_WORK_MODE_INT) {
        if (icm_20689_int_enable() < 0) {
            ret = -1;
        }
        NRF_LOG_INFO("set mode to INT\r\n");
    } else if (mode == ICM_20689_WORK_MODE_WAKEUP) {
        if (icm_20689_wakeup_enable() < 0) {
            ret = -1;
        }
        NRF_LOG_INFO("set mode to WAKEUP\r\n");
    } else {
        NRF_LOG_INFO("Unsupported mode, code is %d\r\n", mode);
        ret = -1;
    }

    return ret;
}

static uint8_t icm_20689_set_st(uint8_t polarity)
{
    int     ret = 0;
    uint8_t bit;

    // open gyro st
    bit = 0xd0;
    ret = set_reg_bit_pattern(GYRO_CONFIG, bit, 0xd0);

    // open acce st
    bit = 0xd0;
    ret += set_reg_bit_pattern(ACCEL_CONFIG, bit, 0xd0);

    return ret;
}

static uint8_t icm_20689_sensor_init_config()
{
    int ret = 0;

    ret = icm_20689_gyro_range_set(dev_data.gyro_range);
    if (ret < 0)
        return ret;

    ret = icm_20689_acce_range_set(dev_data.acce_range);
    if (ret < 0)
        return ret;

    ret = icm_20689_acce_gyro_freq_set(dev_data.sample_freq);
    if (ret < 0)
        return ret;

    return 0;
}

uint8_t icm_20689_soft_reset()
{
    uint8_t reg;
    uint8_t bit;
    int     ret = 0;

    // device reset
    bit = 0x80;
    ret = set_reg_bit_pattern(PWR_MGMT_1, bit, 0x80);
    if (ret < 0) {
        return ret;
    }

    // read who am i
    for (int i = 0; i < 10; i++) {
        reg = read_reg(WHO_AM_I);
        if (reg == ICM_20689_ID) {
            return 0;
        } else {
            nrf_delay_ms(10);
        }
    }

    return -1;
}

int icm_20689_init()
{
    int     ret = 0;
    uint8_t bit;

    nrf_drv_spi_config_t spi_config = NRF_DRV_SPI_DEFAULT_CONFIG;
    spi_config.ss_pin = SPI_SS_PIN;
    spi_config.miso_pin = SPI_MISO_PIN;
    spi_config.mosi_pin = SPI_MOSI_PIN;
    spi_config.sck_pin = SPI_SCK_PIN;
    APP_ERROR_CHECK(nrf_drv_spi_init(&spi, &spi_config, spi_event_handler));

    NRF_LOG_INFO("start icm_20689 init\r\n");

    ret = icm_20689_soft_reset();
    if (ret < 0) {
        NRF_LOG_ERROR("soft reset error\r\n");
        return ret;
    }

    dev_data.sample_freq = 100;

    dev_data.acce_range = 4;

    dev_data.gyro_range = 500;

    // ensure acce is running
    bit = 0x01;
    ret += set_reg_bit_pattern(PWR_MGMT_1, bit, 0xff);

    bit = 0x03;
    ret += set_reg_bit_pattern(SIGNAL_PATH_RESET, bit, 0x03);

    bit = 0x11;
    ret += set_reg_bit_pattern(USER_CTRL, bit, 0x11);

    bit = 0x40;
    ret += set_reg_bit_pattern(PWR_MGMT_2, bit, 0xff);

    /* set init values to sensor */
    ret += icm_20689_sensor_init_config();

    NRF_LOG_INFO("icm_20689 init done\r\n");

    return ret;
}

int icm_20689_deinit()
{
    int ret = 0;

    NRF_LOG_INFO("start icm_20689 uninit\r\n");

    ret = icm_20689_soft_reset();
    if (ret < 0) {
        NRF_LOG_INFO("soft reset error\r\n");
        return ret;
    }

    nrf_drv_spi_uninit(&spi);

    return ret;
}
