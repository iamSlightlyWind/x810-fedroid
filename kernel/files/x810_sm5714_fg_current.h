/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef X810_SM5714_FG_CURRENT_H
#define X810_SM5714_FG_CURRENT_H

/*
 * Convert SM5714 fuel-gauge sign-magnitude SRAM current to the Linux
 * power_supply unit (microamps). One count is 1/2044 A. The helper is kept
 * freestanding so its scaling/sign behavior can be exercised by host tests.
 * The caller must pass a successful 16-bit SRAM value, not a read errno.
 */
static inline int x810_sm5714_fg_current_ua(unsigned int raw)
{
	unsigned long long scaled;
	int ua;

	scaled = (unsigned long long)(raw & 0x7fff) * 1000000ULL;
	ua = (int)((scaled + 1022) / 2044);

	return (raw & 0x8000) ? -ua : ua;
}

#endif /* X810_SM5714_FG_CURRENT_H */
