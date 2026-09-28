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
makes `pd-mapper` want and follow that existing, panel-ordered ADSP unit and
retry a failed mapper start after three seconds. This matches the local
X910 Ubuntu drop-in and the X910 pmOS drop-in, adjusted to X810 unit names.
The pinned firmware payload includes the ADSP metadata and its PD JSON
maps, so this is an ordering correction rather than a substitute for missing
firmware.

This is a static/source-supported fix for a concrete startup race, **not yet
validated on the X810 tablet**. It cannot by itself prove the cause of missing
SSC service 400 or working auto-rotation. The kernel image/DT and runtime
evidence are still needed to verify the QRTR service and sensor discovery.

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
```

Expected ordering: panel recovery completes, the delayed ADSP unit starts,
then `pd-mapper` starts/retries with the firmware JSON maps present. A mapper
log that still reports no maps after this ordering, or a running ADSP with no
QRTR 400 service, points beyond systemd ordering and needs the captured logs
plus the active firmware-directory/map listing for the next diagnosis.
