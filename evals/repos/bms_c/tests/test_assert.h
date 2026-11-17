/**
 * @file test_assert.h
 * @brief Minimal assertion helpers for the unit test programs.
 *
 * Each test program calls CHECK() as often as it likes and returns
 * TEST_RESULT() from main(): exit code 0 if every check passed.
 */
#ifndef TEST_ASSERT_H
#define TEST_ASSERT_H

#include <stdio.h>

static unsigned int test_failures = 0U;

/** Record a failure (with file and line) if @p cond is false; the test continues. */
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);      \
            test_failures++;                                                     \
        }                                                                        \
    } while (0)

/** Print the outcome and return the process exit code. */
#define TEST_RESULT() ((test_failures == 0U) ? (puts("OK"), 0) : (printf("%u check(s) failed\n", test_failures), 1))

#endif /* TEST_ASSERT_H */
