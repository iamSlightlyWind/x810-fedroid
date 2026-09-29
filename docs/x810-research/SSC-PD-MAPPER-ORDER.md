# SSC discovery: `pd-mapper` must follow the late ADSP start

## Source-side change

The X810 rootfs enables `pd-mapper.service`, but ADSP firmware is reachable
only after the rootfs is mounted. The X810-specific
`gts9wifi-adsp-boot.service` adds a 25-second delay and is ordered after the
panel cold-boot recovery. `pd-mapper` needs the ADSP remoteproc to be running
and its firmware-directory `*.jsn` protection-domain maps available; if it
starts before that, it can report no maps/remoteproc and hit systemd's start
limit. An `After=` on `sensorspd` alone does not start or order `pd-mapper`.

The additive drop-in at
`rootfs/overlay/etc/systemd/system/pd-mapper.service.d/10-gts9wifi-adsp-order.conf`
makes `pd-mapper` require and follow that existing, panel-ordered ADSP unit
and retry a failed mapper start after three seconds. The stronger `Requires=`
is deliberate: the tablet's journal showed the first ADSP firmware attempts
failing while `pd-mapper` nevertheless started under the former `Wants=`
dependency, then remained running when the exact CYG1 helper succeeded later.
The local X910 ports use `Wants=`, but that did not protect this X810 sequence.

The reference ports exposed an additional packaging requirement: `pd-mapper`
needs the device's `adspr.jsn`, `adsps.jsn`, `adspua.jsn`, and `cdspr.jsn`
protection-domain maps alongside the ADSP metadata. Earlier X810 code staged
only `.mdt` files and ELF segments. The ADSP helper now copies these maps when
they exist on this tablet's read-only APNHLOS mount before starting remoteproc.
It does not substitute X910/X710 maps or make ADSP boot depend on optional
maps. Missing maps are reported explicitly because QRTR services may still
fail to publish.

The 2026-09-29 read-only live check on the installed X810 found QRTR service
400, a successful sensor-recovery oneshot, active sensorspd and
iio-sensor-proxy, and SensorProxy's `HasAccelerometer=true` plus orientation
property. The earlier missing-service state has therefore recovered on this
boot, validating SSC discovery through the running kernel/DT and userspace
stack. This does **not** establish visible GNOME auto-rotation; physically
rotating the tablet while observing the display is still required. Resume
recovery and repeatability across clean boots also remain unverified.

## Smallest read-only runtime check

After installing the update and booting Fedora, capture this before manually
restarting ADSP, `pd-mapper`, or `sensorspd`:

```sh
systemctl show pd-mapper.service gts9wifi-adsp-boot.service \
  hexagonrpcd-adsp-sensorspd.service gts9wifi-wait-sensor-proxy.service \
  -p ActiveState -p SubState -p ExecMainStatus -p NRestarts -p After -p Wants
journalctl -b -o short-monotonic -u gts9wifi-adsp-boot.service \
  -u pd-mapper.service -u hexagonrpcd-adsp-sensorspd.service \
  -u gts9wifi-wait-sensor-proxy.service
cat /sys/class/remoteproc/remoteproc0/{state,firmware}
qrtr-lookup 400
busctl --system get-property net.hadess.SensorProxy /net/hadess/SensorProxy \
  net.hadess.SensorProxy HasAccelerometer
busctl --system get-property net.hadess.SensorProxy /net/hadess/SensorProxy \
  net.hadess.SensorProxy AccelerometerOrientation
```

Expected ordering: panel recovery completes, the delayed ADSP unit starts,
then `pd-mapper` starts/retries. Confirm that all four device-local JSON maps
appear in `/usr/lib/firmware/qcom/sm8550/`. A mapper log that still reports no
maps after this ordering, or a running ADSP with no QRTR 400 service, points
beyond systemd ordering and needs the captured logs plus the active
firmware-directory/map listing for the next diagnosis.
