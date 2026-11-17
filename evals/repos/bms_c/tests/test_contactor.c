/**
 * @file test_contactor.c
 * @brief Unit tests for main contactor control.
 */
#include "contactor.h"
#include "fault.h"
#include "hal_stub.h"
#include "test_assert.h"

static void setup(void)
{
    hal_stub_reset();
    fault_init();
    contactor_init();
}

static void test_close_and_open(void)
{
    setup();
    CHECK(contactor_close() == BMS_OK);
    CHECK(contactor_is_closed());
    CHECK(hal_stub_contactor_closed());
    CHECK(contactor_open() == BMS_OK);
    CHECK(!contactor_is_closed());
    CHECK(!hal_stub_contactor_closed());
}

static void test_close_refused_while_faulted(void)
{
    setup();
    CHECK(fault_set(FAULT_OVERCURRENT) == BMS_OK);
    CHECK(contactor_close() == BMS_ERR_STATE);
    CHECK(!contactor_is_closed());
    CHECK(hal_stub_contactor_writes() == 0U);
}

static void test_hardware_failure(void)
{
    setup();
    hal_stub_fail_contactor(true);
    CHECK(contactor_close() == BMS_ERR_HW);
    CHECK(!contactor_is_closed());
}

int main(void)
{
    test_close_and_open();
    test_close_refused_while_faulted();
    test_hardware_failure();
    return TEST_RESULT();
}
