/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef X810_PD_LIMITS_H
#define X810_PD_LIMITS_H

/*
 * Shared X810 board limits for the fixed SM5714 path and SM5440 PPS path.
 * The PPS ceiling is the pump's 11 V VBUS OVP minus 500 mV; its current cap
 * is 5 A only when the adapter's advertised APDO grants it.  The shipped
 * module default remains 3 A.
 */
#define X810_FIXED_PD_MAX_MV		9000U
#define X810_FIXED_PD_MAX_MA		3000U
#define X810_PPS_MAX_MV			10500U
#define X810_PPS_MAX_MA			5000U
#define X810_PPS_DEFAULT_MA		3000U
#define X810_PPS_MIN_MA			1800U
#define X810_PPS_CURRENT_STEP_MA	50U

/* USB-PD PPS current requests use 50 mA units. Always round a user limit
 * down after clamping it to the supported board range.
 */
static inline unsigned int x810_pd_pps_current_limit(unsigned int requested_ma)
{
	unsigned int ma = requested_ma;

	if (ma < X810_PPS_MIN_MA)
		ma = X810_PPS_MIN_MA;
	if (ma > X810_PPS_MAX_MA)
		ma = X810_PPS_MAX_MA;

	return ma - ma % X810_PPS_CURRENT_STEP_MA;
}

static inline int x810_pd_contract_is_valid(int direct_charge,
					    unsigned int mv,
					    unsigned int ma)
{
	if (!mv)
		return ma <= X810_FIXED_PD_MAX_MA;
	if (mv < 5000U)
		return 0;

	if (direct_charge)
		return mv <= X810_PPS_MAX_MV && ma <= X810_PPS_MAX_MA;

	return mv <= X810_FIXED_PD_MAX_MV && ma <= X810_FIXED_PD_MAX_MA;
}

#endif /* X810_PD_LIMITS_H */
