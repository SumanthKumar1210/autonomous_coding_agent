/**
 * @file fault.h
 * @brief Fault manager: records which protection faults are active.
 */
#ifndef FAULT_H
#define FAULT_H

#include "bms_types.h"

/** Protection faults. FAULT_COUNT must stay last. */
typedef enum {
    FAULT_CELL_OVERVOLTAGE = 0, /**< A cell is above CELL_OVERVOLTAGE_MV. */
    FAULT_CELL_UNDERVOLTAGE,    /**< A cell is below CELL_UNDERVOLTAGE_MV. */
    FAULT_SENSOR,               /**< A measurement could not be read. */
    FAULT_OVERCURRENT,          /**< Pack current above the allowed limit. */
    FAULT_COUNT                 /**< Number of fault codes (not a fault). */
} fault_code_t;

/** @brief Clear every fault. */
void fault_init(void);

/**
 * @brief Mark a fault as active.
 * @return BMS_OK, or BMS_ERR_PARAM for an unknown code.
 */
bms_status_t fault_set(fault_code_t code);

/**
 * @brief Mark a fault as no longer active.
 * @return BMS_OK, or BMS_ERR_PARAM for an unknown code.
 */
bms_status_t fault_clear(fault_code_t code);

/** @brief true if @p code is active (false for an unknown code). */
bool fault_is_active(fault_code_t code);

/** @brief true if any fault is active. */
bool fault_any_active(void);

#endif /* FAULT_H */
