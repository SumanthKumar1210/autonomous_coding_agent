/* Hidden acceptance test for eval case c-cell-imbalance (never shown to the agent). */
#include "cell_monitor.h"
#include "fault.h"
#include "hal_stub.h"
#include "test_assert.h"

static void setup(void)
{
    hal_stub_reset();
    fault_init();
    cell_monitor_init();
}

static void test_threshold_is_strict(void)
{
    setup();
    CHECK(CELL_IMBALANCE_MV == 100U);
    CHECK(cell_monitor_imbalance_mv() == 0U);
    hal_stub_set_cell_mv(0U, 3700U);
    hal_stub_set_cell_mv(1U, 3800U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_imbalance_mv() == 100U);
    CHECK(!fault_is_active(FAULT_CELL_IMBALANCE));

    hal_stub_set_cell_mv(1U, 3801U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_imbalance_mv() == 101U);
    CHECK(fault_is_active(FAULT_CELL_IMBALANCE));
}

static void test_failed_read_keeps_fault(void)
{
    setup();
    hal_stub_set_cell_mv(3U, 3900U);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(fault_is_active(FAULT_CELL_IMBALANCE));

    hal_stub_set_cell_mv(3U, 3700U);
    hal_stub_fail_cell(2U, true);
    CHECK(cell_monitor_update() == BMS_ERR_HW);
    CHECK(fault_is_active(FAULT_CELL_IMBALANCE));
    CHECK(cell_monitor_imbalance_mv() == 200U);

    hal_stub_fail_cell(2U, false);
    CHECK(cell_monitor_update() == BMS_OK);
    CHECK(cell_monitor_imbalance_mv() == 0U);
    CHECK(!fault_is_active(FAULT_CELL_IMBALANCE));
}

static void test_fault_code_is_distinct(void)
{
    setup();
    CHECK(FAULT_CELL_IMBALANCE < FAULT_COUNT);
    CHECK(FAULT_CELL_IMBALANCE != FAULT_CELL_OVERVOLTAGE);
    CHECK(FAULT_CELL_IMBALANCE != FAULT_SENSOR);
    CHECK(fault_set(FAULT_CELL_IMBALANCE) == BMS_OK);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
}

int main(void)
{
    test_threshold_is_strict();
    test_failed_read_keeps_fault();
    test_fault_code_is_distinct();
    return TEST_RESULT();
}
