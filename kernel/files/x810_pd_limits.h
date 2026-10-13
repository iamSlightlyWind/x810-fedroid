/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef X810_PD_LIMITS_H
#define X810_PD_LIMITS_H

/*
 * Shared X810 board limits for the fixed SM5714 path and SM5440 PPS path.
 * The PPS ceiling is the pump's 11 V VBUS OVP minus 500 mV. The X810 sink
 * APDO and CYG1 manual pass-through policy are both limited to 3 A; do not
 * raise this merely because an adapter or cable can source more current.
 */
#define X810_FIXED_PD_MAX_MV		9000U
#define X810_FIXED_PD_MAX_MA		3000U
#define X810_PPS_MIN_MV			5000U
#define X810_PPS_MAX_MV			10500U
#define X810_PPS_MAX_MA			3000U
#define X810_PPS_DEFAULT_MA		3000U
#define X810_PPS_MIN_MA			1800U
#define X810_PPS_CURRENT_STEP_MA	50U

/* USB-PD PPS current requests use 50 mA units. Always round a user limit
 * down after clamping it to the supported board range.
 */
static inline unsigned int x810_pd_pps_current_limit(unsigned int requested_ma)
{
	unsigned int ma = requested_ma;

	/* This is an upper bound, not a target: never raise a user's lower
	 * limit (including zero) to the pump's minimum operating current.
	 */
	if (ma > X810_PPS_MAX_MA)
		ma = X810_PPS_MAX_MA;

	ma -= ma % X810_PPS_CURRENT_STEP_MA;
	if (ma < X810_PPS_MIN_MA)
		return 0;

	return ma;
}

/* USB_TYPE reports the selected PD contract, not the partner's full source
 * capability list. A fixed 9 V PD contract can have a PPS APDO advertised but
 * still report USB_TYPE_PD. Use fixed-PD state as a passive candidate gate;
 * validate the active APDO after asking TCPM to enter PPS.
 */
static inline int x810_pd_pps_fixed_entry_ready(int online,
						unsigned int mv,
						unsigned int ma,
						unsigned int min_ma)
{
	return online == 1 && mv == X810_FIXED_PD_MAX_MV && ma >= min_ma;
}

/* Intersect the board/user limit with TCPM's negotiated APDO current. Unlike
 * x810_pd_pps_current_limit(), do not round a weak APDO up to the pump minimum.
 */
static inline unsigned int x810_pd_pps_intersect_current(
		unsigned int requested_ma, unsigned int apdo_max_ma)
{
	unsigned int ma = requested_ma < apdo_max_ma ?
			  requested_ma : apdo_max_ma;

	if (ma > X810_PPS_MAX_MA)
		ma = X810_PPS_MAX_MA;
	ma -= ma % X810_PPS_CURRENT_STEP_MA;
	if (ma < X810_PPS_MIN_MA)
		return 0;
	return ma;
}

/*
 * Linux 7.2 TCPM's active PPS VOLTAGE_MIN/MAX report the selected source APDO
 * bounds, not the source/sink intersection. Clamp those source bounds to the
 * X810 board/pump envelope here; a wider source APDO (for example 5..11 V) is
 * valid when it overlaps the board's advertised 5..10.5 V sink APDO.
 */
static inline int x810_pd_pps_intersect_voltage(
		unsigned int source_min_mv, unsigned int source_max_mv,
		unsigned int *min_mv, unsigned int *max_mv)
{
	unsigned int low = source_min_mv > X810_PPS_MIN_MV ?
			    source_min_mv : X810_PPS_MIN_MV;
	unsigned int high = source_max_mv < X810_PPS_MAX_MV ?
			     source_max_mv : X810_PPS_MAX_MV;

	if (source_max_mv < source_min_mv || high < low)
		return 0;

	*min_mv = low;
	*max_mv = high;
	return 1;
}

static inline int x810_pd_contract_is_valid(int direct_charge,
					    unsigned int mv,
					    unsigned int ma)
{
	if (!mv)
		return ma <= X810_FIXED_PD_MAX_MA;
	if (mv < X810_PPS_MIN_MV)
		return 0;

	if (direct_charge)
		return mv <= X810_PPS_MAX_MV && ma <= X810_PPS_MAX_MA;

	return mv <= X810_FIXED_PD_MAX_MV && ma <= X810_FIXED_PD_MAX_MA;
}

#endif /* X810_PD_LIMITS_H */
