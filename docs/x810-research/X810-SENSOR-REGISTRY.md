# X810 stock sensor-registry staging

## Runtime data flow

The image no longer ships the donor X710 sensor JSON or its generated
registry. The sensor startup helper composes a private tree under `/run` from
the running tablet's Android partitions:

| HexagonFS root path | Runtime source |
| --- | --- |
| `sensors/config/*.json` | `/vendor/etc/sensors/config/*.json` |
| `sensors/sns_reg.conf` | `/vendor/etc/sensors/sns_reg_config` |
| `sensors/registry/*` | `/mnt/vendor/persist/sensors/registry/registry/*` |
| `sensors/sns_reg_version` | `/mnt/vendor/persist/sensors/registry/sns_reg_version` |
| `socinfo/*` | `/sys/devices/soc0/*` where exported, otherwise the target's read-only TWRP-captured values below |
| `dsp/adsp/*` | shared DSP skeleton files from the firmware asset |

The stock vendor `sns_reg_config` writes the registry at
`/mnt/vendor/persist/sensors/registry/registry`. The patched `hexagonrpcd`
maps virtual `/persist/sensors/registry` to `<-R>/sensors/`, so its nested
`registry/` output lands at `<-R>/sensors/registry/registry`; the companion
`sns_reg_version` path is `<-R>/sensors/sns_reg_version`. This is why the
runtime composer preserves the two source directory levels as shown above.

`gts9wifi-sensor-registry-perms.service` has
`RequiresMountsFor=/vendor /mnt/vendor/persist`, and is ordered before
`hexagonrpcd-adsp-sensorspd.service`. The latter requires and follows the
staging unit, then starts `hexagonrpcd` with
`-R /run/gts9wifi-hexagonfs`. Mounts are already enabled by the rootfs image.
The helper copies into a temporary `/run` directory, chowns that private copy
to `fastrpc`, and atomically switches a symlink only after validation. It
never changes Android vendor or persist files. Any missing marker, version,
config, cache entry, or cache timestamp mismatch fails closed and prevents
this sensorspd launch rather than falling back to sibling-model data.

## Why runtime composition is model-safe

The read-only CYG1 inspection on the owner's tablet found 35 stock sensor
JSONs in `/vendor/etc/sensors/config`, and 167 files in the persist registry
output. The stock `sns_reg_config` cache lists those JSON paths with mtime
`1640995200`; all 35 vendor JSONs have that same mtime. The persist
`sns_reg_version` is `version=6\0`, and its registry output contains both the
zero-byte `sensors_registry` completion marker and non-empty cache JSON.
Those observations support staging the exact vendor/config and same-tablet
persist registry together. The helper verifies that relationship at every
boot, preserving mtimes with `copy2` and refusing stale or incomplete input.

No CYG1 sensor JSON, calibration, or generated registry bytes are embedded in
this Fedora source tree or release. Users need an initialized stock persist
registry on the tablet; if absent or stale, boot stock Android once so its
sensor service can initialize/update persist, then retry Fedora.

## SoC selector values and confidence

The stock config requests the five Android-era `socinfo` names
`hw_platform`, `platform_subtype`, `platform_subtype_id`, `platform_version`,
and `soc_id`. Mainline `qcom-socinfo` does not expose all of these Samsung
aliases. On this target, read-only TWRP inspection captured the exact values
for **SM-X816B / gts9p**: `hw_platform=MTP`, `soc_id=519`,
`platform_subtype=Unknown`, `platform_subtype_id=0`, and
`platform_version=65536`. When Fedora sysfs provides a non-empty value it
takes precedence; otherwise the helper uses this captured tuple.

The captured `MTP` and `519` values agree with the checked-in X810 DTS
(`qcom,kalama-mtp`; MSM ID `0x207` = 519) and the CYG1 Kailua sensor selector
domain (MTP and SOC IDs 519/536). The exact tuple is now based on read-only
TWRP data, not inference from the sibling Ultra reference. The copied
completed persist registry is still required and its config-file mtime cache
must match; the helper does not accept or ship a sibling model's registry as
a substitute.

## Validation status

Static tests exercise the exact composition, mtime preservation, sibling
config exclusion, read-only source behavior, cache/marker/version rejection,
and the missing-sysfs selector fallback against the TWRP-captured tuple.
Build/ordering tests verify that
vendor and persist mounts precede staging and that sensorspd consumes the
published `/run` path. This does not yet prove successful live sensor
enumeration; a device boot with the rebuilt support package is still needed
to validate the firmware's registry RPC against the mounted CYG1 files.
