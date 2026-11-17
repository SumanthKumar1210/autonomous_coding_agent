/**
 * @file cell_monitor.c
 * @brief Cell voltage monitoring implementation.
 */
#include "cell_monitor.h"

#include "fault.h"
#include "hal.h"

static uint16_t s_cell_mv[BMS_CELL_COUNT];
static uint16_t s_min_mv;
static uint16_t s_max_mv;

void cell_monitor_init(void)
{
    for (uint8_t i = 0U; i < BMS_CELL_COUNT; i++) {
        s_cell_mv[i] = 0U;
    }
    s_min_mv = 0U;
    s_max_mv = 0U;
}

bms_status_t cell_monitor_update(void)
{
    uint16_t readings[BMS_CELL_COUNT];

    for (uint8_t i = 0U; i < BMS_CELL_COUNT; i++) {
        if (hal_read_cell_mv(i, &readings[i]) != BMS_OK) {
            (void)fault_set(FAULT_SENSOR);
            return BMS_ERR_HW;
        }
    }
    (void)fault_clear(FAULT_SENSOR);

    uint16_t min_mv = readings[0];
    uint16_t max_mv = readings[0];
    for (uint8_t i = 0U; i < BMS_CELL_COUNT; i++) {
        s_cell_mv[i] = readings[i];
        if (readings[i] < min_mv) {
            min_mv = readings[i];
        }
        if (readings[i] > max_mv) {
            max_mv = readings[i];
        }
    }
    s_min_mv = min_mv;
    s_max_mv = max_mv;

    if (max_mv > CELL_OVERVOLTAGE_MV) {
        (void)fault_set(FAULT_CELL_OVERVOLTAGE);
    } else {
        (void)fault_clear(FAULT_CELL_OVERVOLTAGE);
    }
    if (min_mv < CELL_UNDERVOLTAGE_MV) {
        (void)fault_set(FAULT_CELL_UNDERVOLTAGE);
    } else {
        (void)fault_clear(FAULT_CELL_UNDERVOLTAGE);
    }
    return BMS_OK;
}

bms_status_t cell_monitor_cell_mv(uint8_t cell, uint16_t *mv)
{
    if ((cell >= BMS_CELL_COUNT) || (mv == NULL)) {
        return BMS_ERR_PARAM;
    }
    *mv = s_cell_mv[cell];
    return BMS_OK;
}

uint16_t cell_monitor_min_mv(void)
{
    return s_min_mv;
}

uint16_t cell_monitor_max_mv(void)
{
    return s_max_mv;
}
