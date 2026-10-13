/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef X810_SM5440_BYPASS_POLICY_H
#define X810_SM5440_BYPASS_POLICY_H

/*
 * Pure policy extracted from Samsung's SM5440 CV_MAN pass-through path.
 * Inputs use 0.1%-SOC and microamp units as in Samsung's fuel-gauge interface.
 * The Linux port currently exposes integer SOC, so the driver feeds percent *
 * 10; classification is consequently coarser than Samsung's implementation.
 */
#define X810_SM5440_PT_MIN_SOC_TENTHS	200
#define X810_SM5440_PT_MAX_SOC_TENTHS	900
#define X810_SM5440_PT_START_HEADROOM_MV	80
/* Mirror Samsung pd_cv_man_work(): never decrement below 2*ADC_VBAT +
 * 2*PPS_V_STEP. Round the floor upward to an actual 20 mV PPS point.
 */
#define X810_SM5440_PT_MIN_HEADROOM_MV	40
/* Use no positive-current deadband: after confirmation any continuing charge
 * sample lowers PPS. Small negative current is allowed as discharge tolerance.
 * The FG's raw step is ~0.49 mA/count; sensor offset and transient samples
 * still prevent this control loop from guaranteeing zero net charge.
 */
#define X810_SM5440_PT_DISCHARGE_TOLERANCE_UA	100000
#define X810_SM5440_PT_VOLTAGE_STEP_MV	20
#define X810_SM5440_PT_CURRENT_CONFIRM_SAMPLES	3
#define X810_SM5440_PT_MAX_PPS_MV		10500
#define X810_SM5440_PT_FREQUENCY_KHZ	550
#define X810_SM5440_IBUS_MARGIN_MA		300
#define X810_SM5440_PT_VBAT_TARGET_OFFSET_MV	200
#define X810_SM5440_PT_CV_OFFSET_MV	50
#define X810_SM5440_VBATREG_BASE_MV		3800
#define X810_SM5440_VBATREG_STEP_NUM		125
#define X810_SM5440_VBATREG_STEP_DEN		10
#define X810_SM5440_VBATREG_MAX_CODE		0x3f
#define X810_SM5440_IBUS_MAX_MA		6350
#define X810_SM5440_IBUS_STEP_MA		50

/* Match Samsung's downstream SM5440 PPS refresh cadence (2.5 s) while staying
 * comfortably inside the observed ~5 s EP-T4510 fallback window. The shorter
 * 2 s cadence was colliding with unsolicited source Alert/Get_Status AMSs on
 * this TCPM port; an Alert during the sink's PPS AMS makes TCPM soft-reset and
 * renegotiate fixed PDO. The driver uses jiffies and schedules polling against
 * this absolute deadline so park/settle and worker tails do not accumulate as
 * tick drift.
 */
#define X810_SM5440_PPS_REFRESH_INTERVAL_MS	2500UL

/* `now - last` is intentionally unsigned: it remains wrap-safe for intervals
 * much shorter than half the unsigned-long counter range (as this one is).
 */
static inline bool
x810_sm5440_pps_refresh_due(unsigned long now, unsigned long last,
			    unsigned long interval)
{
	return now - last >= interval;
}

static inline unsigned long
x810_sm5440_pps_refresh_delay(unsigned long now, unsigned long last,
			      unsigned long interval,
			      unsigned long poll_delay)
{
	unsigned long elapsed = now - last;
	unsigned long remaining;

	if (elapsed >= interval)
		return 0;
	remaining = interval - elapsed;
	return remaining < poll_delay ? remaining : poll_delay;
}

/* CYG1 SM5440 INT1..4 read-to-clear latches. Permit only defined benign
 * indications; unknown/reserved bits are also failed closed. */
#define X810_SM5440_PT_INT1_FAULT_MASK 0xff /* no benign INT1 bits */
#define X810_SM5440_PT_INT2_FAULT_MASK 0x77 /* allow IBUSLIM + VBATREG only */
#define X810_SM5440_PT_INT3_FAULT_MASK 0xdf /* allow VBUSPOK only */
#define X810_SM5440_PT_INT4_FAULT_MASK 0x8e /* allow ADCUPDATED + ready 4..6 */
#define X810_SM5440_PT_STATUS2_FAULT_MASK 0x02 /* THEM_REG */
#define X810_SM5440_PT_STATUS3_FAULT_MASK 0x18 /* thermal alarm/shutdown */

/*
 * Validate one PPS operating point against both the live TCPM state and the
 * X810/SM5440 electrical envelope. All voltages/currents are the Linux
 * power_supply units (uV/uA), except the requested target (mV/mA). Do not
 * issue an operating-point write based only on a cached "PPS capable" label:
 * require an active PPS contract, valid APDO readback, board bounds, 20 mV
 * voltage grid, and the pump's 15 W minimum operating point.
 */
static inline bool
x810_sm5440_pps_point_valid(int online, bool pps_type, int apdo_min_uv,
			   int apdo_max_uv, int apdo_max_ua,
			   int target_mv, int target_ma)
{
	if (online != 2 || !pps_type || apdo_min_uv <= 0 ||
	    apdo_max_uv < apdo_min_uv || apdo_max_ua <= 0 ||
	    target_mv < 5000 || target_mv > X810_SM5440_PT_MAX_PPS_MV ||
	    target_mv % X810_SM5440_PT_VOLTAGE_STEP_MV ||
	    target_ma < 1800 || target_ma > 3000 ||
	    target_mv * 1000 < apdo_min_uv ||
	    target_mv * 1000 > apdo_max_uv ||
	    target_ma * 1000 > apdo_max_ua ||
	    target_mv * target_ma < 15000000)
		return false;

	return true;
}

/* TCPM POWER_SUPPLY_PROP_ONLINE values used by the local Linux TCPM driver. */
enum x810_sm5440_handoff_state {
	X810_SM5440_HANDOFF_INVALID = -1,
	X810_SM5440_HANDOFF_OFFLINE = 0,
	X810_SM5440_HANDOFF_FIXED_PD = 1,
	X810_SM5440_HANDOFF_PPS = 2,
};

/*
 * A parked pump can hand SM5714 ownership back immediately if TCPM says the
 * source is gone. Otherwise request/verify a fixed contract first. Preserve
 * negative property-read errors and reject unexpected ONLINE values.
 */
static inline enum x810_sm5440_handoff_state
x810_sm5440_handoff_state(int online)
{
	if (online == 0)
		return X810_SM5440_HANDOFF_OFFLINE;
	if (online == 1)
		return X810_SM5440_HANDOFF_FIXED_PD;
	if (online == 2)
		return X810_SM5440_HANDOFF_PPS;
	return X810_SM5440_HANDOFF_INVALID;
}

/* A failed request to leave PPS can still have completed a usable handoff:
 * some sources/TCPM paths report an error after switching to fixed PD or
 * dropping the contract. Only accept those states after read-back; never
 * release the charger owner while PPS remains active or state is unknown.
 */
static inline enum x810_sm5440_handoff_state
x810_sm5440_handoff_after_fixed_request(int online)
{
	enum x810_sm5440_handoff_state state =
		x810_sm5440_handoff_state(online);

	if (state == X810_SM5440_HANDOFF_FIXED_PD ||
	    state == X810_SM5440_HANDOFF_OFFLINE)
		return state;

	return X810_SM5440_HANDOFF_INVALID;
}

/* CYG1 uses ci_gl + SM5440_CI_OFFSET (300 mA) in both direct-charge setup and
 * manual CV_MAN setup. This is the pump's internal input-current regulation
 * ceiling; it does not raise the TCPM-negotiated source contract. IBUSCNTL
 * rounds down to 50 mA steps.
 */
static inline int
x810_sm5440_ibus_limit_ma(int negotiated_ma)
{
	int limit = negotiated_ma + X810_SM5440_IBUS_MARGIN_MA;

	if (limit < 0)
		limit = 0;
	if (limit > X810_SM5440_IBUS_MAX_MA)
		limit = X810_SM5440_IBUS_MAX_MA;

	return (limit / X810_SM5440_IBUS_STEP_MA) *
		X810_SM5440_IBUS_STEP_MA;
}

/*
 * CYG1 `sm_dc_start_manual_charging()` sets target_vbat = ADC_VBAT + 200 mV.
 * On the X810 product DTS, sm5440,en_vbatreg = 0, so
 * `sm5440_dc_set_charging_config()` adds SM5440_CV_OFFSET (50 mV) before
 * `sm5440_set_vbatreg()` encodes VBATCNTL as floor((mV - 3800) * 10 / 125),
 * saturating values below 3800 mV at code 0. Reject a value beyond the 6-bit
 * register range instead of allowing it to wrap when masked into VBATCNTL.
 * This is the vendor register encoding, not an independent safety proof.
 */
static inline int
x810_sm5440_pt_vbatreg_code(int adc_vbat_mv)
{
	int requested_mv, code;

	if (adc_vbat_mv < 0)
		return -1;
	requested_mv = adc_vbat_mv + X810_SM5440_PT_VBAT_TARGET_OFFSET_MV +
		X810_SM5440_PT_CV_OFFSET_MV;
	if (requested_mv < X810_SM5440_VBATREG_BASE_MV)
		return 0;
	code = ((requested_mv - X810_SM5440_VBATREG_BASE_MV) *
		X810_SM5440_VBATREG_STEP_DEN) /
		X810_SM5440_VBATREG_STEP_NUM;
	if (code > X810_SM5440_VBATREG_MAX_CODE)
		return -1;

	return code;
}

enum x810_sm5440_pt_state {
	X810_SM5440_PT_LOW = -1,
	X810_SM5440_PT_NORMAL = 0,
	X810_SM5440_PT_HIGH = 1,
};

enum x810_sm5440_pt_direction {
	X810_SM5440_PT_HOLD = 0,
	X810_SM5440_PT_RAISE_VOLTAGE = 1,
	X810_SM5440_PT_LOWER_VOLTAGE = -1,
};

/* Preserve microamp resolution so positive sub-mA readings aren't rounded to
 * zero before the control policy sees them. This is only an observed-sample
 * test: ADC quantization, sensor offset, averaging latency, and PPS settling
 * mean it cannot prove mathematically zero net charge.
 */
static inline bool x810_sm5440_pt_no_charge_sample(int current_ua)
{
	return current_ua <= 0;
}

static inline unsigned int
x810_sm5440_pt_positive_count(unsigned int previous, int current_ua)
{
	if (current_ua <= 0)
		return 0;
	if (previous < X810_SM5440_PT_CURRENT_CONFIRM_SAMPLES)
		previous++;
	return previous;
}

static inline unsigned int
x810_sm5440_pt_discharge_count(unsigned int previous, int current_ua)
{
	if (current_ua >= -X810_SM5440_PT_DISCHARGE_TOLERANCE_UA)
		return 0;
	if (previous < X810_SM5440_PT_CURRENT_CONFIRM_SAMPLES)
		previous++;
	return previous;
}

static inline bool
x810_sm5440_pt_soc_allowed(int soc_tenths, int reference_tenths)
{
	return soc_tenths > X810_SM5440_PT_MIN_SOC_TENTHS &&
	       soc_tenths <= X810_SM5440_PT_MAX_SOC_TENTHS &&
	       reference_tenths > X810_SM5440_PT_MIN_SOC_TENTHS &&
	       reference_tenths <= X810_SM5440_PT_MAX_SOC_TENTHS;
}

/* Exact vendor check_cap() behavior, including the 0.6/0.4 decile bands. */
static inline enum x810_sm5440_pt_state
x810_sm5440_pt_classify_soc(int soc_tenths, int reference_tenths,
			    int current_ua)
{
	int soc_whole = soc_tenths / 10;
	int soc_fraction = soc_tenths % 10;
	int reference_whole = reference_tenths / 10;

	if (soc_whole == reference_whole) {
		if (soc_fraction >= 6 && current_ua > 0)
			return X810_SM5440_PT_HIGH;
		if (soc_fraction <= 4 && current_ua < 0)
			return X810_SM5440_PT_LOW;
		return X810_SM5440_PT_NORMAL;
	}
	if (soc_whole > reference_whole)
		return current_ua > 0 ? X810_SM5440_PT_HIGH :
			X810_SM5440_PT_NORMAL;

	return current_ua < 0 ? X810_SM5440_PT_LOW :
		X810_SM5440_PT_NORMAL;
}

/* Positive pack current is never the target: after three consecutive positive
 * samples, step PPS down on each continuing positive sample. Negative current
 * within the tolerance is accepted as a small load; a larger sustained draw
 * steps PPS up. This policy reduces observed charging but cannot guarantee
 * zero net charge because FG readings are quantized and have unknown offset.
 */
static inline enum x810_sm5440_pt_direction
x810_sm5440_pt_voltage_direction(int current_ua,
					 unsigned int positive_samples,
					 unsigned int discharge_samples)
{
	/* Charge avoidance takes priority over SOC recovery: after three
	 * consecutive positive samples, reduce PPS voltage one step per sample.
	 */
	if (current_ua > 0 &&
	    positive_samples >= X810_SM5440_PT_CURRENT_CONFIRM_SAMPLES)
		return X810_SM5440_PT_LOWER_VOLTAGE;
	/* Small discharge is the intentional tolerance around the zero-current
	 * target. Only correct a larger, sustained discharge after confirmation.
	 */
	if (current_ua < -X810_SM5440_PT_DISCHARGE_TOLERANCE_UA &&
	    discharge_samples >= X810_SM5440_PT_CURRENT_CONFIRM_SAMPLES)
		return X810_SM5440_PT_RAISE_VOLTAGE;
	return X810_SM5440_PT_HOLD;
}

/* Samsung CV_MAN starts from 2 * SM5440 VBAT ADC + 80 mV, 20 mV rounded. */
static inline int x810_sm5440_pt_initial_target_mv(int vbat_mv)
{
	int target;

	if (vbat_mv < 0)
		return -1;
	target = 2 * vbat_mv + X810_SM5440_PT_START_HEADROOM_MV;
	target = ((target + X810_SM5440_PT_VOLTAGE_STEP_MV / 2) /
		  X810_SM5440_PT_VOLTAGE_STEP_MV) *
		 X810_SM5440_PT_VOLTAGE_STEP_MV;
	if (target < 5000 || target > X810_SM5440_PT_MAX_PPS_MV)
		return -1;
	return target;
}

/* Samsung CV_MAN's lower VBUS bound is 2*the pump's ADC VBAT + 40 mV.
 * PPS voltage requests are quantized in 20 mV steps, so round the floor up.
 * Return -1 for ADC values that cannot form a legal low-voltage PPS point.
 */
static inline int x810_sm5440_pt_min_target_mv(int vbat_mv)
{
	int floor_mv;

	if (vbat_mv < 0)
		return -1;
	floor_mv = 2 * vbat_mv + X810_SM5440_PT_MIN_HEADROOM_MV;
	floor_mv = ((floor_mv + X810_SM5440_PT_VOLTAGE_STEP_MV - 1) /
		    X810_SM5440_PT_VOLTAGE_STEP_MV) *
		   X810_SM5440_PT_VOLTAGE_STEP_MV;
	if (floor_mv < 5000 || floor_mv > X810_SM5440_PT_MAX_PPS_MV)
		return -1;
	return floor_mv;
}

/* Combine pack-current feedback with the SM5440's hardware loop indication.
 * Vendor CV_MAN reduces PPS for VBATREG/IBUSLIM, but never crosses its ADC
 * VBAT-derived minimum. We additionally lower after confirmed positive pack
 * current to target no charge. Reaching the floor while still charging is an
 * inability to satisfy this policy: caller must stop CV_MAN and hand back.
 */
static inline enum x810_sm5440_pt_direction
x810_sm5440_pt_control_direction(int current_ua, unsigned int positive_samples,
				 unsigned int discharge_samples,
				 bool vbatreg, bool ibuslim)
{
	enum x810_sm5440_pt_direction direction;

	direction = x810_sm5440_pt_voltage_direction(current_ua,
		positive_samples, discharge_samples);
	if (vbatreg || ibuslim) {
		if (direction != X810_SM5440_PT_LOWER_VOLTAGE)
			return X810_SM5440_PT_LOWER_VOLTAGE;
	}
	return direction;
}

/* Lowering is allowed only if the next discrete PPS point remains at/above
 * Samsung's live ADC-derived floor. A current target below that floor is
 * already inconsistent with the vendor invariant and is rejected too.
 */
static inline int
x810_sm5440_pt_next_safe_target_mv(int current_mv,
				  enum x810_sm5440_pt_direction direction,
				  int apdo_min_mv, int apdo_max_mv, int vbat_mv)
{
	int floor_mv = x810_sm5440_pt_min_target_mv(vbat_mv);
	int target_mv, minimum, maximum;

	if (floor_mv < 0 || current_mv < floor_mv)
		return -1;
	if (direction == X810_SM5440_PT_HOLD)
		return current_mv;
	minimum = apdo_min_mv > 5000 ? apdo_min_mv : 5000;
	maximum = apdo_max_mv < X810_SM5440_PT_MAX_PPS_MV ?
		apdo_max_mv : X810_SM5440_PT_MAX_PPS_MV;
	target_mv = current_mv + direction * X810_SM5440_PT_VOLTAGE_STEP_MV;
	if (target_mv < minimum || target_mv > maximum)
		return -1;
	if (target_mv < floor_mv)
		return -1;
	return target_mv;
}

/* Return a bounded next point, or -1 when that correction exceeds bounds. */
static inline int
x810_sm5440_pt_next_target_mv(int current_mv,
			      enum x810_sm5440_pt_direction direction,
			      int apdo_min_mv, int apdo_max_mv)
{
	int target = current_mv + direction * X810_SM5440_PT_VOLTAGE_STEP_MV;
	int minimum = apdo_min_mv > 5000 ? apdo_min_mv : 5000;
	int maximum = apdo_max_mv < X810_SM5440_PT_MAX_PPS_MV ?
		apdo_max_mv : X810_SM5440_PT_MAX_PPS_MV;

	if (direction == X810_SM5440_PT_HOLD)
		return current_mv;
	if (target < minimum || target > maximum)
		return -1;
	return target;
}

/* The Fedora CV_MAN loop does not implement Samsung's IRQ/error state machine. */
static inline bool
x810_sm5440_pt_irq_faulted(unsigned int int1, unsigned int int2,
			   unsigned int int3, unsigned int int4)
{
	return (int1 & X810_SM5440_PT_INT1_FAULT_MASK) ||
	       (int2 & X810_SM5440_PT_INT2_FAULT_MASK) ||
	       (int3 & X810_SM5440_PT_INT3_FAULT_MASK) ||
	       (int4 & X810_SM5440_PT_INT4_FAULT_MASK);
}

/* Status is live/non-latched; INT1..4 is read-to-clear. Both sources must be
 * clean before re-arming after a PPS transition in the opt-in CV_MAN path.
 */
static inline bool
x810_sm5440_pt_rearm_faulted(unsigned int status2, unsigned int status3,
			     unsigned int int1, unsigned int int2,
			     unsigned int int3, unsigned int int4)
{
	return (status2 & X810_SM5440_PT_STATUS2_FAULT_MASK) ||
	       (status3 & X810_SM5440_PT_STATUS3_FAULT_MASK) ||
	       x810_sm5440_pt_irq_faulted(int1, int2, int3, int4);
}

#endif /* X810_SM5440_BYPASS_POLICY_H */
