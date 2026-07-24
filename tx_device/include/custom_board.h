/* Copyright (c) 2014 Nordic Semiconductor. All Rights Reserved.
 *
 * The information contained herein is property of Nordic Semiconductor ASA.
 * Terms and conditions of usage are described in detail in NORDIC
 * SEMICONDUCTOR STANDARD SOFTWARE LICENSE AGREEMENT.
 *
 * Licensees are granted free, non-transferable use of the information. NO
 * WARRANTY of ANY KIND is provided. This heading must NOT be removed from
 * the file.
 *
 */
#ifndef CUSTOM_BOARD_H
#define CUSTOM_BOARD_H

#ifdef __cplusplus
extern "C" {
#endif

#include "nrf_gpio.h"

#define SPI_INSTANCE            0
#define SPI_SCK_PIN             7
#define SPI_MISO_PIN            12
#define SPI_MOSI_PIN            6
#define SPI_SS_PIN              8
#define TRAJ_INT_PIN            13
#define TRAJ_EN_PIN             5

#define LED_OTA_PIN             18
#define LED_CONFIG_PIN          19

#define DEVICE_INFO_RX_PIN_NUMBER  8
#define DEVICE_INFO_TX_PIN_NUMBER  6
#define CTS_PIN_NUMBER 7
#define RTS_PIN_NUMBER 5
#define HWFC           false

// Low frequency clock source to be used by the SoftDevice
#define NRF_CLOCK_LFCLKSRC      {.source        = NRF_CLOCK_LF_SRC_RC,               \
                                 .rc_ctiv       = 10,                                \
                                 .rc_temp_ctiv  = 10,                                \
                                 .xtal_accuracy = NRF_CLOCK_LF_XTAL_ACCURACY_20_PPM}

#ifdef __cplusplus
}
#endif

#endif // CUSTOM_BOARD_H
