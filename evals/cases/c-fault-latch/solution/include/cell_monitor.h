/**
 * @file cell_monitor.h
 * @brief Cell voltage monitoring and over/undervoltage protection.
 */
#ifndef CELL_MONITOR_H
#define CELL_MONITOR_H

#include "bms_types.h"

/** Cell voltage above which FAULT_CELL_OVERVOLTAGE is raised (mV). */
#define CELL_OVERVOLTAGE_MV 4200U
/** Cell voltage below which FAULT_CELL_UNDERVOLTAGE is raised (mV). */
#define CELL_UNDERVOLTAGE_MV 2800U

/** @brief Forget all measurements (min/max read as 0 until the next update). */
void cell_monitor_init(void);

/**
 * @brief Read every cell, update min/max and the voltage faults.
 *
 * Sets FAULT_CELL_OVERVOLTAGE and FAULT_CELL_UNDERVOLTAGE; they latch until
 * cell_monitor_reset_faults(). If a cell cannot be read, FAULT_SENSOR is set
 * and the previous measurements are kept; the next successful read clears it.
 *
 * @return BMS_OK, or BMS_ERR_HW if a cell could not be read.
 */
bms_status_t cell_monitor_update(void);

/**
 * @brief Last measured voltage of one cell.
 * @param cell Cell index, 0 .. BMS_CELL_COUNT - 1.
 * @param[out] mv Voltage in millivolts.
 * @return BMS_OK or BMS_ERR_PARAM.
 */
bms_status_t cell_monitor_cell_mv(uint8_t cell, uint16_t *mv);

/** @brief Lowest cell voltage of the last update (mV). */
uint16_t cell_monitor_min_mv(void);

/** @brief Highest cell voltage of the last update (mV). */
uint16_t cell_monitor_max_mv(void);

/**
 * @brief Clear the latched voltage faults whose condition is gone from the last successful measurement.
 *
 * Does not read the cells again.
 *
 * @return BMS_OK if no voltage fault is active afterwards, BMS_ERR_STATE if one is still present.
 */
bms_status_t cell_monitor_reset_faults(void);

#endif /* CELL_MONITOR_H */
