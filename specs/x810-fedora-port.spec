Name:           x810-fedora-port
Version:        %{port_version}
Release:        %{port_release}%{?dist}
Summary:        Fedora device support for Samsung Galaxy Tab S9+ Wi-Fi
License:        CC0-1.0 AND MIT AND LGPL-2.1-or-later AND BSD-2-Clause
BuildArch:      aarch64
# This is a payload-only support RPM; the separately built libcamera module's
# sources are in the repository and no compiled debugsource package is needed.
%global debug_package %{nil}
Requires:       systemd
Requires:       policycoreutils
Requires:       python3
Requires:       device-mapper
Requires:       libcamera-ipa%{?_isa} = 0.7.1-1.fc44
Requires:       tuned-ppd
Requires:       libssc.so.2()(64bit)
# The port's GNOME profile and UCM route require a live PipeWire graph.  The
# Workstation multimedia group recommends (rather than requires) the daemon,
# while rootfs builds disable weak deps; declare the stack so both fresh
# images and Tab Companion RPM updates cannot silently omit it.
Requires:       pipewire
Requires:       pipewire-alsa
Requires:       pipewire-pulseaudio
Requires:       wireplumber
Requires:       pipewire-utils
Requires:       colord
Provides:       iio-sensor-proxy = 3.9

Source0:        port-overlay.tar.gz
Source1:        port-overlay.filelist
Source2:        port-license.txt

%description
Port-owned Fedora device integration for the Samsung Galaxy Tab S9+ Wi-Fi
(SM-X810), including its SSC-backed sensor proxy and the HI1337 libcamera
software-IPA helper. The helper is built for Fedora 44 aarch64 against the
exact Fedora libcamera 0.7.1 build. It includes only the owner-authorized,
SHA-256-pinned X810 CYG1 VPU firmware required for in-place hardware decode;
kernel and boot images are deliberately excluded.

%prep
%setup -q -c -T

%build

%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}
install -D -m0644 %{SOURCE2} %{buildroot}%{_licensedir}/%{name}/LICENSE

%posttrans
# Existing rootfs archives were extracted by TWRP without Fedora's SELinux
# xattrs. Repair labels immediately to stop permissive-mode AVC flooding, then
# request Fedora's stock early-boot autorelabel once. That reboot relaunches
# PID 1 and services with correct SELinux domains. New images already contain
# both markers and therefore do not repeat this migration on every update.
selinux_migration_marker=/var/lib/x810-fedora/selinux-relabel-v1-requested
if [ ! -e "$selinux_migration_marker" ] && command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -RFx / >/dev/null 2>&1 || :
    fi
    printf '%s\n' '-F' > /.autorelabel
    mkdir -p /var/lib/x810-fedora
    touch "$selinux_migration_marker"
fi

if command -v getent >/dev/null 2>&1 && command -v usermod >/dev/null 2>&1; then
    # The installer assigns the chosen desktop account UID 1000. Add it to
    # video on existing installations so the updater repairs camera-node
    # access as well; a fresh image has no UID 1000 yet and is handled by the
    # installer's useradd command.
    desktop_user=$(getent passwd 1000 | cut -d: -f1)
    if [ -n "$desktop_user" ] && getent group video >/dev/null 2>&1; then
        usermod -a -G video "$desktop_user" || :
    fi
    # Tab Companion opens the gpio-vibrator evdev node as the desktop user.
    # The udev rule limits this group access to the vibrator event node.
    if [ -n "$desktop_user" ] && getent group input >/dev/null 2>&1; then
        usermod -a -G input "$desktop_user" || :
    fi
    # Keep ALSA hardware capture/playback available to the port's desktop
    # user's PipeWire services even in non-seat/SSH-started sessions. Normal
    # logind uaccess ACLs still grant devices to the active graphical user.
    if [ -n "$desktop_user" ] && getent group audio >/dev/null 2>&1; then
        usermod -a -G audio "$desktop_user" || :
    fi
fi
if command -v systemctl >/dev/null 2>&1; then
    # Earlier X810 images carried this exact system-level mask, which hides
    # Fedora's vendor.mount unit even though the read-only Android LP mapping
    # now supplies /dev/mapper/vendor. Remove only the known /dev/null mask;
    # preserve any other local unit override.
    vendor_mount_mask=/etc/systemd/system/vendor.mount
    if [ -L "$vendor_mount_mask" ] && [ "$(readlink "$vendor_mount_mask")" = /dev/null ]; then
        rm -f "$vendor_mount_mask"
    fi
    # Remove only the exact old port-created 20s override. It cut off
    # gts9wifi-wait-sensor-proxy while the required ADSP boot (25s) was still
    # starting. The service no longer blocks GDM, so it can use its packaged
    # bounded timeout without delaying the desktop.
    sensor_timeout_override=/etc/systemd/system/gts9wifi-wait-sensor-proxy.service.d/timeout.conf
    if [ -f "$sensor_timeout_override" ] && command -v cmp >/dev/null 2>&1; then
        if printf '[Service]\nTimeoutStartSec=20s\n' | cmp -s - "$sensor_timeout_override"; then
            rm -f "$sensor_timeout_override"
            rmdir /etc/systemd/system/gts9wifi-wait-sensor-proxy.service.d \
                >/dev/null 2>&1 || :
        fi
    fi
    systemctl daemon-reload >/dev/null 2>&1 || :
    # Apply the X810 no-suspend lid policy immediately on already-running
    # systems; logind reloads logind.conf on SIGHUP. Sleep-target masks above
    # take effect after daemon-reload even if logind is not yet running.
    if systemctl is-active --quiet systemd-logind.service; then
        systemctl kill --signal=HUP systemd-logind.service >/dev/null 2>&1 || :
    fi
    # The support RPM owns the pinned SSC-linked iio-sensor-proxy binary and
    # service files. Refresh an already-running proxy after replacement so
    # GNOME reclaims the sensor and receives the corrected startup properties.
    if systemctl is-active --quiet iio-sensor-proxy.service; then
        systemctl try-restart iio-sensor-proxy.service >/dev/null 2>&1 || :
    fi
    # The Android super mapping and vendor mount are read-only. Enable and
    # start them after installing the support update; a missing/unsupported
    # LP layout is fail-closed and must not make the RPM transaction fail.
    systemctl enable gts9wifi-android-parts.service vendor.mount \
        >/dev/null 2>&1 || :
    systemctl start gts9wifi-android-parts.service vendor.mount \
        >/dev/null 2>&1 || :
    # Install CPUFreq's policy providers/governors before starting tuned-ppd.
    # Otherwise the D-Bus profile can be selected before policies exist, then
    # remain a no-op when qcom-cpufreq-hw appears later in the transaction.
    if command -v modprobe >/dev/null 2>&1; then
        modprobe cpufreq_powersave >/dev/null 2>&1 || :
        modprobe icc_osm_l3 >/dev/null 2>&1 || :
    fi
    # The generic TuneD "powersave" profile prefers schedutil when available;
    # on SM8550 that can still reach benchmark-boost frequencies. Route PPD's
    # power-saver mode to the X810 profile that pins the powersave governor,
    # preserving the upstream profile's non-CPU power-saving settings.
    if [ -x /usr/libexec/x810-configure-tuned-profiles ]; then
        if ! ppd_change=$(/usr/libexec/x810-configure-tuned-profiles 2>&1); then
            echo "x810-fedora-port: could not configure TuneD power profiles: $ppd_change" >&2
            ppd_change="configuration failed"
        fi
        case "$ppd_change" in
            "updated power-saver mapping")
                if systemctl is-active --quiet tuned-ppd.service; then
                    active_profile=$(busctl --system get-property \
                        net.hadess.PowerProfiles /net/hadess/PowerProfiles \
                        net.hadess.PowerProfiles ActiveProfile 2>/dev/null \
                        | sed -n 's/^s "\([^"]*\)"$/\1/p')
                    systemctl try-restart tuned-ppd.service >/dev/null 2>&1 || :
                    if [ -n "$active_profile" ]; then
                        for attempt in 1 2 3 4 5; do
                            systemctl is-active --quiet tuned-ppd.service && break
                            sleep 1
                        done
                        busctl --system set-property net.hadess.PowerProfiles \
                            /net/hadess/PowerProfiles net.hadess.PowerProfiles \
                            ActiveProfile s "$active_profile" >/dev/null 2>&1 || :
                    fi
                fi
                ;;
            "power-saver mapping already configured") ;;
            *) echo "x810-fedora-port: TuneD power-saver mapping not changed: $ppd_change" >&2 ;;
        esac
    fi
    # Install the GNOME power-profile API on Fedora 44 using TuneD's PPD
    # compatibility daemon. Balanced remains the default.
    systemctl enable tuned.service tuned-ppd.service >/dev/null 2>&1 || :
    systemctl start tuned-ppd.service >/dev/null 2>&1 || :
    # Keep the optional user-configurable CPU thermal cap active on existing
    # installs as well as freshly installed images. It never edits kernel trips.
    systemctl enable x810-thermal-limit.service >/dev/null 2>&1 || :
    if systemctl is-active --quiet x810-thermal-limit.service; then
        systemctl try-restart x810-thermal-limit.service >/dev/null 2>&1 || :
    else
        systemctl start x810-thermal-limit.service >/dev/null 2>&1 || :
    fi
    # The Qualcomm ALSA card can register after WirePlumber's first scan. Run
    # the bounded user-session recovery now on updates and every boot; it only
    # restarts WirePlumber/reselects HiFi after ALSA is present, never the ADSP.
    systemctl enable gts9wifi-audio-session.service >/dev/null 2>&1 || :
    systemctl start gts9wifi-audio-session.service >/dev/null 2>&1 || :
    # This panel has no EDID and no assigned profile by default. Mutter then
    # reports Night Light as supported but leaves GAMMA_LUT unset. Create a
    # persistent system-scope sRGB fallback while preserving any custom ICC.
    systemctl enable gts9wifi-color-profile.service >/dev/null 2>&1 || :
    systemctl start gts9wifi-color-profile.service >/dev/null 2>&1 || :
    # Migrate the enabled sensor-recovery unit from its previous
    # multi-user.target link to the new graphical.target ordering. Reenable
    # changes symlinks only; do not trigger a live sensorspd/ADSP restart.
    systemctl reenable gts9wifi-wait-sensor-proxy.service \
        >/dev/null 2>&1 || :
    # Keep ADSP and sensorspd out of standalone preset enablement. The
    # graphical sensor-recovery unit requests them after GNOME and panel
    # recovery, with FastRPC ownership and HexagonFS permissions prepared.
    systemctl enable gts9wifi-sensor-registry-perms.service \
        >/dev/null 2>&1 || :
    systemctl disable --quiet gts9wifi-adsp-boot.service \
        hexagonrpcd-adsp-sensorspd.service >/dev/null 2>&1 || :
    # Do not stage the Android vendor/persist registry during a live RPM
    # transaction: it could race an active sensorspd. The boot oneshot builds
    # a private /run tree before the next sensorspd attach.
fi
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload >/dev/null 2>&1 || :
fi
if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then
    glib-compile-schemas /usr/share/glib-2.0/schemas >/dev/null 2>&1 || :
fi

%postun
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || :
fi
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload >/dev/null 2>&1 || :
fi
if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then
    glib-compile-schemas /usr/share/glib-2.0/schemas >/dev/null 2>&1 || :
fi

%files -f %{SOURCE1}
%defattr(-,root,root,-)
%license %{_licensedir}/%{name}/LICENSE
%license %{_licensedir}/%{name}/libcamera/LGPL-2.1-or-later.txt
%license %{_licensedir}/%{name}/libcamera/BSD-2-Clause.txt
