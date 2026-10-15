// SPDX-License-Identifier: GPL-2.0-only
/*
 * Silicon Mitus SM5440 2:1 direct charger for the Samsung SM-X710.
 *
 * This is a deliberately small mainline-first driver.  Linux TCPM owns USB-PD
 * policy; this driver only requests a conservative PPS operating point, hands
 * the battery path over from SM5714, and programs the board's charge pump using
 * the register sequence published in Samsung's GPL source.  Every failure
 * turns the pump off and restores the fixed-PD switching charger.
 *
 * Direct charging is off by default; the worker requires the SM5714
 * fast_charge sysfs control to be enabled before it starts normal PPS charging.
 */

#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/power_supply.h>
#include <linux/suspend.h>
#include <linux/workqueue.h>

#include "x810_pd_limits.h"
#include "x810_sm5440_bypass_policy.h"

/*
 * Interrupt latches.  They are read-to-clear, and they are the only thing that
 * says *why* the chip cut the pump off -- otherwise a stop is just "CHG_ON went
 * away".  Bit names follow Silicon Mitus' own driver header.
 */
#define SM5440_REG_INT1			0x00
#define SM5440_REG_INT2			0x01
#define SM5440_REG_INT3			0x02
#define SM5440_REG_INT4			0x03
#define  SM5440_INT1_VOUTOVP		BIT(4)
#define  SM5440_INT1_VBATOVP		BIT(3)
#define  SM5440_INT1_REVBPOCP		BIT(1)
#define  SM5440_INT1_REVBSTOCP		BIT(0)
#define  SM5440_INT3_VBUSOVP		BIT(7)
#define  SM5440_INT3_VBUSUVLO		BIT(6)
#define  SM5440_INT3_THEMSHDN_ALM	BIT(4)
#define  SM5440_INT3_THEMSHDN		BIT(3)
#define  SM5440_INT3_STUP_FAIL		BIT(2)
#define  SM5440_INT3_REVBLK		BIT(1)
#define  SM5440_INT3_CFLY_SHORT		BIT(0)
#define  SM5440_INT1_REFRESH_FAULTS	(SM5440_INT1_VOUTOVP | \
					 SM5440_INT1_VBATOVP | \
					 SM5440_INT1_REVBPOCP | \
					 SM5440_INT1_REVBSTOCP)
#define  SM5440_INT3_REFRESH_FAULTS	(SM5440_INT3_VBUSOVP | \
					 SM5440_INT3_VBUSUVLO | \
					 SM5440_INT3_THEMSHDN_ALM | \
					 SM5440_INT3_THEMSHDN | \
					 SM5440_INT3_STUP_FAIL | \
					 SM5440_INT3_REVBLK | \
					 SM5440_INT3_CFLY_SHORT)
#define SM5440_REG_STATUS1		0x08
#define SM5440_REG_STATUS2		0x09
#define  SM5440_STATUS2_IBUSLIM		BIT(7)
#define  SM5440_STATUS2_VBATREG		BIT(3)
#define  SM5440_STATUS2_THEM_REG	BIT(1)
#define SM5440_REG_STATUS3		0x0a
#define  SM5440_STATUS3_VBUSPOK		BIT(5)
#define  SM5440_STATUS3_THEMSHDN_ALM	BIT(4)
#define  SM5440_STATUS3_THEMSHDN	BIT(3)
#define SM5440_REG_CNTL1		0x0c
#define  SM5440_CNTL1_SW_RESET		BIT(0)
#define  SM5440_CNTL1_WDT_EN		BIT(7)
#define  SM5440_CNTL1_WDT_30S		(4 << 4)
#define SM5440_REG_CNTL2		0x0d
#define  SM5440_CNTL2_REVBLK_EN		BIT(7)
#define SM5440_REG_CNTL3		0x0e
#define SM5440_REG_CNTL4		0x0f
#define SM5440_REG_CNTL5		0x10
#define  SM5440_CNTL5_OP_MODE_MASK	GENMASK(3, 2)
#define  SM5440_CNTL5_CHG_ON		BIT(2)
#define SM5440_REG_CNTL6		0x11
#define SM5440_REG_CNTL7		0x12
#define SM5440_REG_VBUSCNTL		0x13
#define SM5440_REG_VBATCNTL		0x14
#define SM5440_REG_VOUTCNTL		0x15
#define SM5440_REG_IBUSCNTL		0x16
#define SM5440_REG_PRTNCNTL		0x19
#define SM5440_REG_THEMCNTL1		0x1a
#define SM5440_REG_ADCCNTL1		0x1c
#define  SM5440_ADCCNTL1_AVG_32		BIT(3)
#define  SM5440_ADCCNTL1_CONTINUOUS	BIT(1)
#define  SM5440_ADCCNTL1_ENABLE	BIT(0)
#define SM5440_REG_ADCCNTL2		0x1d
#define SM5440_REG_ADC_VBUS1		0x1e
#define SM5440_REG_ADC_IBUS1		0x22
#define SM5440_REG_ADC_DIETEMP		0x26
#define SM5440_REG_ADC_VBAT1		0x27
#define SM5440_REG_DEVICEID		0x2b

#define SM5440_POLL_MS			1000
#define SM5440_RETRY_MS			2000
#define SM5440_MAX_RETRY_MS		30000
#define SM5440_QUIET_RETRY_MS		300000
#define SM5440_MAX_FAILS		5

/*
 * TCPM answers -EAGAIN while its state machine is not in SNK_READY, or while the
 * source has not signalled SinkTxOK yet.  That is a "try again" condition, not
 * a failure, so the PD properties are retried briefly before giving up.
 */
#define SM5440_PD_RETRY_MS		200
#define SM5440_PD_RETRIES		5

#define SM5440_INITIAL_IBUS_MA		1800
#define SM5440_VBATREG_MV		4400
#define SM5440_OPERATING_SNK_MW		15000

/*
 * How much input current to ask the adapter for.
 *
 * The board's advertised sink APDO and CYG1 manual pass-through path are capped
 * at 3 A. Never increase this based solely on the source adapter or cable rating.
 * The source's lower APDO limit is still discovered at run time by walking down
 * from this board limit.
 */
#define SM5440_MAX_PPS_MA		X810_PPS_DEFAULT_MA
#define SM5440_MIN_PPS_MA		X810_PPS_MIN_MA
#define SM5440_PPS_STEP_MA		X810_PPS_CURRENT_STEP_MA
#define SM5440_IBUS_MARGIN_MA		X810_SM5440_IBUS_MARGIN_MA
#define SM5440_PPS_V_STEP_MV		20

static unsigned int max_pps_ma = SM5440_MAX_PPS_MA;
module_param(max_pps_ma, uint, 0644);
MODULE_PARM_DESC(max_pps_ma,
		 "Highest PPS input current to request, in mA (default/max 3000)");

/* Off unless explicitly enabled by an administrator; requires the configured
 * SM5714 charge cap to be active and all runtime power/thermal guards to pass.
 * Keep it opt-in: the Samsung init sequence programs CNTL2=0xf2 (vendor
 * comment: disables IBUSOCP, IBATOCP and THEM). The STATUS2/3 monitor below is
 * not a proven substitute for those hardware protections; do not alter the
 * CNTL2 protection bits without an SM5440 datasheet / board validation.
 */
static bool manual_pass_through;
module_param(manual_pass_through, bool, 0644);
MODULE_PARM_DESC(manual_pass_through,
		 "Opt in to Samsung-style SM5440 CV_MAN pass-through at an active charge cap");

/*
 * Board tuning from the X710 device tree (sm5440,freq = 850, freq_siop = 450
 * 650, r_ttl = 320000): the vendor driver drops the pump's switching frequency
 * at low charge current and keeps the drop across the cable and connector in
 * the PPS operating point.  Samsung caps this board's PD path at 9 V.
 */
#define SM5440_FREQUENCY_KHZ		850
#define SM5440_FREQUENCY_BYPASS_KHZ	X810_SM5440_PT_FREQUENCY_KHZ
#define SM5440_FREQUENCY_SIOP2_KHZ	650
#define SM5440_FREQUENCY_SIOP1_KHZ	450
#define SM5440_SIOP_LEV1_MA		1100
#define SM5440_SIOP_LEV2_MA		1700
#define SM5440_R_TTL_MILLIOHM		320
/* The X910 SM5440 port measured only ~290 mV left at the pump with a
 * 700 mV requested margin and recurrent REVBLK; 1100 mV stopped those trips.
 * X810's observed 1.8 A attempt likewise sagged from 9.38 V to 8.80 V,
 * leaving <200 mV above 2*VBAT. 525 mV fixed margin plus R_TTL*I yields
 * ~1100 mV at 1.8 A. Keep the operating point bounded by the board APDO below.
 */
#define SM5440_EXTRA_HEADROOM_MV	525
/*
 * The vendor's own ceiling: its dc_vbus_ovp_th is 11 V and the direct-charge
 * loop never asks for more than dc_vbus_ovp_th - 500.  The operating point needs
 * that room -- at the board's 3 A the cable and connector drop alone is
 * 3 A x 320 mOhm = 960 mV on top of twice the cell voltage -- so clamping lower
 * would starve the pump exactly where the current matters.
 */
#define SM5440_MAX_PPS_MV		X810_PPS_MAX_MV

/*
 * Direct charging is opt-in.
 *
 * The pump needs a programmable supply, so this driver re-requests a PPS
 * operating point every two seconds for as long as it runs.  On this board that
 * request path, not the pump, is the fragile part: a keep-alive Request that
 * lands while the source re-applies its output trips the chip's reverse-blocking
 * comparator (measured: INT3 bit 1 latched, CHG_ON cleared by the chip itself),
 * and after enough of those the SM5714 TCPC stops answering on i2c, after which
 * every further Request fails with -EAGAIN ("port not ready") for the rest of
 * the boot.  Meanwhile the SM5714 switching charger on the fixed 9 V contract --
 * which is what Samsung's own board data programs -- holds the same pack current
 * (measured 2.4-2.8 A at this pack voltage) with none of that churn.
 *
 * The switch is the charger driver's fast_charge attribute, which
 * gnome-gts9wifi and /usr/libexec/gts9wifi-device-control drive: turning it off
 * hands the pack straight back to the switching path, which is what a stock
 * tablet uses.
 */
int sm5714_battery_set_direct_charge(bool active);
int sm5714_battery_set_direct_passthrough(bool active);
bool sm5714_battery_fast_charge_enabled(void);
bool sm5714_battery_charge_limit_reached(int capacity);

struct sm5440_direct {
	struct device *dev;
	struct i2c_client *client;
	struct power_supply *tcpm;
	struct power_supply *battery;
	struct delayed_work work;
	int target_mv;
	int target_ma;
	int passthrough_target_mv;
	unsigned long last_pps_request;
	unsigned int fails;
	bool active;
	bool passthrough_active;
	bool live_refresh_available;
	bool pps_request_valid;
	int passthrough_reference_soc;
	unsigned int passthrough_positive_samples;
	unsigned int passthrough_discharge_samples;
	bool restore_pending;
	bool suspending;
	struct notifier_block pm_nb;
};

static bool sm5440_eligible(struct sm5440_direct *sm);
static bool sm5440_pt_eligible(struct sm5440_direct *sm, bool active);

static int sm5440_update_bits(struct sm5440_direct *sm, u8 reg, u8 mask,
			      u8 val)
{
	int old;

	old = i2c_smbus_read_byte_data(sm->client, reg);
	if (old < 0)
		return old;

	return i2c_smbus_write_byte_data(sm->client, reg,
					 (old & ~mask) | (val & mask));
}

static int sm5440_read_adc_pair(struct sm5440_direct *sm, u8 reg)
{
	int high, low;

	high = i2c_smbus_read_byte_data(sm->client, reg);
	if (high < 0)
		return high;
	low = i2c_smbus_read_byte_data(sm->client, reg + 1);
	if (low < 0)
		return low;

	return (high << 5) | (low >> 3);
}

static int sm5440_adc_vbus_mv(struct sm5440_direct *sm)
{
	int raw = sm5440_read_adc_pair(sm, SM5440_REG_ADC_VBUS1);

	return raw < 0 ? raw : 4096 + raw;
}

static int sm5440_adc_ibus_ma(struct sm5440_direct *sm)
{
	int raw = sm5440_read_adc_pair(sm, SM5440_REG_ADC_IBUS1);

	return raw < 0 ? raw : (raw * 625) / 1000;
}

static int sm5440_adc_vbat_mv(struct sm5440_direct *sm)
{
	int raw = sm5440_read_adc_pair(sm, SM5440_REG_ADC_VBAT1);

	return raw < 0 ? raw : 2048 + (raw * 500) / 1000;
}

static int sm5440_adc_die_temp(struct sm5440_direct *sm)
{
	int raw = i2c_smbus_read_byte_data(sm->client,
					   SM5440_REG_ADC_DIETEMP);

	return raw < 0 ? raw : 225 + raw * 5;
}

static int sm5440_psy_get_value(struct power_supply *psy,
				enum power_supply_property prop, int *value)
{
	union power_supply_propval val;
	int ret;

	ret = power_supply_get_property(psy, prop, &val);
	if (ret)
		return ret;
	*value = val.intval;
	return 0;
}

static int sm5440_psy_get(struct power_supply *psy,
			  enum power_supply_property prop)
{
	int value;
	int ret = sm5440_psy_get_value(psy, prop, &value);

	return ret ? ret : value;
}

static int sm5440_psy_set(struct power_supply *psy,
			  enum power_supply_property prop, int value)
{
	union power_supply_propval val = { .intval = value };

	return power_supply_set_property(psy, prop, &val);
}

static int sm5440_psy_retry(struct sm5440_direct *sm,
			    enum power_supply_property prop, int value)
{
	int ret = -EAGAIN;
	int i;

	for (i = 0; i < SM5440_PD_RETRIES && ret == -EAGAIN; i++) {
		if (i)
			msleep(SM5440_PD_RETRY_MS);
		ret = sm5440_psy_set(sm->tcpm, prop, value);
	}

	return ret;
}

/*
 * Return the adapter to its fixed contract before handing the battery path
 * back, but do not issue a PD request after TCPM reports no source. A live PPS
 * request is synchronous; read ONLINE again so a source detach or post-error
 * transition can be distinguished from a stale PPS contract. Only verified
 * fixed-PD or offline states release SM5714 ownership.
 */
static int sm5440_restore_fixed_pd_if_online(
	struct sm5440_direct *sm, enum x810_sm5440_handoff_state *state)
{
	int online, online_before, voltage_before_uv, current_before_ua;
	int voltage_after_uv, current_after_ua, ret;

	online = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	*state = x810_sm5440_handoff_state(online);
	if (*state == X810_SM5440_HANDOFF_INVALID)
		return online < 0 ? online : -EINVAL;
	if (*state != X810_SM5440_HANDOFF_PPS)
		return 0;

	online_before = online;
	voltage_before_uv = sm5440_psy_get(sm->tcpm,
					   POWER_SUPPLY_PROP_VOLTAGE_NOW);
	current_before_ua = sm5440_psy_get(sm->tcpm,
					   POWER_SUPPLY_PROP_CURRENT_NOW);
	ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_ONLINE, 1);
	online = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	*state = x810_sm5440_handoff_after_fixed_request(online);
	voltage_after_uv = sm5440_psy_get(sm->tcpm,
					  POWER_SUPPLY_PROP_VOLTAGE_NOW);
	current_after_ua = sm5440_psy_get(sm->tcpm,
					  POWER_SUPPLY_PROP_CURRENT_NOW);
	dev_info(sm->dev,
		 "TCPM fixed-PD handback: request=%d ONLINE=%d->%d VBUS=%d->%d mV current=%d->%d mA\n",
		 ret, online_before, online,
		 voltage_before_uv < 0 ? voltage_before_uv :
			 voltage_before_uv / 1000,
		 voltage_after_uv < 0 ? voltage_after_uv :
			 voltage_after_uv / 1000,
		 current_before_ua < 0 ? current_before_ua :
			 current_before_ua / 1000,
		 current_after_ua < 0 ? current_after_ua :
			 current_after_ua / 1000);
	if (*state == X810_SM5440_HANDOFF_INVALID)
		return online < 0 ? online : -EINVAL;
	/* A source can drop the PD connection while rejecting or timing out the
	 * transition back from PPS. Treat an error return as failure only when the
	 * read-back still shows PPS; a verified fixed or offline state is already a
	 * completed handoff. Never release SM5714 ownership on stale/unknown state.
	 */
	if (*state == X810_SM5440_HANDOFF_PPS)
		return ret ? ret : -EIO;
	if (ret)
		dev_warn(sm->dev,
			 "fixed-PD request returned %d but TCPM read-back is %s; accepting verified handoff\n",
			 ret, *state == X810_SM5440_HANDOFF_OFFLINE ?
			 "offline" : "fixed-PD");
	if (*state != X810_SM5440_HANDOFF_OFFLINE &&
	    *state != X810_SM5440_HANDOFF_FIXED_PD)
		return -EIO;

	return 0;
}

/*
 * The pump halves the bus, so the operating point has to carry twice the cell
 * voltage plus the drop across the cable and connector (r_ttl on this board)
 * plus a small margin, exactly as the vendor loop computes it.
 */
static int sm5440_pps_target_mv(int ma, int vbat_uv)
{
	int headroom = DIV_ROUND_UP(ma * SM5440_R_TTL_MILLIOHM, 1000) +
		       SM5440_EXTRA_HEADROOM_MV;
	int min_power_mv = DIV_ROUND_UP(SM5440_OPERATING_SNK_MW * 1000, ma);
	int pump_target_mv = DIV_ROUND_UP((vbat_uv / 1000) * 2 + headroom,
					 SM5440_PPS_V_STEP_MV) *
			     SM5440_PPS_V_STEP_MV;
	int power_floor_mv = DIV_ROUND_UP(min_power_mv,
					  SM5440_PPS_V_STEP_MV) *
			     SM5440_PPS_V_STEP_MV;
	int mv = max(pump_target_mv, power_floor_mv);

	return max(mv, 8200);
}

/*
 * The chip's own input limit, kept a little above the requested PPS current so
 * the pump is not the thing throttling the contract, the way the vendor does it
 * (ibuslim = ci_gl + SM5440_CI_OFFSET).  IBUSCNTL encodes 50 mA per step up to
 * 0x7f, i.e. 6350 mA.
 */
static int sm5440_set_ibus_limit(struct sm5440_direct *sm, int ma)
{
	int limit = x810_sm5440_ibus_limit_ma(ma);

	return i2c_smbus_write_byte_data(sm->client, SM5440_REG_IBUSCNTL,
					 limit / 50);
}

/*
 * Activate PPS with the charge pump parked, then read TCPM's active source
 * APDO bounds through VOLTAGE_MIN/MAX and CURRENT_MAX. Linux 7.2 exposes those
 * source bounds rather than intersecting them with our sink APDO, so clamp to
 * the X810 board/pump envelope before issuing operating-point requests.
 */
static int sm5440_negotiate_pps(struct sm5440_direct *sm, int vbat_uv,
				int *target_ma, int *target_mv,
				bool manual_pt)
{
	int online, usb_type, min_uv, max_uv, max_ua;
	unsigned int min_mv, max_mv;
	int ma, mv, ret;

	ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_ONLINE, 2);
	if (ret) {
		dev_warn_ratelimited(sm->dev,
			"TCPM PPS activation request failed: %d\n", ret);
		return ret;
	}

	/* ONLINE=2 is synchronous: TCPM selects an eligible source APDO and
	 * completes PPS negotiation before returning. Read its limits, then intersect
	 * against the board's sink APDO instead of guessing source capabilities.
	 */
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	if (ret < 0)
		goto fixed;
	online = ret;
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_USB_TYPE);
	if (ret < 0)
		goto fixed;
	usb_type = ret;
	if (online != 2 || usb_type != POWER_SUPPLY_USB_TYPE_PD_PPS) {
		dev_warn_ratelimited(sm->dev,
			"TCPM PPS activation readback mismatch: ONLINE=%d USB_TYPE=%d\n",
			online, usb_type);
		ret = -EOPNOTSUPP;
		goto fixed;
	}
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MIN);
	if (ret < 0)
		goto fixed;
	min_uv = ret;
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MAX);
	if (ret < 0)
		goto fixed;
	max_uv = ret;
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_MAX);
	if (ret < 0)
		goto fixed;
	max_ua = ret;
	if (min_uv < 0 || max_uv < 0 || max_ua <= 0 ||
	    !x810_pd_pps_intersect_voltage(min_uv / 1000, max_uv / 1000,
					   &min_mv, &max_mv)) {
		ret = -ERANGE;
		goto fixed;
	}
	/* Linux 7.2 TCPM exposes the source APDO limits here. Clamp its range to
	 * our sink PDO/SM5440 electrical envelope rather than rejecting common
	 * 3.3-11V PPS APDOs just because they extend outside the board's 5-10.5V
	 * sink range.
	 */
	min_uv = min_mv * 1000;
	max_uv = max_mv * 1000;

	ma = x810_pd_pps_intersect_current(
		x810_pd_pps_current_limit(manual_pt ? SM5440_MAX_PPS_MA :
								     max_pps_ma),
		max_ua / 1000);
	if (ma < SM5440_MIN_PPS_MA) {
		ret = -ERANGE;
		goto fixed;
	}
	if (manual_pt) {
		/* CV_MAN's Samsung starting point is 2*VBAT+80mV, not the normal
		 * direct-charge cable-drop target. It is only allowed if the
		 * negotiated source/sink APDO really includes that point and 3A.
		 */
		if (ma != SM5440_MAX_PPS_MA) {
			ret = -ERANGE;
			goto fixed;
		}
		mv = x810_sm5440_pt_initial_target_mv(vbat_uv / 1000);
	} else {
		mv = sm5440_pps_target_mv(ma, vbat_uv);
		mv = max(mv, DIV_ROUND_UP(min_uv, 1000));
		mv = DIV_ROUND_UP(mv, SM5440_PPS_V_STEP_MV) *
			SM5440_PPS_V_STEP_MV;
	}
	if (mv > max_uv / 1000 ||
	    mv < DIV_ROUND_UP(min_uv, 1000) ||
	    (mv * ma) / 1000 < SM5440_OPERATING_SNK_MW) {
		ret = -ERANGE;
		goto fixed;
	}

	/* First raise/confirm current while the initial PPS voltage is still the
	 * safe pre-transition 9 V contract. Then set the ADC-derived operating
	 * voltage. This driver enforces the board/source intersection above; TCPM
	 * independently validates the request against the active source APDO.
	 */
	ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_CURRENT_NOW, ma * 1000);
	if (ret)
		goto fixed;
	ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_VOLTAGE_NOW,
			       mv * 1000);
	if (ret)
		goto fixed;
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	if (ret != 2) {
		ret = ret < 0 ? ret : -EIO;
		goto fixed;
	}
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_USB_TYPE);
	if (ret != POWER_SUPPLY_USB_TYPE_PD_PPS) {
		ret = ret < 0 ? ret : -EIO;
		goto fixed;
	}
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_NOW);
	if (ret != mv * 1000) {
		ret = ret < 0 ? ret : -ERANGE;
		goto fixed;
	}
	ret = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_NOW);
	if (ret != ma * 1000) {
		ret = ret < 0 ? ret : -ERANGE;
		goto fixed;
	}
	*target_ma = ma;
	*target_mv = mv;
	/* Anchor future keep-alives to an actually accepted operating point, not
	 * to the end of the pump's startup/settle work. */
	sm->last_pps_request = jiffies;
	sm->pps_request_valid = true;

	dev_info(sm->dev, "PPS contract requested: %d mV/%d mA\n",
		 *target_mv, *target_ma);
	return 0;

fixed:
	{
		enum x810_sm5440_handoff_state handoff;
		int handoff_ret;

		handoff_ret = sm5440_restore_fixed_pd_if_online(sm, &handoff);
		if (handoff_ret)
			dev_warn(sm->dev,
				 "PPS setup failed (%d), fixed/offline fallback also failed (%d)\n",
				 ret, handoff_ret);
	}
	return ret;
}

/*
 * Keep the operating point alive.
 *
 * Every power_supply property write is its own Power Negotiation and each one
 * re-applies the source's output voltage, so the refresh sends as few of them as
 * it can: the voltage only when the pack has moved the operating point by a step
 * (the only way it changes), the current every time.  A same-voltage keep-alive
 * is attempted with the pump running and checked for hardware faults; actual
 * voltage steps retain the pump-park sequence below.
 */
static int sm5440_next_pps_target_mv(struct sm5440_direct *sm)
{
	int battery_uv;

	if (sm->passthrough_active)
		return sm->passthrough_target_mv;

	battery_uv = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_VOLTAGE_NOW);
	if (battery_uv < 0)
		return battery_uv;

	return sm5440_pps_target_mv(sm->target_ma, battery_uv);
}

static int sm5440_refresh_pps(struct sm5440_direct *sm)
{
	int target_mv, online, type, min_uv, max_uv, max_ua;
	int ret;

	target_mv = sm5440_next_pps_target_mv(sm);
	if (target_mv < 0)
		return target_mv;

	/* Check the currently negotiated source APDO immediately before every PPS
	 * write. The board-specific ceiling below supplies the other half of the
	 * intersection; a type label alone is insufficient.
	 */
	online = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	type = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_USB_TYPE);
	min_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MIN);
	max_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MAX);
	max_ua = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_MAX);
	if (!x810_sm5440_pps_point_valid(online,
			type == POWER_SUPPLY_USB_TYPE_PD_PPS,
			min_uv, max_uv, max_ua, target_mv, sm->target_ma))
		return -ERANGE;
	if (abs(target_mv - sm->target_mv) >= SM5440_PPS_V_STEP_MV) {
		ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_VOLTAGE_NOW,
				       target_mv * 1000);
		if (ret)
			return ret;
		sm->target_mv = target_mv;
	}

	ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_CURRENT_NOW,
			       sm->target_ma * 1000);
	if (!ret) {
		/* Only a completed TCPM Request moves the deadline. In particular,
		 * validation/property-write errors must leave the old deadline intact
		 * so callers take the existing fail-closed handoff path. */
		sm->last_pps_request = jiffies;
		sm->pps_request_valid = true;
	}
	return ret;
}

/* Same-point keep-alives must not recalculate or write a voltage. Doing so
 * could turn a supposedly uninterrupted refresh into a live voltage step if
 * the battery ADC crossed a rounding boundary after sm5440_renegotiate_pps()
 * made its decision. */
static int sm5440_refresh_pps_current_only(struct sm5440_direct *sm,
					   int target_mv)
{
	int online, type, min_uv, max_uv, max_ua;
	int ret;

	online = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	type = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_USB_TYPE);
	min_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MIN);
	max_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MAX);
	max_ua = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_MAX);
	if (!x810_sm5440_pps_point_valid(online,
			type == POWER_SUPPLY_USB_TYPE_PD_PPS,
			min_uv, max_uv, max_ua, target_mv, sm->target_ma))
		return -ERANGE;

	ret = sm5440_psy_retry(sm, POWER_SUPPLY_PROP_CURRENT_NOW,
			       sm->target_ma * 1000);
	if (!ret) {
		sm->last_pps_request = jiffies;
		sm->pps_request_valid = true;
	}
	return ret;
}

static int sm5440_set_freq(struct sm5440_direct *sm, int khz)
{
	return i2c_smbus_write_byte_data(sm->client, SM5440_REG_CNTL7,
					 (khz - 250) / 50);
}

static int sm5440_select_freq(struct sm5440_direct *sm, int ma)
{
	if (ma <= SM5440_SIOP_LEV1_MA)
		return sm5440_set_freq(sm, SM5440_FREQUENCY_SIOP1_KHZ);
	if (ma <= SM5440_SIOP_LEV2_MA)
		return sm5440_set_freq(sm, SM5440_FREQUENCY_SIOP2_KHZ);

	return sm5440_set_freq(sm, SM5440_FREQUENCY_KHZ);
}

static int sm5440_pump_off(struct sm5440_direct *sm)
{
	int mode;
	int ret;

	ret = sm5440_update_bits(sm, SM5440_REG_CNTL5,
				 SM5440_CNTL5_OP_MODE_MASK, 0);
	if (ret)
		return ret;

	/* Do not release battery ownership unless the pump reports itself parked. */
	mode = i2c_smbus_read_byte_data(sm->client, SM5440_REG_CNTL5);
	if (mode < 0)
		return mode;
	if (mode & SM5440_CNTL5_OP_MODE_MASK)
		return -EIO;

	return 0;
}

static int sm5440_pump_on(struct sm5440_direct *sm)
{
	int status3 = 0;
	int i;
	int ret;

	ret = sm5440_update_bits(sm, SM5440_REG_CNTL5,
				 SM5440_CNTL5_OP_MODE_MASK,
				 SM5440_CNTL5_CHG_ON);
	if (ret)
		return ret;

	/*
	 * VBUSPOK is set as soon as the pump starts pulling the bus down, which is
	 * well before the 100 ms this used to sleep unconditionally.  Poll for it
	 * instead, keeping the old worst case as the loop bound.
	 */
	for (i = 0; i < 5; i++) {
		msleep(20);
		status3 = i2c_smbus_read_byte_data(sm->client,
						   SM5440_REG_STATUS3);
		if (status3 < 0)
			return status3;
		if (status3 & SM5440_STATUS3_VBUSPOK)
			return 0;
	}

	return -ENOLINK;
}

/*
 * A PPS power_supply write completes before the adapter has necessarily
 * reached the requested voltage, and the chip's reverse-blocking comparator
 * latches REVBLK if the pump is switching across that step.  Let the SM5440's
 * own ADC prove the physical bus is ready before touching CHG_ON.
 */
static int sm5440_wait_vbus_settled(struct sm5440_direct *sm, int target_mv)
{
	int vbus_mv = 0;
	int i;

	/*
	 * The pump is parked while this runs, so every millisecond here is charge
	 * current the pack does not get.  The chip's bus ADC is already averaging
	 * (see ADCCNTL1 in hw_init) and the source has normally re-applied its
	 * output by now, so give it one conversion and then look, rather than
	 * sleeping a fixed 100 ms per attempt and waiting up to three seconds.
	 */
	for (i = 0; i < 30; i++) {
		msleep(i ? 50 : 20);
		vbus_mv = sm5440_adc_vbus_mv(sm);
		if (vbus_mv < 0)
			return vbus_mv;
		if (vbus_mv >= target_mv - 500)
			return 0;
	}

	dev_warn(sm->dev, "PPS bus did not settle: target=%dmV measured=%dmV\n",
		 target_mv, vbus_mv);
	return -ETIMEDOUT;
}

static int sm5440_pt_rearm_check(struct sm5440_direct *sm, const char *stage);

/* If a PPS keep-alive repeats the exact operating voltage, avoid stopping the
 * pump for the request. The SM5440 itself remains the safety boundary: verify
 * CHG_ON/VBUSPOK and inspect the read-to-clear fault latches immediately after
 * the synchronous TCPM request. Any fault takes the existing verified
 * fixed-PD handback path. A voltage change still follows the parked sequence.
 */
static int sm5440_refresh_pps_without_park(struct sm5440_direct *sm,
					  int target_mv)
{
	int mode, status3, int1, int3, ret;

	ret = sm5440_refresh_pps_current_only(sm, target_mv);
	if (ret)
		goto failed;

	mode = i2c_smbus_read_byte_data(sm->client, SM5440_REG_CNTL5);
	if (mode < 0) {
		ret = mode;
		goto failed;
	}
	status3 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_STATUS3);
	if (status3 < 0) {
		ret = status3;
		goto failed;
	}
	int1 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_INT1);
	if (int1 < 0) {
		ret = int1;
		goto failed;
	}
	int3 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_INT3);
	if (int3 < 0) {
		ret = int3;
		goto failed;
	}

	if (!(mode & SM5440_CNTL5_CHG_ON) ||
	    !(status3 & SM5440_STATUS3_VBUSPOK) ||
	    (int1 & SM5440_INT1_REFRESH_FAULTS) ||
	    (int3 & SM5440_INT3_REFRESH_FAULTS)) {
		dev_warn(sm->dev,
			 "same-point PPS refresh faulted with pump running: mode=%#x status3=%#x INT1=%#x INT3=%#x\n",
			 mode, status3, int1, int3);
		ret = -EIO;
		goto failed;
	}

	dev_dbg(sm->dev, "same-point PPS keep-alive completed without parking pump\n");
	return 0;

failed:
	/* One failed live refresh disables this optimization for the rest of the
	 * boot. The worker performs the normal fixed-PD handoff before its guarded
	 * retry, which then uses the established pump-park sequence.
	 */
	sm->live_refresh_available = false;
	dev_warn(sm->dev,
		 "disabling uninterrupted PPS refresh for this boot (%d); reverting to pump-park refreshes\n",
		 ret);
	return ret;
}

/*
 * Keep the programmable contract alive while minimizing pump interruptions.
 *
 * A PPS source re-applies its output voltage on every Request, and that step is
 * enough to drive the 2:1 pump's output above its input for an instant.  The
 * chip then latches REVBLK and clears CHG_ON by itself, so the keep-alive used
 * to end direct charging every couple of seconds and hand the battery back to
 * the 9 V switching charger until the retry.  Park it for a voltage-changing
 * request, then re-arm once the bus settles. For a same-point keep-alive, leave
 * it running and immediately check the SM5440 status/fault latches; any fault
 * fails closed through the normal fixed-PD handoff.
 */
static int sm5440_renegotiate_pps(struct sm5440_direct *sm)
{
	int target_mv, ret;

	target_mv = sm5440_next_pps_target_mv(sm);
	if (target_mv < 0)
		return target_mv;
	if (sm->live_refresh_available &&
	    !x810_sm5440_pps_voltage_change_required(target_mv,
							 sm->target_mv,
							 SM5440_PPS_V_STEP_MV))
		return sm5440_refresh_pps_without_park(sm, target_mv);

	ret = sm5440_pump_off(sm);
	if (ret)
		return ret;
	if (sm->passthrough_active) {
		ret = sm5440_pt_rearm_check(sm, "after pump park");
		if (ret)
			return ret;
	}

	ret = sm5440_refresh_pps(sm);
	if (ret)
		return ret;

	ret = sm5440_wait_vbus_settled(sm, sm->target_mv);
	if (ret)
		return ret;
	if (sm->passthrough_active) {
		ret = sm5440_pt_rearm_check(sm, "after VBUS settle");
		if (ret)
			return ret;
	}

	return sm5440_pump_on(sm);
}

static void sm5440_log_faults(struct sm5440_direct *sm)
{
	static const struct {
		u8 bit;
		bool int3;
		const char *name;
	} faults[] = {
		{ SM5440_INT1_VOUTOVP, false, "VOUT_OVP" },
		{ SM5440_INT1_VBATOVP, false, "VBAT_OVP" },
		{ SM5440_INT3_VBUSOVP, true, "VBUS_OVP" },
		{ SM5440_INT3_VBUSUVLO, true, "VBUS_UVLO" },
		{ SM5440_INT3_THEMSHDN, true, "THERMAL_SHUTDOWN" },
		{ SM5440_INT3_STUP_FAIL, true, "STARTUP_FAIL" },
		{ SM5440_INT3_REVBLK, true, "REVERSE_BLOCKING" },
		{ SM5440_INT3_CFLY_SHORT, true, "FLYING_CAP_SHORT" },
		{ }
	};
	int int1, int3;
	int i;

	/* Read-to-clear: harvest these before the next start wipes them. */
	int1 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_INT1);
	int3 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_INT3);

	dev_warn(sm->dev, "pump off: INT1=%#x INT3=%#x\n", int1, int3);
	for (i = 0; faults[i].name; i++) {
		int reg = faults[i].int3 ? int3 : int1;

		if (reg > 0 && (reg & faults[i].bit))
			dev_warn(sm->dev, "  latched fault: %s\n",
				 faults[i].name);
	}
}

/*
 * CYG1's direct-charger error-state path reads all four read-to-clear SM5440
 * interrupt registers. This standalone Fedora driver has no charger IRQ
 * handler, so CV_MAN must poll the latches itself. A failed register read or
 * any error/alarm that this worker cannot recover from forces handback.
 */
static int sm5440_pt_read_fault_latches(struct sm5440_direct *sm, u8 latches[4])
{
	int i, ret;

	for (i = 0; i < 4; i++) {
		ret = i2c_smbus_read_byte_data(sm->client, SM5440_REG_INT1 + i);
		if (ret < 0)
			return ret;
		latches[i] = ret;
	}
	return 0;
}

/* CV_MAN is a fail-closed opt-in: an I2C read error, live thermal status, or
 * any unsupported read-to-clear interrupt latch inhibits pump re-arm. Call
 * this while parked after a Request and again after ADC bus-settle so faults
 * arising during either transition cannot be erased by the next pump start.
 */
static int sm5440_pt_rearm_check(struct sm5440_direct *sm, const char *stage)
{
	u8 latches[4];
	int status2, status3, ret;

	status2 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_STATUS2);
	if (status2 < 0)
		return status2;
	status3 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_STATUS3);
	if (status3 < 0)
		return status3;
	ret = sm5440_pt_read_fault_latches(sm, latches);
	if (ret)
		return ret;
	if (x810_sm5440_pt_rearm_faulted(status2, status3,
					 latches[0], latches[1],
					 latches[2], latches[3])) {
		dev_err(sm->dev,
			"CV_MAN re-arm inhibited %s: STATUS2=%#x STATUS3=%#x INT1=%#x INT2=%#x INT3=%#x INT4=%#x\n",
			stage, status2, status3, latches[0], latches[1],
			latches[2], latches[3]);
		return -EIO;
	}

	return 0;
}

static int sm5440_restore_switching(struct sm5440_direct *sm)
{
	int ret;
	enum x810_sm5440_handoff_state handoff;

	/* Keep SM5714 ownership until all steps needed for a safe handoff pass. */
	sm->restore_pending = true;
	ret = sm5440_pump_off(sm);
	if (ret)
		goto failed;
	/* CYG1 disables REVBLK only while its CV_MAN pass-through mode owns the
	 * pump. Restore the normal protection while the pump is parked, before
	 * negotiating away from PPS or returning SM5714 ownership.
	 */
	ret = sm5440_update_bits(sm, SM5440_REG_CNTL2,
				 SM5440_CNTL2_REVBLK_EN,
				 SM5440_CNTL2_REVBLK_EN);
	if (ret)
		goto failed;
	ret = sm5440_update_bits(sm, SM5440_REG_ADCCNTL1,
				 SM5440_ADCCNTL1_ENABLE, 0);
	if (ret)
		goto failed;
	ret = sm5440_update_bits(sm, SM5440_REG_CNTL1,
				 SM5440_CNTL1_WDT_EN, 0);
	if (ret)
		goto failed;
	/*
	 * TCPM ONLINE=0 means TCPM is not reporting a current Type-C/PD charging
	 * contract; it does not prove the connector has no VBUS. The SM5714 MUIC
	 * can independently still report a 5 V DCP input in this state. Once the
	 * pump is confirmed parked, do not issue ONLINE=1 without a TCPM contract:
	 * hand ownership back to SM5714 so its normal MUIC/charger path can handle
	 * any remaining input. For live PPS, block until fixed-PD handoff completes
	 * and verify the resulting state before releasing the battery path. A read
	 * error or unexpected state is not evidence that TCPM is offline.
	 */
	ret = sm5440_restore_fixed_pd_if_online(sm, &handoff);
	if (ret)
		goto failed;
	if (handoff == X810_SM5440_HANDOFF_OFFLINE)
		dev_info(sm->dev,
			 "source offline with pump parked; skipping fixed-PD request before SM5714 handoff\n");
	ret = sm5714_battery_set_direct_charge(false);
	if (ret)
		goto failed;
	sm->restore_pending = false;
	sm->pps_request_valid = false;
	sm->active = false;
	sm->passthrough_active = false;
	sm->passthrough_reference_soc = 0;
	sm->passthrough_positive_samples = 0;
	sm->passthrough_discharge_samples = 0;
	return 0;

failed:
	dev_err(sm->dev,
		"safe switching-charger handoff failed (%d); retaining direct-charge ownership for retry\n",
		ret);
	return ret;
}

static void sm5440_put_power_supply(void *data)
{
	power_supply_put(data);
}

static int sm5440_hw_init(struct sm5440_direct *sm)
{
	int reg;
	int ret;
	int i;

	ret = i2c_smbus_write_byte_data(sm->client, SM5440_REG_CNTL1,
					 SM5440_CNTL1_SW_RESET);
	if (ret)
		return ret;
	for (i = 0; i < 255; i++) {
		usleep_range(1000, 2000);
		reg = i2c_smbus_read_byte_data(sm->client, SM5440_REG_CNTL1);
		if (reg < 0)
			return reg;
		if (!(reg & SM5440_CNTL1_SW_RESET))
			break;
	}
	if (i == 255)
		return -ETIMEDOUT;

#define SM5440_WRITE(_reg, _val) do {					\
	ret = i2c_smbus_write_byte_data(sm->client, (_reg), (_val));	\
	if (ret)							\
		return ret;						\
} while (0)

	SM5440_WRITE(SM5440_REG_CNTL1, SM5440_CNTL1_WDT_30S);
	SM5440_WRITE(SM5440_REG_CNTL2, 0xf2);
	SM5440_WRITE(SM5440_REG_CNTL3, 0xb8);
	SM5440_WRITE(SM5440_REG_CNTL4, 0xff);
	SM5440_WRITE(SM5440_REG_CNTL6, 0x09);
	/* CNTL7 (switching frequency) is programmed per charge current in
	 * sm5440_start(), the way the vendor driver does it. */
	SM5440_WRITE(SM5440_REG_VBUSCNTL, 0x07);
	/* Seed the ordinary direct-charge target. The guarded manual CV_MAN
	 * path replaces this field from the SM5440 ADC sample before requesting
	 * PPS, using CYG1's ADC_VBAT + 200 mV plus the X810 50 mV CV offset.
	 * CV_MAN remains default-off; source parity is not safety validation. */
	SM5440_WRITE(SM5440_REG_VBATCNTL,
		     ((SM5440_VBATREG_MV - 3800) * 10) / 125);
	SM5440_WRITE(SM5440_REG_VOUTCNTL, 0x3f);
	SM5440_WRITE(SM5440_REG_IBUSCNTL, SM5440_INITIAL_IBUS_MA / 50);
	SM5440_WRITE(SM5440_REG_PRTNCNTL, 0xfe);
	SM5440_WRITE(SM5440_REG_THEMCNTL1, 0x0c);
	SM5440_WRITE(SM5440_REG_ADCCNTL1,
		     SM5440_ADCCNTL1_AVG_32 |
		     SM5440_ADCCNTL1_CONTINUOUS |
		     SM5440_ADCCNTL1_ENABLE);
	SM5440_WRITE(SM5440_REG_ADCCNTL2, 0xdf);
#undef SM5440_WRITE

	/* Reading the four interrupt latches clears stale bootloader events. */
	for (i = 0; i < 4; i++) {
		ret = i2c_smbus_read_byte_data(sm->client, i);
		if (ret < 0)
			return ret;
	}

	return 0;
}

static int sm5440_start(struct sm5440_direct *sm, bool manual_pt)
{
	int battery_uv, battery_mv, target_mv, target_ma, capacity;
	int pt_vbatreg_code;
	int ret;

	/* Recheck the passive TCPM gate immediately before taking SM5714/Q4
	 * ownership; the worker's earlier eligibility sample may have gone stale.
	 */
	if (manual_pt ? !sm5440_pt_eligible(sm, false) :
			!sm5440_eligible(sm)) {
		dev_warn_ratelimited(sm->dev,
			"PPS start safety gate rejected request (manual=%u ONLINE=%d VBUS=%d mV input=%d mA SOC=%d temp=%d fast_charge=%u limit=%u)\n",
			manual_pt,
			sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE),
			sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_NOW) / 1000,
			sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_MAX) / 1000,
			sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_CAPACITY),
			sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_TEMP),
			sm5714_battery_fast_charge_enabled(),
			sm5714_battery_charge_limit_reached(
				sm5440_psy_get(sm->battery,
					      POWER_SUPPLY_PROP_CAPACITY)));
		return -EOPNOTSUPP;
	}

	battery_uv = sm5440_psy_get(sm->battery,
				    POWER_SUPPLY_PROP_VOLTAGE_NOW);
	if (battery_uv < 0)
		return battery_uv;
	if (manual_pt) {
		capacity = sm5440_psy_get(sm->battery,
					  POWER_SUPPLY_PROP_CAPACITY);
		if (capacity < 0)
			return capacity;
	}

	/*
	 * Open the SM5714 switching path while VBUS is still at its safe fixed
	 * 9 V contract.  Only then may the direct charger leave that contract
	 * for a programmable one.
	 */
	ret = manual_pt ? sm5714_battery_set_direct_passthrough(true) :
		sm5714_battery_set_direct_charge(true);
	if (ret)
		return ret;

	ret = sm5440_hw_init(sm);
	if (ret)
		goto restore;
	if (manual_pt) {
		/* Samsung's CV_MAN entry waits for the normal path to stop, selects
		 * its bypass frequency (550 kHz on this board's vendor default), then
		 * disables reverse blocking before requesting PPS.
		 */
		msleep(200);
		ret = sm5440_set_freq(sm, SM5440_FREQUENCY_BYPASS_KHZ);
		if (ret)
			goto restore;
		ret = sm5440_update_bits(sm, SM5440_REG_CNTL2,
					 SM5440_CNTL2_REVBLK_EN, 0);
		if (ret)
			goto restore;
	}

	/* Match CYG1's manual PPS path: use the SM5440 ADC after initializing
	 * its conversion engine, not a separate fuel-gauge sample, for the 2:1
	 * pump setpoint.
	 */
	battery_mv = sm5440_adc_vbat_mv(sm);
	if (battery_mv < 0) {
		ret = battery_mv;
		goto restore;
	}
	if (battery_mv < 3500 || battery_mv >= 4350) {
		ret = -ERANGE;
		goto restore;
	}
	if (manual_pt) {
		/* CYG1 uses target_vbat = ADC_VBAT + 200 mV. X810's
		 * en_vbatreg=0 adds the vendor 50 mV CV offset before encoding
		 * VBATCNTL. The fixed 4.4 V value from hw_init() is only for the
		 * ordinary direct-charge path; do not enter CV_MAN with it.
		 */
		pt_vbatreg_code = x810_sm5440_pt_vbatreg_code(battery_mv);
		if (pt_vbatreg_code < 0) {
			ret = -ERANGE;
			goto restore;
		}
		ret = sm5440_update_bits(sm, SM5440_REG_VBATCNTL, 0x3f,
					 pt_vbatreg_code);
		if (ret)
			goto restore;
		dev_info(sm->dev,
			 "CV_MAN VBAT regulation from CYG1 ADC sample: ADC=%dmV, target=%dmV, VBATCNTL=%#x\n",
			 battery_mv,
			 battery_mv + X810_SM5440_PT_VBAT_TARGET_OFFSET_MV +
				 X810_SM5440_PT_CV_OFFSET_MV,
			 pt_vbatreg_code);
	}
	ret = sm5440_negotiate_pps(sm, battery_mv * 1000,
				   &target_ma, &target_mv, manual_pt);
	if (ret)
		goto restore;

	if (!manual_pt) {
		ret = sm5440_select_freq(sm, target_ma);
		if (ret)
			goto restore;
	}

	ret = sm5440_set_ibus_limit(sm, target_ma);
	if (ret)
		goto restore;

	ret = sm5440_wait_vbus_settled(sm, target_mv);
	if (ret)
		goto restore;
	if (manual_pt) {
		ret = sm5440_pt_rearm_check(sm, "before initial pump start");
		if (ret)
			goto restore;
	}

	ret = sm5440_update_bits(sm, SM5440_REG_CNTL1,
				 SM5440_CNTL1_WDT_EN,
				 SM5440_CNTL1_WDT_EN);
	if (ret)
		goto restore;

	ret = sm5440_pump_on(sm);
	if (ret)
		goto restore;

	sm->active = true;
	sm->passthrough_active = manual_pt;
	sm->target_mv = target_mv;
	sm->target_ma = target_ma;
	sm->passthrough_positive_samples = 0;
	sm->passthrough_discharge_samples = 0;
	if (manual_pt) {
		sm->passthrough_reference_soc = capacity * 10;
		sm->passthrough_target_mv = target_mv;
	}
	dev_info(sm->dev,
		 "%s started: PPS %d mV/%d mA, ibus limit %d mA\n",
		 manual_pt ? "manual-CV pass-through" : "direct charge",
		 target_mv, target_ma,
			 x810_sm5440_ibus_limit_ma(target_ma));
	return 0;

restore:
	sm5440_restore_switching(sm);
	return ret;
}

/* Eligibility for CV_MAN pass-through. In addition to normal hardware and
 * thermal bounds, it requires PPS source capability, integer SOC strictly
 * above 20% and at/below 90%, a configured cap currently holding, and fast
 * charge disabled. While active, require the PPS contract to remain active.
 */
static bool sm5440_pt_eligible(struct sm5440_direct *sm, bool active)
{
	int capacity, online, type, temp, contract_uv, contract_ua, battery_uv;
	int apdo_min_uv, apdo_max_uv;

	if (!READ_ONCE(manual_pass_through) ||
	    sm5714_battery_fast_charge_enabled())
		return false;
	online = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	type = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_USB_TYPE);
	contract_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_NOW);
	contract_ua = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_MAX);
	if (active) {
		apdo_min_uv = sm5440_psy_get(sm->tcpm,
					     POWER_SUPPLY_PROP_VOLTAGE_MIN);
		apdo_max_uv = sm5440_psy_get(sm->tcpm,
					     POWER_SUPPLY_PROP_VOLTAGE_MAX);
	} else {
		/* ONLINE=1 is the fixed-PD entry contract. The negotiated APDO is
		 * read only after switching TCPM to PPS in sm5440_negotiate_pps().
		 */
		apdo_min_uv = 0;
		apdo_max_uv = 0;
	}
	capacity = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_CAPACITY);
	temp = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_TEMP);
	battery_uv = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_VOLTAGE_NOW);
	if ((active && (online != 2 ||
			type != POWER_SUPPLY_USB_TYPE_PD_PPS)) ||
	    (!active && !x810_pd_pps_fixed_entry_ready(
			 online, contract_uv / 1000, contract_ua / 1000,
			 SM5440_MAX_PPS_MA)) ||
	    capacity < 0 ||
	    (active && !x810_sm5440_pps_point_valid(online,
			type == POWER_SUPPLY_USB_TYPE_PD_PPS,
			apdo_min_uv, apdo_max_uv, contract_ua,
			sm->target_mv, sm->target_ma)) ||
	    !x810_sm5440_pt_soc_allowed(capacity * 10,
		active ? sm->passthrough_reference_soc : capacity * 10) ||
	    !sm5714_battery_charge_limit_reached(capacity) ||
	    temp < 100 || temp >= 420 ||
	    battery_uv < 3500000 || battery_uv >= 4350000)
		return false;
	return true;
}

static int sm5440_manual_pt_adjust(struct sm5440_direct *sm, int status2)
{
	int current_ua, min_uv, max_uv, target_mv, vbat_mv;
	enum x810_sm5440_pt_direction direction;
	int ret;

	/* CURRENT_NOW legitimately becomes negative while the battery supplies
	 * system load. Keep the property-get errno separate from that signed value.
	 */
	ret = sm5440_psy_get_value(sm->battery, POWER_SUPPLY_PROP_CURRENT_NOW,
				   &current_ua);
	if (ret)
		return ret;
	/* CYG1 CV_MAN uses the SM5440 ADC's instantaneous VBAT for loop
	 * control and the 2*VBAT+40mV lower bound. Do not derive this safety
	 * boundary from the separately filtered fuel-gauge voltage.
	 */
	vbat_mv = sm5440_adc_vbat_mv(sm);
	if (vbat_mv < 3500 || vbat_mv >= 4350)
		return vbat_mv < 0 ? vbat_mv : -ERANGE;
	sm->passthrough_positive_samples = x810_sm5440_pt_positive_count(
		sm->passthrough_positive_samples, current_ua);
	sm->passthrough_discharge_samples = x810_sm5440_pt_discharge_count(
		sm->passthrough_discharge_samples, current_ua);
	direction = x810_sm5440_pt_control_direction(current_ua,
		sm->passthrough_positive_samples,
		sm->passthrough_discharge_samples,
		status2 & SM5440_STATUS2_VBATREG,
		status2 & SM5440_STATUS2_IBUSLIM);
	if (direction == X810_SM5440_PT_HOLD)
		return 0;

	min_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MIN);
	max_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_MAX);
	if (min_uv < 0 || max_uv < min_uv)
		return -ERANGE;
	target_mv = x810_sm5440_pt_next_safe_target_mv(
		sm->target_mv, direction, DIV_ROUND_UP(min_uv, 1000),
		max_uv / 1000, vbat_mv);
	if (target_mv < 0 || target_mv > X810_PPS_MAX_MV)
		return -ERANGE;
	if (target_mv == sm->target_mv)
		return 0;

	/* The existing worker is the sole SM5440 owner. Park the pump, request the
	 * single 20 mV correction through TCPM with fresh APDO bounds, wait for the
	 * bus ADC to settle, then re-arm the pump. A failed step takes the ordinary
	 * verified fixed-PD handback path.
	 */
	sm->passthrough_target_mv = target_mv;
	ret = sm5440_renegotiate_pps(sm);
	if (ret)
		return ret;
	return 0;
}

static bool sm5440_eligible(struct sm5440_direct *sm)
{
	int capacity, online, temp, contract_uv, contract_ua, battery_uv;

	online = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_ONLINE);
	contract_uv = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_VOLTAGE_NOW);
	contract_ua = sm5440_psy_get(sm->tcpm, POWER_SUPPLY_PROP_CURRENT_MAX);
	capacity = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_CAPACITY);
	temp = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_TEMP);
	battery_uv = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_VOLTAGE_NOW);

	/* USB_TYPE describes the selected fixed contract here (PD), not the
	 * source's advertised APDO list. Use fixed 9 V as a passive candidate
	 * gate; sm5440_negotiate_pps() verifies an actual APDO with the pump parked.
	 */
	return x810_pd_pps_fixed_entry_ready(online, contract_uv / 1000,
			contract_ua / 1000, SM5440_MIN_PPS_MA) &&
	       capacity >= 5 && capacity < 90 &&
	       !sm5714_battery_charge_limit_reached(capacity) &&
	       temp >= 100 && temp < 420 &&
	       battery_uv >= 3500000 && battery_uv < 4350000;
}

/*
 * A pump that cannot hold is worse than no pump: it hands the pack back to the
 * switching charger and re-negotiates PPS over and over.  Back off exponentially
 * and then stay quiet for a while instead of cycling.
 */
static unsigned long sm5440_backoff(struct sm5440_direct *sm)
{
	if (sm->fails < SM5440_MAX_FAILS)
		sm->fails++;

	if (sm->fails >= SM5440_MAX_FAILS) {
		dev_warn(sm->dev,
			 "direct charging is not holding; retrying in %d s\n",
			 SM5440_QUIET_RETRY_MS / 1000);
		return msecs_to_jiffies(SM5440_QUIET_RETRY_MS);
	}

	return msecs_to_jiffies(min(SM5440_RETRY_MS << sm->fails,
				    SM5440_MAX_RETRY_MS));
}

/* A CV_MAN attempt that fails its runtime guard must not be auto-started again
 * after rollback. Require a new explicit administrator write to re-arm it.
 */
static void sm5440_pt_disarm(struct sm5440_direct *sm, const char *reason)
{
	if (!READ_ONCE(manual_pass_through))
		return;

	WRITE_ONCE(manual_pass_through, false);
	dev_warn(sm->dev,
		 "CV_MAN disarmed after %s; explicit re-enable required after inspection\n",
		 reason);
}

static void sm5440_work(struct work_struct *work)
{
	struct sm5440_direct *sm =
		container_of(to_delayed_work(work), struct sm5440_direct, work);
	unsigned long delay = msecs_to_jiffies(SM5440_POLL_MS);
	int capacity, die_temp, ibus, op_mode, pack_temp, status2, status3;
	u8 pt_int[4];
	int vbat, vbus;
	int ret;

	if (sm->restore_pending) {
		sm5440_restore_switching(sm);
		delay = msecs_to_jiffies(SM5440_RETRY_MS);
		goto out;
	}

	if (!sm->active) {
		bool manual_pt = READ_ONCE(manual_pass_through);

		/*
		 * Opt-in: the SM5714 switching charger owns the pack on the fixed
		 * 9 V contract until fast charging is switched on.
		 */
		if (manual_pt) {
			if (!sm5440_pt_eligible(sm, false)) {
				delay = msecs_to_jiffies(SM5440_RETRY_MS);
				goto out;
			}
		} else if (!sm5714_battery_fast_charge_enabled()) {
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
			goto out;
		} else if (!sm5440_eligible(sm)) {
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
			goto out;
		}
		ret = sm5440_start(sm, manual_pt);
		if (ret) {
			if (manual_pt)
				sm5440_pt_disarm(sm, "start failure");
			dev_warn(sm->dev, "direct-charge start failed: %d\n", ret);
			delay = sm->restore_pending ?
				msecs_to_jiffies(SM5440_RETRY_MS) :
				sm5440_backoff(sm);
			goto out;
		}
		sm->fails = 0;
		goto out;
	}

	/* A mode change is serialized through this one worker: never run the
	 * normal fast-charge and CV_MAN pass-through loops simultaneously.
	 */
	if (sm->passthrough_active != READ_ONCE(manual_pass_through)) {
		ret = sm5440_restore_switching(sm);
		if (ret)
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
		goto out;
	}
	if (sm->passthrough_active && !sm5440_pt_eligible(sm, true)) {
		dev_info(sm->dev,
			 "pass-through preflight no longer holds; returning to fixed-PD switching charger\n");
		sm5440_pt_disarm(sm, "eligibility/readback failure");
		ret = sm5440_restore_switching(sm);
		if (ret)
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
		goto out;
	}

	/* The switch can go off while the pump runs: hand the pack back. */
	if (!sm->passthrough_active &&
	    !sm5714_battery_fast_charge_enabled()) {
		sm5440_log_faults(sm);
		ret = sm5440_restore_switching(sm);
		if (ret)
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
		else {
			dev_info(sm->dev,
				 "fast charging off: back to the fixed contract\n");
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
		}
		goto out;
	}

	capacity = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_CAPACITY);
	if (!sm->passthrough_active && capacity >= 0 &&
	    sm5714_battery_charge_limit_reached(capacity)) {
		dev_info(sm->dev,
			 "charge end threshold reached at %d%%; handing battery back\n",
			 capacity);
		ret = sm5440_restore_switching(sm);
		if (ret)
			delay = msecs_to_jiffies(SM5440_RETRY_MS);
		goto out;
	}

	/*
	 * PPS sources leave the programmable contract unless the sink refreshes
	 * its Request periodically. Samsung's downstream loop does this every
	 * 2.5 seconds; without it the EP-T4510 fell back after about five seconds
	 * and the resulting VBUS step tripped REVBLK. Use an absolute deadline
	 * anchored to the last successful request: fixed worker-tick counting
	 * accumulated the park/settle and polling tails and could miss that window.
	 * This matches Samsung's 2.5-second cadence and leaves room for bounded
	 * TCPM -EAGAIN retries while avoiding the previous 2-second source-Alert
	 * collision observed in the live trace.
	 * Keep the park/refresh/settle/re-arm order below unchanged.
	 */
	if (sm->pps_request_valid &&
	    x810_sm5440_pps_refresh_due(jiffies, sm->last_pps_request,
					msecs_to_jiffies(
						X810_SM5440_PPS_REFRESH_INTERVAL_MS))) {
		ret = sm5440_renegotiate_pps(sm);
		if (ret) {
			sm5440_log_faults(sm);
			dev_warn(sm->dev, "failed to refresh PPS: %d\n", ret);
			if (sm->passthrough_active)
				sm5440_pt_disarm(sm, "PPS refresh failure");
			sm5440_restore_switching(sm);
			delay = sm->restore_pending ?
				msecs_to_jiffies(SM5440_RETRY_MS) :
				sm5440_backoff(sm);
			goto out;
		}
	}

	pack_temp = sm5440_psy_get(sm->battery, POWER_SUPPLY_PROP_TEMP);
	op_mode = i2c_smbus_read_byte_data(sm->client, SM5440_REG_CNTL5);
	status3 = i2c_smbus_read_byte_data(sm->client, SM5440_REG_STATUS3);
	status2 = sm->passthrough_active ?
		i2c_smbus_read_byte_data(sm->client, SM5440_REG_STATUS2) : 0;
	vbus = sm5440_adc_vbus_mv(sm);
	ibus = sm5440_adc_ibus_ma(sm);
	vbat = sm5440_adc_vbat_mv(sm);
	die_temp = sm5440_adc_die_temp(sm);

	if (capacity < 0 || pack_temp < 0 || op_mode < 0 || status3 < 0 ||
	    (sm->passthrough_active && status2 < 0) ||
	    vbus < 0 || ibus < 0 || vbat < 0 || die_temp < 0 ||
	    capacity >= 90 || pack_temp >= 450 ||
	    !(op_mode & SM5440_CNTL5_CHG_ON) ||
	    !(status3 & SM5440_STATUS3_VBUSPOK) ||
	    vbus > 10800 || vbat > 4450 || die_temp >= 1100) {
		dev_warn(sm->dev,
			 "stopping direct charge: cap=%d temp=%d mode=%#x "
			 "st3=%#x vbus=%d ibus=%d vbat=%d die=%d\n",
			 capacity, pack_temp, op_mode, status3,
			 vbus, ibus, vbat, die_temp);
		sm5440_log_faults(sm);
		if (sm->passthrough_active)
			sm5440_pt_disarm(sm, "telemetry or safety limit");
		sm5440_restore_switching(sm);
		delay = sm->restore_pending ?
			msecs_to_jiffies(SM5440_RETRY_MS) :
			sm5440_backoff(sm);
		goto out;
	}
	if (sm->passthrough_active) {
		ret = sm5440_pt_read_fault_latches(sm, pt_int);
		if (ret) {
			dev_err(sm->dev,
				"stopping CV_MAN: failed to read SM5440 interrupt latches (%d)\n",
				ret);
			sm5440_pt_disarm(sm, "interrupt-latch read failure");
			sm5440_restore_switching(sm);
			delay = sm->restore_pending ?
				msecs_to_jiffies(SM5440_RETRY_MS) :
				sm5440_backoff(sm);
			goto out;
		}
		if (x810_sm5440_pt_irq_faulted(pt_int[0], pt_int[1],
					       pt_int[2], pt_int[3]) ||
		    (status2 & SM5440_STATUS2_THEM_REG) ||
		    (status3 & (SM5440_STATUS3_THEMSHDN_ALM |
				SM5440_STATUS3_THEMSHDN))) {
			dev_err(sm->dev,
				"stopping CV_MAN on unsupported SM5440 fault: INT1=%#x INT2=%#x INT3=%#x INT4=%#x STATUS2=%#x STATUS3=%#x\n",
				pt_int[0], pt_int[1], pt_int[2], pt_int[3],
				status2, status3);
			sm5440_pt_disarm(sm, "SM5440 fault/alarm");
			sm5440_restore_switching(sm);
			delay = sm->restore_pending ?
				msecs_to_jiffies(SM5440_RETRY_MS) :
				sm5440_backoff(sm);
			goto out;
		}

		ret = sm5440_manual_pt_adjust(sm, status2);
		if (ret) {
			dev_warn(sm->dev,
				 "manual-CV pass-through control step failed: %d\n",
				 ret);
			/* A missing current sample, exhausted APDO correction range, or
			 * failed PPS step means this opt-in mode cannot demonstrate its
			 * no-positive-current target. Latch it off; don't automatically
			 * retry the same unsafe operating point on the next poll.
			 */
			sm5440_pt_disarm(sm, "current-control/APDO failure");
			sm5440_restore_switching(sm);
			delay = sm->restore_pending ?
				msecs_to_jiffies(SM5440_RETRY_MS) :
				sm5440_backoff(sm);
			goto out;
		}
	}

	/* Rewriting CNTL1 services the hardware watchdog. */
	ret = sm5440_update_bits(sm, SM5440_REG_CNTL1,
				 SM5440_CNTL1_WDT_EN,
				 SM5440_CNTL1_WDT_EN);
	if (ret) {
		if (sm->passthrough_active)
			sm5440_pt_disarm(sm, "watchdog service failure");
		sm5440_restore_switching(sm);
		delay = sm->restore_pending ?
			msecs_to_jiffies(SM5440_RETRY_MS) :
			sm5440_backoff(sm);
		goto out;
	}

	/* A complete, healthy poll: forget earlier failures. */
	sm->fails = 0;
	if (sm->pps_request_valid)
		delay = x810_sm5440_pps_refresh_delay(jiffies,
			sm->last_pps_request,
			msecs_to_jiffies(X810_SM5440_PPS_REFRESH_INTERVAL_MS),
			delay);

	dev_info_ratelimited(sm->dev,
			     "direct: pack=%d.%dC vbus=%dmV ibus=%dmA "
			     "vbat=%dmV die=%d.%dC\n",
			     pack_temp / 10, abs(pack_temp % 10),
			     vbus, ibus, vbat,
			     die_temp / 10, abs(die_temp % 10));
out:
	/* The PM notifier owns rescheduling while the system is suspending. */
	if (!sm->suspending)
		schedule_delayed_work(&sm->work, delay);
}

static void sm5440_cancel_work(void *data)
{
	struct sm5440_direct *sm = data;
	int ret;

	cancel_delayed_work_sync(&sm->work);
	if (sm->active || sm->restore_pending) {
		ret = sm5440_restore_switching(sm);
		if (ret)
			dev_crit(sm->dev,
				 "driver cleanup could not verify safe charger handoff (%d); switching charger remains locked out\n",
				 ret);
	}
}

static void sm5440_unregister_pm(void *data)
{
	struct sm5440_direct *sm = data;

	unregister_pm_notifier(&sm->pm_nb);
}

/*
 * The i2c adapter this charger sits on is suspended with the system, so the poll
 * must neither run nor be scheduled once the tablet is going down: a poll during
 * suspend trips the i2c core's "Transfer while suspended" warning, and a pump
 * left running would lose its PPS contract anyway because nothing refreshes it
 * while the bus is gone. Park the pump, hand the pack back to the switching
 * charger, and start polling again after suspend, hibernation or image restore.
 */
static int sm5440_pm_notify(struct notifier_block *nb, unsigned long action,
			    void *data)
{
	struct sm5440_direct *sm = container_of(nb, struct sm5440_direct, pm_nb);
	int ret;

	switch (action) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		sm->suspending = true;
		cancel_delayed_work_sync(&sm->work);
		if (sm->active || sm->restore_pending) {
			ret = sm5440_restore_switching(sm);
			if (ret) {
				/* Never suspend with a charger owner left unresolved. */
				sm->suspending = false;
				schedule_delayed_work(&sm->work,
						      msecs_to_jiffies(SM5440_POLL_MS));
				return NOTIFY_BAD;
			}
			dev_info(sm->dev,
				 "system suspending: back to the fixed contract\n");
		}
		break;
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		sm->suspending = false;
		schedule_delayed_work(&sm->work,
				      msecs_to_jiffies(SM5440_POLL_MS));
		break;
	}

	return NOTIFY_DONE;
}

static int sm5440_probe(struct i2c_client *client)
{
	struct sm5440_direct *sm;
	int id;
	int ret;

	if (!i2c_check_functionality(client->adapter,
				     I2C_FUNC_SMBUS_BYTE_DATA))
		return -EOPNOTSUPP;

	sm = devm_kzalloc(&client->dev, sizeof(*sm), GFP_KERNEL);
	if (!sm)
		return -ENOMEM;
	sm->dev = &client->dev;
	sm->client = client;
	sm->live_refresh_available = true;
	i2c_set_clientdata(client, sm);

	id = i2c_smbus_read_byte_data(client, SM5440_REG_DEVICEID);
	if (id < 0)
		return dev_err_probe(sm->dev, id, "cannot read device ID\n");
	if ((id & 0x0f) != 1)
		return dev_err_probe(sm->dev, -ENODEV,
				     "unexpected device ID %#x\n", id);

	sm->tcpm = devm_power_supply_get_by_reference(sm->dev,
						      "tcpm-power-supply");
	if (IS_ERR(sm->tcpm))
		return dev_err_probe(sm->dev, PTR_ERR(sm->tcpm),
				     "cannot get TCPM power supply\n");
	if (!sm->tcpm)
		return dev_err_probe(sm->dev, -EPROBE_DEFER,
				     "TCPM power supply is not ready\n");

	sm->battery = power_supply_get_by_name("sm5714-battery");
	if (!sm->battery)
		return dev_err_probe(sm->dev, -EPROBE_DEFER,
				     "battery power supply is not ready\n");
	ret = devm_add_action_or_reset(sm->dev, sm5440_put_power_supply,
				       sm->battery);
	if (ret)
		return ret;

	INIT_DELAYED_WORK(&sm->work, sm5440_work);
	ret = devm_add_action_or_reset(sm->dev, sm5440_cancel_work, sm);
	if (ret)
		return ret;

	sm->pm_nb.notifier_call = sm5440_pm_notify;
	ret = register_pm_notifier(&sm->pm_nb);
	if (ret)
		return dev_err_probe(sm->dev, ret,
				     "cannot register the PM notifier\n");
	ret = devm_add_action_or_reset(sm->dev, sm5440_unregister_pm, sm);
	if (ret)
		return ret;

	schedule_delayed_work(&sm->work, msecs_to_jiffies(10000));

	dev_info(sm->dev,
		 "SM5440 direct charger device ID %#x, fast charging %s (cap %u mA)\n",
		 id, sm5714_battery_fast_charge_enabled() ? "on" : "off",
		 max_pps_ma);
	return 0;
}

static const struct of_device_id sm5440_of_match[] = {
	{ .compatible = "siliconmitus,sm5440" },
	{ }
};
MODULE_DEVICE_TABLE(of, sm5440_of_match);

static struct i2c_driver sm5440_driver = {
	.driver = {
		.name = "sm5440-direct",
		.of_match_table = sm5440_of_match,
		/* Avoid an unsafe manual unbind while direct charge owns the pack. */
		.suppress_bind_attrs = true,
	},
	.probe = sm5440_probe,
};
module_i2c_driver(sm5440_driver);

MODULE_DESCRIPTION("Silicon Mitus SM5440 direct charger for Samsung SM-X710");
MODULE_LICENSE("GPL");
