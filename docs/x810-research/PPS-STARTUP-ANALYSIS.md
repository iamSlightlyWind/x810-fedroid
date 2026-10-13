# SM5440 PPS startup comparison (X810 and X910)

This note separates three things which can otherwise be confused:

1. An adapter advertising a PPS APDO.
2. TCPM activating PPS and negotiating an operating point.
3. The SM5440 pump starting and staying stable on that point.

An earlier live X810 sample had a PPS APDO, and a controlled request activated
it. The attempt to return to fixed PD returned `EINVAL` and dropped TCPM to
`ONLINE=0`, while the SM5714 MUIC still saw DCP input; a read at that point
showed no partner object and only the port's own 5 V / 900 mA PDO. That was a
stale/disconnected TCPM state, not proof the adapter lacked PPS.

After a physical charger reconnect at 21:27 (+07:00), the live state recovered
to a fixed-PD contract: TCPM `ONLINE=1`, setpoint 9 V / 3 A. The connected
partner's source-capability objects now expose fixed PDOs at 5 V / 2 A, 9 V / 3
A, 12 V / 3 A, 15 V / 3 A, and 20 V / 3 A, plus a PPS APDO from 5 V to 11 V at
up to 3 A. This verifies that the current charger advertises PPS. PPS is not
active in this snapshot; the active contract is fixed 9 V / 3 A. The diagnostic
now reads partner PDOs directly and ignores the tablet's own source PDOs. It
also reads the tablet's *sink* PDOs: the currently running kernel advertises a
5–9 V / 3 A sink APDO, so the advertised source/sink capability overlap is
5–9 V / 3 A (not the source's full 5–11 V range). This is an intersection of
PDO capability data, not a TCPM active-contract getter. The current worktree
candidate DTS advertises a 5–10.5 V / 3 A sink APDO, but the tablet has not
booted that kernel: the active built-in driver's `modinfo` still reports the
older 5 A maximum and exposes no `manual_pass_through` parameter. Source-tree
capability is therefore not the same as the running kernel's capability.

Linux v7.2 TCPM uses `ONLINE=1` for fixed PD and `ONLINE=2` for active PPS.
The `usb_type` value is a partner/source-capability classification: in this
capture, the partner PDOs include the 5–11 V PPS APDO, which explains
`PD_PPS`. It indicates PPS capability, not that the active contract is PPS.
Here `ONLINE=1` and 9 V min/max identify the active fixed-PD contract. When
PPS is active (`ONLINE=2`), Linux 7.2 TCPM's `VOLTAGE_MIN/MAX` and
`CURRENT_MAX` getters expose the selected *source APDO* limits, not the
source/sink intersection. The X810 driver candidate separately clamps its
requested operating point to the board/sink APDO and SM5440 limits. The
diagnostic reads the partner's source-capability PDOs directly, so these
capability and active-contract facts are not conflated. Do not repeat the
manual PPS-offline transition until the fixed-contract handoff problem is
understood; the physical reconnect restored PD without a reboot or software
controller reset.

### Short live PPS-only trial (2026-10-04, no charge-pump activation)

With the active charge limit holding at 89% and `fast_charge=0`, I briefly
requested TCPM `ONLINE=2` from its existing fixed 9 V / 3 A contract. The
read-back was `ONLINE=2`, active voltage 9 V, APDO range 5–11 V and current
limit 3 A. This proves the source and TCPM can negotiate PPS; it did not test
the SM5440 pump or bypass.

The request to return with `ONLINE=1` returned `EINVAL`. TCPM then logged
repeated 5 V / 3 A updates followed by `0 V / 0 mA`; current read-back is
`ONLINE=0`, while the separate SM5714 MUIC still classifies the input as DCP.
Read-only Type-C state afterward remained sink/device with a 3 A Type-C
operation mode, but no Type-C partner object was present. Thus `ONLINE=0`
means TCPM is not reporting an active Type-C/PD charging contract; it does not
prove that physical VBUS has disappeared. `fast_charge` remained off and the
battery remains `Not charging` under the active cap. This trial exposed a real
rollback edge case and disturbed the PD contract. The current read-only
interfaces show no way to request a fresh attach/PD negotiation; a physical
charger disconnect/reconnect is the available recovery action without changing
hardware state. Do not repeat this PPS toggle as an experiment.

The candidate handoff helper now always reads TCPM after a fixed-PD request,
even if the request returns an error. It accepts only verified fixed-PD or
TCPM-offline read-back, and still fails closed if PPS remains active or the
state is unknown. TCPM-offline is distinct from physical input absent: the
SM5714 can independently read `STATUS1.VBUS_POK` when the direct owner is
released; if input remains, it reconfigures the switching charger using its
MUIC classification, and it retains direct ownership if the read/configuration
fails. Thus `ONLINE=0` is not treated as proof that VBUS disappeared. This
recovery path does not measure analog VBUS voltage, and it depends on TCPM
clearing its cached contract and the MUIC classifying any surviving input
correctly. The attempted PPS-to-fixed failure itself remains unexplained and
should not be retried until an instrumented candidate can be tested. The
candidate now logs the request result and TCPM ONLINE/VBUS/current before and
after the handback; this is covered by offline C policy/source-contract tests
but has not been installed or hardware-validated.

### Current charger snapshot (2026-10-04 22:23 +07:00)

After the user's physical charger reconnect, a fresh read-only capture again
shows an attached PPS source but a fixed-PD operating point: source APDO
5–11 V / 3 A, running-kernel sink APDO 5–9 V / 3 A (advertised capability
overlap 5–9 V / 3 A), TCPM `ONLINE=1` at fixed 9 V / 3 A. Three further
samples over 30 seconds remained at fixed 9 V / 3 A. The SM5714 `fast_charge`
switch is off, battery capacity
is 86%, temperature is 36.5 °C, and status is `Not charging`. Fuel-gauge
`CURRENT_NOW` samples were -6 to -9 mA; that is not an independent measurement
of zero battery current and does not demonstrate pass-through. The live
`sm5440_direct` module is older than the current worktree: it exposes only
`max_pps_ma`, not the worktree's `manual_pass_through` parameter. Therefore no
candidate pass-through or new handback diagnostics are active on-device. No
software negotiation, register write, reset, or charger mode change was
performed during this snapshot.

The charger/source capability check is positive, but this is not an active PPS
contract: the adapter advertises a 5–11 V / 3 A APDO and the running tablet
kernel advertises a 5–9 V / 3 A sink APDO (capability overlap 5–9 V / 3 A),
while `ONLINE=1` and equal 9 V min/max report fixed PD. The live normal
direct-charge path is gated off by `fast_charge=0`; that likely explains why a
capable source is not currently running PPS. Do not manually write TCPM
`ONLINE=2`: the earlier experimental request/rollback disturbed the PD
contract, and the currently running module lacks the new one-attempt
handback diagnostics.

### Read-only normal-PPS eligibility diagnosis (2026-10-04)

A fresh invocation of `tools/diagnose-x810-usbc.py --no-commands
--no-kernel-log` read the live sysfs state without requesting a contract or
writing any control. It confirmed `ONLINE=1` (fixed PD) at 9 V / 3 A, source
and sink PPS APDO capabilities with a 5–9 V / 3 A overlap (not the active
contract range), battery SOC 86%, battery
temperature 36.3 °C, and battery voltage 4.201 V. The SM5714 `fast_charge`
switch reads `0`; charge-limit start/end thresholds are 70% / 80%, so the
current 86% SOC is above the end threshold. These are two independent runtime
reasons that the ordinary SM5440 PPS worker is not eligible: fast charging is
disabled and the charge-limit policy is holding. This is distinct from a
source/APDO/build failure. The installed `sm5440_direct` exposes only
`max_pps_ma=3000`; the worktree's experimental `manual_pass_through` parameter
is not installed. No charger state was changed. The diagnostic now reports
those gates and fails closed when the hidden charge-limit hysteresis latch
cannot be inferred from the exported start/end thresholds.

### Fresh read-only snapshot (2026-10-04 22:54 +07:00)

The tablet's USB-C diagnostic was streamed and run remotely without enabling
fast charge or changing any sysfs setting. The connected adapter advertises
fixed PDOs at 5 V/2 A, 9 V/3 A, 12 V/3 A, 15 V/3 A and 20 V/3 A, plus a PPS
APDO of 5–11 V/3 A. The running kernel advertises a 5–9 V/3 A sink APDO, so
the advertised capability overlap is 5–9 V/3 A. TCPM is `ONLINE=1` at fixed
9 V/3 A; PPS is available but not active. The battery reads 86%, 36.2 °C,
`Not charging`, and -3 mA; start/end thresholds are 70/80%, and the SM5714
fast-charge gate reads 0. The diagnostic therefore correctly reports both
`fast_charge_switch_not_enabled` and `charge_limit_already_reached` as reasons
ordinary direct-charge PPS cannot start now. No PPS request, pump enable,
charger-state change, or reboot was performed. This is capability evidence,
not proof that the worktree's not-yet-installed 5–10.5 V sink-APDO candidate
or its revised startup margin is stable on the hardware.

### User-confirmed charger reconnect (2026-10-04 23:05 +07:00)

After the user confirmed physically disconnecting/reconnecting the charger, a
second read-only capture observed a fresh fixed-PD attach sequence at 23:04:56
(`0 mV`, followed by the standard 5 V negotiation and 9 V / 3 A contract).
The partner still advertises its 5–11 V / 3 A PPS APDO, while the currently
running kernel still advertises only 5–9 V / 3 A. TCPM is `ONLINE=1`,
`VOLTAGE_MIN=VOLTAGE_MAX=9 V`: this is fixed PD, not an active PPS contract.
At 86% SOC, above the configured 80% stop threshold, and with
`fast_charge=0`, the normal SM5440 PPS path remains intentionally ineligible;
no PPS request was made during this snapshot. This confirms PD recovery and
the charger's PPS advertisement after the physical reconnect, but not PPS pump
stability or CV_MAN bypass.

## X810 evidence and code paths

The 2026-10-04 normal direct-charge test negotiated PPS at 9360–9380 mV and
1800 mA. In the second attempt, the SM5440 ADC showed VBUS falling from 9219 mV
to 8803 mV when IBUS reached 1150 mA; INT3 latched `REVERSE_BLOCKING` and the
driver returned to fixed PD. The test predates the current 525 mV extra-margin
change; it is not a test of that newer setpoint.

Normal direct charging uses a computed target:

```
2 * VBAT + ceil(IBUS * R_TTL) + 525 mV
```

For the logged 4310 mV / 1800 mA example and 320 mΩ board estimate, the code
computes 9740 mV. That is an arithmetic result only. It does not model the
adapter's voltage ramp, cable drop, pump inrush, or guarantee that REVBLK will
not recur. The regression test checks the formula and its board-voltage bound;
it deliberately does not claim that the margin clears a hardware transient.

The X810 manual CV pass-through path is different: CYG1's Samsung sequence
stops the normal pump, waits 200 ms, selects 550 kHz, disables REVBLK, obtains
the APDO, and starts CV_MAN at a target of `2 * ADC_VBAT + 80 mV`. The Fedora
candidate follows those mode-specific choices and additionally waits for its
VBUS ADC before enabling the pump. Therefore the 525 mV normal-charge margin
must not be substituted into CV_MAN without a new electrical rationale; doing
so would depart from the exact X810 vendor startup point.

## X910 reference and transfer limits

The local postmarketOS X910 mainline driver
(`postmarketos-galaxy-tab-s9-ultra/pmaports/device/testing/linux-samsung-gts9uwifi-mainline/sm5440_direct.c`)
starts **normal direct charging**, not Samsung's X810 CV_MAN path, with
`2 * VBAT + 700 mV`, a 2 A initial PPS current capped at 2.2 A, and up to 1 s
of VBUS polling at `target - 500 mV` before enabling the pump. Its source
comment says earlier pump startup during the adapter's still-9 V ramp collapsed
VBUS and latched REVBLK. This supports two general practices already used by
the X810 candidate—bounded startup current and waiting on the charger ADC—but
the X910 constants are board/mode-specific, not validated X810 settings.

Samsung's X810 CV_MAN controller is the more relevant comparison for the
requested no-charge pass-through mode. It disables REVBLK only for CV_MAN,
uses its own +80 mV initial point, and periodically adjusts PPS in 20 mV steps.
The Fedora candidate keeps REVBLK disabled only while CV_MAN owns the pump,
then parks the pump and restores the protection before fixed-PD handback.

## What can be changed safely offline

No additional voltage/current tuning is justified by the available evidence.
Changing the CV_MAN target to X910's +700 mV could increase load on the adapter
and would diverge from Samsung's X810 implementation. Raising normal-charge
headroom further might avoid one particular droop, but could instead exceed
the source/APDO and board voltage bounds at high battery voltage. Linux 7.2
TCPM reports the selected source APDO bounds while PPS is active; it does not
return the source/sink intersection through those getters. The candidate clamps
operating requests to the X810's 5–10.5 V / 3 A board/sink and pump envelope.
A 5–11 V / 3 A source therefore yields a driver-bounded 5–10.5 V / 3 A
candidate request range; the wider source range is not grounds for rejecting a
valid overlapping APDO.

The only offline correction made alongside this audit is to the PD/PPS test's
claim: it now asserts the candidate's computed setpoint exactly, while stating
explicitly that static arithmetic cannot prove the pump will survive startup.

### Fresh live read-only snapshot after charger reconnect (2026-10-05 00:07 +07:00)

`python3 tools/diagnose-x810-usbc.py --no-commands --no-kernel-log` was run
locally on the tablet after the user physically reconnected the charger. It
performed no sysfs writes, raw I2C access, PD requests, or reboot. The adapter
advertises fixed PDOs at 5 V/2 A, 9 V/3 A, 12 V/3 A, 15 V/3 A, and 20 V/3 A,
plus a 5–11 V/3 A PPS APDO. The **installed** kernel advertises a 5–9 V/3 A
PPS sink APDO. The active contract remains fixed PD at exactly 9 V/3 A:
TCPM `ONLINE=1`, `VOLTAGE_MIN=VOLTAGE_MAX=9 V`. `usb_type` includes the
bracketed `PD_PPS` capability label, but that is not proof of active PPS.

The same read-only snapshot reports 86% SOC, 33.9 °C battery temperature,
70%/80% charge-limit thresholds, `fast_charge=0`, battery `Not charging`, and
about -5 mA battery current. The normal direct-charge PPS worker is therefore
blocked by both `fast_charge_switch_not_enabled` and
`charge_limit_already_reached`; no pump/PPS attempt was made. The near-zero
battery current at this idle, charge-limited point does **not** demonstrate
pass-through: it is not a loaded test and the experimental CV_MAN kernel is
not installed. This is evidence that fixed-PD negotiation recovers after the
reconnect, not evidence that PPS or battery bypass is operating.
The manual CV_MAN and PD-limit policy tests pass on the devnode. Neither test
proves adapter handoff, zero battery current, safe long-term operation, or
successful live PPS pass-through. Those still require a controlled,
instrumented on-device test; no activation is performed by this audit.

### Follow-up read-only snapshot (2026-10-05 00:25 +07:00)

A later read-only capture still shows source/sink PPS capability overlap
5–9 V / 3 A and fixed PD at 9 V / 3 A (`ONLINE=1`). SOC is 86%, temperature
35.0 °C, charge thresholds remain 70%/80%, `fast_charge=0`, status is `Not
charging`, and the pack reports -1.37 A at that instant. No charger controls
were changed. The diagnostic now exposes the ordinary-charge and CV_MAN
software preflights separately: normal direct-charge PPS is blocked by the
disabled fast-charge switch and held 80% cap; CV_MAN has different gates, but
the installed kernel does not expose its candidate opt-in parameter.

The fresh research ZIP
`out/research/x810-pps-cvman-default-off/update.zip` is a validated kernel/boot
candidate, not installed and not a release. Flashing it alone does not start
PPS: ordinary PPS still needs a deliberate charging-permitted state, and the
candidate's manual pass-through parameter remains default-off. CV_MAN's
independent protection semantics remain unverified, so it must not be enabled
just to test whether the contract negotiates.

### Current live link (2026-10-06 10:32 +07:00)

A read-only `diagnose-x810-usbc.py` capture over the tablet's USB-tether SSH link
shows 5 V / 500 mA, no Type-C PD partner object, no source PDOs, and no sink
PPS APDO on the running 7.2.0 kernel. Battery SOC is 97%, the configured stop
threshold is 80%, and `fast_charge=0`. This is the USB tether/current running
kernel state, not a test with the PPS charger attached. It is ineligible for
normal SM5440 PPS and cannot validate the candidate. No charger controls,
registers, partitions, or boot state were changed during this capture.

Offline candidate checks (2026-10-06): the X810 `sm5440_direct.o` and
`sm5714_battery.o` sources compile against the cached Linux 7.2 X810 tree, and
the X810 DTB compiles. The DTB build emits the existing `sm8550.dtsi`
`#interrupt-cells` warning; no PPS-driver compile errors. PD/PPS limit,
refresh-timing, and read-only diagnostic tests pass. These checks do not replace
an attached-source PPS/pump test. The experimental CV_MAN pass-through remains
default-off and unvalidated.

### Live normal-PPS confirmation (2026-10-13)

On the tablet over SSH, the connected PPS-capable adapter was initially at
fixed PD `ONLINE=1`, 9 V / 3 A. The battery was at 26%, 33.8 C, with the
configured 70%/80% charge thresholds; the `fast_charge` sysfs control was 0.
That switch being off, rather than an inability to negotiate, was the reason
the normal SM5440 PPS path was idle. For a short diagnostic only, I enabled
`fast_charge` for approximately 25 seconds, then restored it to 0 and verified
TCPM returned to fixed 9 V / 3 A (`ONLINE=1`). No image, partition, persistent
configuration, or CV_MAN setting was changed.

The installed 7.2.0 kernel immediately reached `ONLINE=2` and logged a normal
direct-charge PPS request at 8,880 mV / 3,000 mA. During the brief run, SM5440
ADC samples showed about 8.09-8.17 V and 1.9-2.1 A while active; the battery
reported roughly 4.0-4.3 A charge current. Its temperature rose from 33.8 C to
34.2 C and the pump die samples were 45.5-50.5 C. Periodic zero-IBUS samples
coincided with the driver's pump-parking/PPS-refresh cycle. No SM5440 fault
latches were reported when the switch was returned off. This proves that this
adapter and the installed X810 kernel can establish an active PPS contract and
run the normal direct-charge pump for a short test. It does not prove long-run
stability, and does not validate CV_MAN/battery bypass.

The current source-tree candidate raises normal PPS startup headroom to 525 mV
and advertises the X810 sink range through 10.5 V, with source/board intersection
checks. For the live 3,908 mV battery sample and configured 3 A request, its
formula requests 9,320 mV rather than the installed kernel's 8,880 mV point.
The arithmetic and refresh/handoff policies pass offline tests, but that
candidate is not the image tested above and has not yet had an on-device
longer-duration run. The 10.5 V / 3 A sink limit remains capped below the
SM5440's 11 V OVP; do not raise it based on source-adapter capabilities alone.

### Live PPS-capable power-bank confirmation (2026-10-13, 20:36 +07:00)

With the PPS power bank attached, its source advertised a PPS APDO and the
tablet was at fixed PD 9 V / 3 A. At 81% SOC, the saved 70%/80% charge-limit
window correctly held charging off, so normal PPS did not start. For a bounded
test only, the thresholds were temporarily changed to 82%/90% and then restored
to 70%/80%; no reboot or persistent configuration change was made.

The running `7.2.0-gts9wifi #6.x810-ppsdiag` kernel entered active PPS
(`ONLINE=2`) at 9.68-9.78 V / 2.2 A, started the SM5440 direct-charge pump,
and reported battery charging current around 2.6-3.2 A. During the observed
run, pack temperature was about 33.4-34.0 C and pump-die samples peaked around
45.5 C. The regular zero-IBUS readings were interspersed with pump-on samples
and PPS refreshes; no error or fault was observed in this interval. After
restoring the original 70%/80% limit, charging stopped and TCPM returned to
fixed PD 9 V / 3 A. This confirms the tested kernel can negotiate and operate
normal PPS with this power bank; it does not validate manual CV_MAN pass-through
or battery bypass, which remain opt-in and unvalidated.
