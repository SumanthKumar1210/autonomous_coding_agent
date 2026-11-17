/**
 * @file overcurrent.h
 * @brief Overcurrent protection: debounced, latching FAULT_OVERCURRENT that opens the contactor.
 */
#ifndef OVERCURRENT_H
#define OVERCURRENT_H

#include "bms_types.h"

/** Current magnitude (charge or discharge) above which the debounce runs (mA). */
#define OVERCURRENT_LIMIT_MA 150000
/** Time the magnitude must stay above the limit before the fault is raised (ms). */
#define OVERCURRENT_DEBOUNCE_MS 100U

/** @brief Forget all state (no debounce running, last magnitude 0). */
void overcurrent_init(void);

/**
 * @brief Measure the current and run the protection.
 *
 * Raises (and latches) FAULT_OVERCURRENT and opens the contactor once the
 * magnitude has been above OVERCURRENT_LIMIT_MA for at least
 * OVERCURRENT_DEBOUNCE_MS. A sample at or below the limit restarts the debounce.
 *
 * @return BMS_OK, or BMS_ERR_HW if the current could not be read (the debounce restarts).
 */
bms_status_t overcurrent_update(void);

/**
 * @brief Clear the latched fault if the last measured magnitude is at or below the limit.
 * @return BMS_OK, or BMS_ERR_STATE if the current is still too high (the fault stays).
 */
bms_status_t overcurrent_reset(void);

#endif /* OVERCURRENT_H */
