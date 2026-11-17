/**
 * @file hal.h
 * @brief Hardware abstraction layer: the only way BMS modules touch hardware.
 *
 * The target build links the real driver implementation; unit tests link
 * tests/hal_stub.c instead.
 */
#ifndef HAL_H
#define HAL_H

#include "bms_types.h"

/**
 * @brief Read one cell voltage.
 * @param cell Cell index, 0 .. BMS_CELL_COUNT - 1.
 * @param[out] mv Cell voltage in millivolts.
 * @return BMS_OK, BMS_ERR_PARAM for a bad index or NULL, BMS_ERR_HW if the measurement failed.
 */
bms_status_t hal_read_cell_mv(uint8_t cell, uint16_t *mv);

/**
 * @brief Read the pack current.
 * @param[out] ma Current in milliamps, positive while charging.
 * @return BMS_OK, BMS_ERR_PARAM for NULL, BMS_ERR_HW if the measurement failed.
 */
bms_status_t hal_read_current_ma(int32_t *ma);

/**
 * @brief Drive the main contactor coil.
 * @param closed true closes the contactor, false opens it.
 * @return BMS_OK or BMS_ERR_HW.
 */
bms_status_t hal_set_contactor(bool closed);

/** @brief Milliseconds since start-up (wraps after ~49 days). */
uint32_t hal_time_ms(void);

#endif /* HAL_H */
