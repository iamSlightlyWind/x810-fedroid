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
Requires:       python3
Requires:       device-mapper
Requires:       libcamera-ipa%{?_isa} = 0.7.1-1.fc44
Requires:       tuned-ppd
Requires:       libssc.so.2()(64bit)
Provides:       iio-sensor-proxy = 3.9

Source0:        port-overlay.tar.gz
Source1:        port-overlay.filelist
Source2:        port-license.txt

%description
Port-owned Fedora device integration for the Samsung Galaxy Tab S9+ Wi-Fi
(SM-X810), including its SSC-backed sensor proxy and the HI1337 libcamera
software-IPA helper. The helper is built for Fedora 44 aarch64 against the
exact Fedora libcamera 0.7.1 build. This package deliberately excludes the
kernel, boot images, and firmware.

%prep
%setup -q -c -T

%build

%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}
install -D -m0644 %{SOURCE2} %{buildroot}%{_licensedir}/%{name}/LICENSE

%posttrans
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
    # Install the GNOME power-profile API on Fedora 44 using TuneD's PPD
    # compatibility daemon. The balanced profile remains the default; users
    # can select power-saver/performance from GNOME's normal power menu.
    systemctl enable tuned.service tuned-ppd.service >/dev/null 2>&1 || :
    systemctl start tuned-ppd.service >/dev/null 2>&1 || :
    # Keep ADSP and sensorspd out of standalone preset enablement. The
    # sensor-proxy recovery unit requests them only after panel coldboot
    # recovery, with FastRPC node ownership and HexagonFS cache permissions
    # prepared first.
    systemctl enable gts9wifi-sensor-registry-perms.service \
        >/dev/null 2>&1 || :
    systemctl disable --quiet gts9wifi-adsp-boot.service \
        hexagonrpcd-adsp-sensorspd.service >/dev/null 2>&1 || :
    # Do not rerun the registry normalizer in a live transaction: it adjusts
    # firmware-tree mtimes/ownership and could race an active sensorspd. The
    # enabled boot unit prepares that tree before the next sensorspd attach.
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
