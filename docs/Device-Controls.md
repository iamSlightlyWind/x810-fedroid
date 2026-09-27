# Device controls

Two tablet settings live in the kernel and are exposed by a small helper,
`/usr/libexec/gts9wifi-device-control`. The GNOME extension's settings page is built from
that helper's table, so the GUI and a terminal drive exactly the same switches.

| Control | Group | What it does | Default |
|---|---|---|---|
| Double tap to wake | Touchscreen | Wake the tablet from suspend by double tapping the screen | On |
| Fast charging | Power | Charges the battery faster by drawing more power from your charger. A 5 A USB-C cable is recommended | Off |

## From GNOME

Open the Extensions app, find **gnome-gts9wifi** and press its settings button, or run:

```
gnome-extensions prefs gnome-gts9wifi@tabs9linux
```

Both switches are on that page. If a switch snaps back, the write was rejected — the rootfs on the tablet
may predate the control.

## From a terminal

```
gts9wifi-device-control list                 # every control, its label and its current value
gts9wifi-device-control get fast-charge
gts9wifi-device-control set fast-charge 1    # on
gts9wifi-device-control set fast-charge 0    # off
gts9wifi-device-control apply                # re-apply every saved value
```

Values are `0` or `1` only. `set` also saves the value under `/var/lib/gts9wifi/`, and
`gts9wifi-device-control.service` re-applies it at every boot, so what you set survives a
reboot. Writing the kernel file directly (below) does **not** survive a reboot.

The helper needs write access to the file it drives. Those files belong to group `video` and
are mode `0664`, so a user in `video` can set them without `sudo`; otherwise prefix the
command with `sudo`.

## The files behind the switches

| Control | Kernel file |
|---|---|
| Double tap to wake | `/sys/bus/i2c/devices/10-0049/double_tap_to_wake` |
| Fast charging | `/sys/bus/i2c/devices/6-0049/fast_charge` |

So without GNOME, and without the helper, the same switches are:

```
echo 1 | sudo tee /sys/bus/i2c/devices/10-0049/double_tap_to_wake   # double tap to wake on
echo 0 | sudo tee /sys/bus/i2c/devices/10-0049/double_tap_to_wake   # off
echo 1 | sudo tee /sys/bus/i2c/devices/6-0049/fast_charge           # fast charging on
echo 0 | sudo tee /sys/bus/i2c/devices/6-0049/fast_charge           # off
```

These are lost at the next boot unless the value is also saved under
`/var/lib/gts9wifi/` — which is what `gts9wifi-device-control set` does.

## Fast charging in detail

- **Off (default)** keeps the stock behaviour: the SM5714 switching charger on the tablet's
  fixed 9 V USB-PD contract, about 15 W from the adapter.
- **On** hands the job to the SM5440 2:1 charge pump on a PPS contract. It roughly doubles
  the current going into the battery — measured on this port, about 23 W drawn from the
  adapter and 3.0–3.2 A into the pack, where the stock path managed about 2.1 A at the same
  state of charge.
- It needs a charger that supports **PPS** (Samsung's own fast chargers do), and a **5 A
  USB-C cable** for anything above 3 A. The tablet cannot read a cable's rating, so it stays
  at the spec-safe 3 A default limit. A root-only kernel parameter can set a lower or higher
  cap up to 5 A; requests are rounded down to the USB-PD PPS 50 mA current increment, and
  the driver probes the source in compatible 250 mA steps. Do not raise the cap without a
  PPS-capable adapter and a known 5 A e-marked cable.
- Fast charging only applies at lower charge. The pump will not start above roughly 80 %
  (4.35 V) and stops at 90 %, so near full you may see the switch on while the tablet charges
  at the ordinary rate — the pump's own limits, not a fault.
- Turning it off while the pump is active sets the request immediately; the SM5440 worker
  notices on its one-second poll, reads back the SM5440 operating mode to verify pump shutdown,
  requests the fixed-PD contract,
  then releases ownership so the SM5714 re-applies the latest switching/thermal policy. If
  shutdown or contract negotiation fails, it leaves Q4 open and retries rather than exposing
  the SM5714 to the PPS path; suspend, hibernation, and image restore are vetoed until the
  handoff succeeds. A thermal-policy update during direct charging is deferred through this
  same handoff rather than writing SM5714 registers behind the active pump. The production
  kernel builds this driver in and suppresses its sysfs bind/unbind controls because it
  cannot safely be detached while it owns the battery path.

## If a control is missing

`gts9wifi-device-control list` shows what the rootfs supports. The helper, its
udev rules and the kernel attributes ship from this repository's `rootfs/overlay` and
`kernel/files`, so a rootfs older than the control simply will not list it.
