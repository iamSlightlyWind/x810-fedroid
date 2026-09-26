Name:           x810-fedora-port
Version:        %{port_version}
Release:        %{port_release}%{?dist}
Summary:        Fedora device support for Samsung Galaxy Tab S9+ Wi-Fi
License:        MIT
BuildArch:      noarch
Requires:       systemd

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
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || :
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
