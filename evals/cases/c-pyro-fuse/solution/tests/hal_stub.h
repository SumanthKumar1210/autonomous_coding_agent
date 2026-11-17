/**
 * @file hal_stub.h
 * @brief Test controls for the HAL stub (tests/hal_stub.c).
 *
 * The stub implements hal.h for unit tests: measurements return the values
 * set here, and actuator commands are recorded so tests can check them.
 */
#ifndef HAL_STUB_H
#define HAL_STUB_H

#include "bms_types.h"

/** @brief Reset every stub value: cells 3700 mV, current 0 mA, time 0, no failures, nothing recorded. */
void hal_stub_reset(void);

/** @brief Value hal_read_cell_mv() returns for @p cell. */
void hal_stub_set_cell_mv(uint8_t cell, uint16_t mv);

/** @brief Make hal_read_cell_mv() fail (BMS_ERR_HW) for @p cell. */
void hal_stub_fail_cell(uint8_t cell, bool fail);

/** @brief Value hal_read_current_ma() returns. */
void hal_stub_set_current_ma(int32_t ma);

/** @brief Value hal_time_ms() returns. */
void hal_stub_set_time_ms(uint32_t ms);

/** @brief Make hal_set_contactor() fail (BMS_ERR_HW). */
void hal_stub_fail_contactor(bool fail);

/** @brief Last state successfully commanded with hal_set_contactor(). */
bool hal_stub_contactor_closed(void);

/** @brief Number of hal_set_contactor() calls since the last reset. */
uint32_t hal_stub_contactor_writes(void);

/** @brief Make hal_fire_pyro_fuse() fail (BMS_ERR_HW). */
void hal_stub_fail_pyro(bool fail);

/** @brief Number of hal_fire_pyro_fuse() calls since the last reset. */
uint32_t hal_stub_pyro_fire_count(void);

#endif /* HAL_STUB_H */
