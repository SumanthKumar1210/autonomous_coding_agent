/**
 * @file pyro_fuse.h
 * @brief Pyrotechnic fuse driver: permanently disconnects the pack (e.g. in a crash).
 */
#ifndef PYRO_FUSE_H
#define PYRO_FUSE_H

#include "bms_types.h"

/** @brief Arm the driver: the fuse counts as not fired. */
void pyro_fuse_init(void);

/**
 * @brief Fire the fuse. It fires at most once.
 *
 * The fuse counts as fired before the HAL is called, so even a failed firing
 * is never retried (a half-fired squib must not be fired again).
 *
 * @return BMS_OK, BMS_ERR_HW if the HAL reported a failure, or BMS_ERR_STATE
 *         if the fuse was already fired (the HAL is not called).
 */
bms_status_t pyro_fuse_fire(void);

/** @brief true once pyro_fuse_fire() has been called since the last pyro_fuse_init(). */
bool pyro_fuse_is_fired(void);

#endif /* PYRO_FUSE_H */
