/**
 * @file hal_stub.c
 * @brief HAL implementation for unit tests (see hal_stub.h).
 */
#include "hal_stub.h"

#include "hal.h"

static uint16_t s_cell_mv[BMS_CELL_COUNT];
static bool s_cell_fail[BMS_CELL_COUNT];
static int32_t s_current_ma;
static uint32_t s_time_ms;
static bool s_contactor_fail;
static bool s_contactor_closed;
static uint32_t s_contactor_writes;
static bool s_pyro_fail;
static uint32_t s_pyro_fires;

void hal_stub_reset(void)
{
    for (uint8_t i = 0U; i < BMS_CELL_COUNT; i++) {
        s_cell_mv[i] = 3700U;
        s_cell_fail[i] = false;
    }
    s_current_ma = 0;
    s_time_ms = 0U;
    s_contactor_fail = false;
    s_contactor_closed = false;
    s_contactor_writes = 0U;
    s_pyro_fail = false;
    s_pyro_fires = 0U;
}

void hal_stub_set_cell_mv(uint8_t cell, uint16_t mv)
{
    if (cell < BMS_CELL_COUNT) {
        s_cell_mv[cell] = mv;
    }
}

void hal_stub_fail_cell(uint8_t cell, bool fail)
{
    if (cell < BMS_CELL_COUNT) {
        s_cell_fail[cell] = fail;
    }
}

void hal_stub_set_current_ma(int32_t ma)
{
    s_current_ma = ma;
}

void hal_stub_set_time_ms(uint32_t ms)
{
    s_time_ms = ms;
}

void hal_stub_fail_contactor(bool fail)
{
    s_contactor_fail = fail;
}

bool hal_stub_contactor_closed(void)
{
    return s_contactor_closed;
}

uint32_t hal_stub_contactor_writes(void)
{
    return s_contactor_writes;
}

void hal_stub_fail_pyro(bool fail)
{
    s_pyro_fail = fail;
}

uint32_t hal_stub_pyro_fire_count(void)
{
    return s_pyro_fires;
}

bms_status_t hal_read_cell_mv(uint8_t cell, uint16_t *mv)
{
    if ((cell >= BMS_CELL_COUNT) || (mv == NULL)) {
        return BMS_ERR_PARAM;
    }
    if (s_cell_fail[cell]) {
        return BMS_ERR_HW;
    }
    *mv = s_cell_mv[cell];
    return BMS_OK;
}

bms_status_t hal_read_current_ma(int32_t *ma)
{
    if (ma == NULL) {
        return BMS_ERR_PARAM;
    }
    *ma = s_current_ma;
    return BMS_OK;
}

bms_status_t hal_set_contactor(bool closed)
{
    s_contactor_writes++;
    if (s_contactor_fail) {
        return BMS_ERR_HW;
    }
    s_contactor_closed = closed;
    return BMS_OK;
}

uint32_t hal_time_ms(void)
{
    return s_time_ms;
}

bms_status_t hal_fire_pyro_fuse(void)
{
    s_pyro_fires++;
    return s_pyro_fail ? BMS_ERR_HW : BMS_OK;
}
