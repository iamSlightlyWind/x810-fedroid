# X810 intermittent camera recognition

## Finding

There is not yet enough X810 runtime evidence to justify a kernel, udev, or
systemd recovery change. One concrete boot-only failure mode is present in
the driver: `hi1337_probe()` powers each sensor and reads its identity once;
if that initial I2C read fails it returns an error, and there is no board-level
retry or later reprobe. A transient rail/CCI/clock startup fault could
therefore leave one sensor unbound for the rest of that boot. More precisely,
an ordinary I2C identity error is not itself a deferred-probe request; resource
acquisition can still return `-EPROBE_DEFER`, which the driver core handles
separately. This is a source-supported hypothesis, not evidence that it has
happened on the tablet; automatic power cycling could also disturb the front
camera's shared camera/audio GPIO, so no retry was added without the failing
boot log.

The production X810 source describes only the SM-X810's rear and front HI1337
modules (`CCI0/I2C1` and `CCI1/I2C1`, both at 7-bit address `0x21`). The local
X710 Fedora baseline carries the same HI1337 driver and camera-node declarations;
its broader DTS is still an X710 board description, not an X810 DT to copy
wholesale. The front-address sweep and camera-notifier failure semantics are
documented in `docs/Hardware-Notes.md`. In particular, a missing sensor binding
can leave the CAMSS async notifier incomplete, which means this can present as
the *entire* camera stack disappearing, not merely one camera.

## Reference audit

- `/home/slightlywind/Repositories/gts9wifi-fedora-linux` is the S9 Wi-Fi
  baseline. Its `hi1337_gts9u.c` and `sm8550-samsung-gts9wifi.dts` camera
  camera sections match the current X810 port's driver and two-camera sensor
  declarations, but its broader DTS is for SM-X710. The Fedora baseline has no
  camera-specific systemd
  service or recovery/reprobe unit. It has one udev rule that sets the rear
  DW9808 lens to a fixed focus position on video-node add/change; that is not
  an enumeration fix.
- `/home/slightlywind/Repositories/ubuntu-galaxy-tab-s9-ultra` contains a
  substantial camera implementation, but it is for the X910 Ultra. Its
  `docs/camera-focus-and-enumeration.md` describes stable V4L2 IDs added to
  avoid a separate OBS crash when `/dev/v4l/by-id` was absent; that is a
  userspace naming workaround, not a sensor/CAMSS reprobe. Its hardware-status
  notes report all four Ultra cameras tested after cold boot, useful as a
  validation method but not evidence for X810 wiring or supplies.
- `/home/slightlywind/Repositories/postmarketos-galaxy-tab-s9-ultra/README.md`
  explicitly says camera bring-up was not started on that X910 port, so it
  provides no camera detection fix to transplant.
- `/home/slightlywind/Repositories/ubuntu-touch-galaxy-tab-s9-ultra/README.md`
  also marks cameras not started; its Android camera-provider manifest is not
  a Linux V4L2/CAMSS fix.

## Reproducible diagnostic

`tools/diagnose-x810-camera.sh` collects a read-only snapshot without streaming
or capturing images, changing hardware, restarting services, or rebinding
I2C/V4L2 drivers. It records the live firmware-tree model and expected rear /
front HI1337 compatible nodes (including `status` and raw `reg`) separately
from instantiated I2C clients. That distinction helps separate a disabled or
missing DT node from an enabled node whose I2C client did not appear. It also
records CCI/I2C adapter and sensor bindings, kernel warnings/errors when the
journal is readable, and three lines of context around camera/CCI/probe
messages. If the desktop user cannot read the kernel journal, the script tries
the readable `dmesg` ring buffer (which may not retain the full boot) before
asking for privileged journal output. It also runs `cam -l` with
libcamera's documented `LIBCAMERA_LOG_LEVELS=*:DEBUG` setting and saves the
debug log separately beside the snapshot. Run it as the Fedora desktop user
as soon as the failure is noticed and once on a working boot; avoid opening
or restarting camera apps first. Keep each output separately, then compare:

```sh
tools/diagnose-x810-camera.sh working
# On a later boot, while recognition is missing and before restarting anything:
tools/diagnose-x810-camera.sh missing
diff -u ~/x810-camera-diagnostics/working.txt \
        ~/x810-camera-diagnostics/missing.txt
```

The companion `working-libcamera.log` and `missing-libcamera.log` files are
part of the same evidence set. The snapshot records the boot ID and uptime,
live DT model/nodes, user/video-group membership, CCI and sensor/actuator
binding, V4L2/media node names and permissions, media graph, PipeWire/WirePlumber
status and relevant kernel messages. No camera frames are saved. If neither
the kernel journal nor `dmesg` is readable, attach the read-only output of
`sudo journalctl -b -k -o short-monotonic -p warning..alert` from that same
boot.

Interpret the first divergence, not just the desktop symptom:

| Evidence during failure | Likely layer to investigate next |
|---|---|
| Expected HI1337 compatible is missing/disabled in the live DT, or its `reg` differs from `00000021` | Active firmware DT / overlay selection; do not infer an I2C probe race until this agrees with the X810 source DTS. |
| Expected DT node is present/enabled and its CCI adapter exists, but the I2C client is absent | I2C child enumeration, adapter/CCI state, or the active firmware DT; an ordinary sensor-probe failure normally leaves a client visible but unbound. |
| Expected DT node and client exist, but the client has no `driver` symlink; kernel shows `identity reads failed`, `failed to power module`, regulator/clock failure, or deferred probe | X810 sensor probe/power/CCI path. The current driver attempts identity once; only add a bounded retry if the failing log shows a transient identification/power-up error rather than a permanent wiring/supply issue. |
| Both sensors bound, but CAMSS/media graph is missing or notifier never completes | DTS graph/async notifier or a missing required subdevice |
| Media/V4L2 graph exists but `cam -l` lists no cameras | libcamera pipeline/graph or access issue |
| `cam -l` sees cameras, but PipeWire `Video` has none | PipeWire libcamera SPA monitor/session startup or access |
| PipeWire and libcamera both see cameras, but GNOME app does not | portal/client/session issue, not kernel recognition |
| Device nodes exist but desktop user cannot read them | udev ownership/group/session membership |

For a boot-only failure, collect one snapshot as soon as GNOME is ready. If it
shows no camera, wait one minute and collect another labeled `missing-plus-60s`
before opening Snapshot. This separates a failed one-shot sensor probe from a
late userspace enumeration. A working and failing boot should be compared
before proposing a recovery change. The X810 camera drivers and CAMSS are
built-in; the only camera-specific udev rule sets DW9808 focus after its
V4L2 node appears. The local X710 Fedora reference has the same driver/DTS
approach and no discovery-retry service; the X910 Ubuntu camera relay fixes
userspace naming/streaming, not sensor probing. Neither supports importing a
blind systemd reprobe on X810.
