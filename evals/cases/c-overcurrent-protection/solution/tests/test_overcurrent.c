/**
 * @file test_overcurrent.c
 * @brief Unit tests for overcurrent protection.
 */
#include "contactor.h"
#include "fault.h"
#include "hal_stub.h"
#include "overcurrent.h"
#include "test_assert.h"

static void setup(void)
{
    hal_stub_reset();
    fault_init();
    contactor_init();
    overcurrent_init();
    CHECK(contactor_close() == BMS_OK);
}

static void sample(int32_t ma, uint32_t now_ms)
{
    hal_stub_set_current_ma(ma);
    hal_stub_set_time_ms(now_ms);
    CHECK(overcurrent_update() == BMS_OK);
}

static void test_debounced_trip(void)
{
    setup();
    sample(160000, 0U);
    sample(160000, 99U);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
    sample(160000, 100U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));
    CHECK(!contactor_is_closed());
}

static void test_short_spike_is_ignored(void)
{
    setup();
    sample(-160000, 0U);
    sample(0, 50U);
    sample(-160000, 120U);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
    CHECK(contactor_is_closed());
}

static void test_latch_and_reset(void)
{
    setup();
    sample(INT32_MIN, 0U);
    sample(INT32_MIN, 100U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));
    CHECK(overcurrent_reset() == BMS_ERR_STATE);
    sample(0, 150U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));
    CHECK(overcurrent_reset() == BMS_OK);
    CHECK(!fault_is_active(FAULT_OVERCURRENT));
}

static void test_time_wrap(void)
{
    setup();
    sample(160000, 0xFFFFFFF0U);
    sample(160000, 84U);
    CHECK(fault_is_active(FAULT_OVERCURRENT));
}

int main(void)
{
    test_debounced_trip();
    test_short_spike_is_ignored();
    test_latch_and_reset();
    test_time_wrap();
    return TEST_RESULT();
}
