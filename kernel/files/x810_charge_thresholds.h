/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef X810_CHARGE_THRESHOLDS_H
#define X810_CHARGE_THRESHOLDS_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdbool.h>
#endif

/*
 * Linux power-supply start/end threshold semantics.  An end threshold of 100
 * means no limit; otherwise stop at end and resume at start.  Negative
 * capacity means the gauge sample failed, so retain the previous state.
 */
static inline bool x810_charge_thresholds_valid(int start, int end)
{
	return start >= 0 && start < end && end <= 100;
}

/* Samsung's normal store-mode policy uses a 10-point restart hysteresis. */
static inline int x810_charge_threshold_default_start(int end)
{
	return end >= 100 ? 0 : end > 10 ? end - 10 : 0;
}

static inline bool x810_charge_thresholds_update(bool stopped, int start,
						 int end, int capacity)
{
	if (capacity < 0 || end >= 100)
		return end >= 100 ? false : stopped;

	if (stopped)
		return capacity > start;

	return capacity >= end;
}

#endif /* X810_CHARGE_THRESHOLDS_H */
