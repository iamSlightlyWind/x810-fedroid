Name:           x810-fedora-port
Version:        %{port_version}
Release:        %{port_release}%{?dist}
Summary:        Fedora device support for Samsung Galaxy Tab S9+ Wi-Fi
License:        MIT
BuildArch:      noarch
Requires:       systemd
Requires:       tuned-ppd

Source0:        port-overlay.tar.gz
Source1:        port-overlay.filelist

%description
Port-owned, architecture-independent Fedora device integration for the
Samsung Galaxy Tab S9+ Wi-Fi (SM-X810). This package contains the rootfs
overlay and Tab Companion port identity only; it deliberately excludes the
kernel, boot images, firmware payloads, and base Fedora packages.

%prep
%setup -q -c -T

%build

%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}

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
fi
if command -v systemctl >/dev/null 2>&1; then
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
    # GNOME's power-profile UI uses the PPD API provided by tuned-ppd.
    systemctl enable tuned.service tuned-ppd.service >/dev/null 2>&1 || :
    systemctl start tuned-ppd.service >/dev/null 2>&1 || :
    # This port's ADSP boot path is intentionally manual: it has previously
    # hung/reset the tablet, especially when raced with panel coldboot resume.
    # Update the preset and remove old autostart links on existing installs.
    systemctl disable --quiet gts9wifi-adsp-boot.service \
        hexagonrpcd-adsp-sensorspd.service >/dev/null 2>&1 || :
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
