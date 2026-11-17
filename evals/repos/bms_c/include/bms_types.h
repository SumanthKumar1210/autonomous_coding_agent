/**
 * @file bms_types.h
 * @brief Types and constants shared by every BMS module.
 */
#ifndef BMS_TYPES_H
#define BMS_TYPES_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/** Number of series cells in the pack. */
#define BMS_CELL_COUNT 4U

/** Result of every BMS function that can fail. */
typedef enum {
    BMS_OK = 0,    /**< Success. */
    BMS_ERR_PARAM, /**< Invalid argument (NULL pointer, out-of-range value). */
    BMS_ERR_HW,    /**< The hardware abstraction layer reported a failure. */
    BMS_ERR_STATE  /**< The operation is not allowed in the current state. */
} bms_status_t;

#endif /* BMS_TYPES_H */
