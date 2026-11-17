/**
 * @file test_pyro_fuse.c
 * @brief Unit tests for the pyro fuse driver.
 */
#include "hal_stub.h"
#include "pyro_fuse.h"
#include "test_assert.h"

static void setup(void)
{
    hal_stub_reset();
    pyro_fuse_init();
}

static void test_fires_once(void)
{
    setup();
    CHECK(!pyro_fuse_is_fired());
    CHECK(pyro_fuse_fire() == BMS_OK);
    CHECK(pyro_fuse_is_fired());
    CHECK(pyro_fuse_fire() == BMS_ERR_STATE);
    CHECK(hal_stub_pyro_fire_count() == 1U);
}

static void test_failure_is_not_retried(void)
{
    setup();
    hal_stub_fail_pyro(true);
    CHECK(pyro_fuse_fire() == BMS_ERR_HW);
    CHECK(pyro_fuse_is_fired());
    hal_stub_fail_pyro(false);
    CHECK(pyro_fuse_fire() == BMS_ERR_STATE);
    CHECK(hal_stub_pyro_fire_count() == 1U);
}

int main(void)
{
    test_fires_once();
    test_failure_is_not_retried();
    return TEST_RESULT();
}
