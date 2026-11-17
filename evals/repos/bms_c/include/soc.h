/**
 * @file soc.h
 * @brief State of charge (SOC) estimation by coulomb counting.
 */
#ifndef SOC_H
#define SOC_H

#include "bms_types.h"

/**
 * @brief Start a new estimate.
 * @param capacity_mah Usable pack capacity in mAh (must be > 0).
 * @param initial_pct Initial state of charge, 0 .. 100 %.
 * @return BMS_OK or BMS_ERR_PARAM.
 */
bms_status_t soc_init(uint32_t capacity_mah, uint8_t initial_pct);

/**
 * @brief Integrate the pack current over one time step.
 * @param current_ma Pack current in mA, positive while charging.
 * @param dt_ms Length of the time step in milliseconds.
 */
void soc_update(int32_t current_ma, uint32_t dt_ms);

/** @brief Current state of charge in percent. */
uint8_t soc_get_pct(void);

#endif /* SOC_H */
