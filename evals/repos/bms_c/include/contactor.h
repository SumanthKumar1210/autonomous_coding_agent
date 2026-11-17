/**
 * @file contactor.h
 * @brief Main contactor control.
 */
#ifndef CONTACTOR_H
#define CONTACTOR_H

#include "bms_types.h"

/** @brief Forget the contactor state (assumed open). */
void contactor_init(void);

/**
 * @brief Close the main contactor.
 * @return BMS_OK, BMS_ERR_STATE while any fault is active, or BMS_ERR_HW.
 */
bms_status_t contactor_close(void);

/**
 * @brief Open the main contactor. Always allowed.
 * @return BMS_OK or BMS_ERR_HW.
 */
bms_status_t contactor_open(void);

/** @brief true if the contactor was last commanded closed successfully. */
bool contactor_is_closed(void);

#endif /* CONTACTOR_H */
