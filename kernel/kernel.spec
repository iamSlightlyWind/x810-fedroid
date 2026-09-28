# linux-x810: mainline kernel package for the Samsung Galaxy Tab S9+ Wi-Fi.
# Built from an already-prepared tree (kernel/prepare.sh runs in the workflow
# and the result is fed in as Source0).  Translation of the postmarketOS
# APKBUILD package() to RPM.
%define flavor gts9wifi
%define debug_package %{nil}
%define kversion 7.2.0

Name:           linux-x810
Version:        7.2.0
Release:        0.1%{?dist}
Summary:        Mainline Linux kernel for Samsung Galaxy Tab S9+ Wi-Fi (SM-X810)
License:        GPL-2.0-only
URL:            https://www.kernel.org
BuildRequires:  dtc
BuildRequires:  python3
BuildArch:      aarch64
ExclusiveArch:  aarch64
Provides:       kernel-uname-r
Provides:       linux-gts9wifi = %{version}-%{release}
Obsoletes:      linux-gts9wifi <= %{version}-%{release}
AutoReqProv:    no

Source0:        linux-prepared.tar.gz

%description
Mainline %{kversion} plus the gts9pwifi/SM-X810 port patch set: FTS1BA90A touch,
ANA38407 panel, SM5714/SM5440/PS5169 power & Type-C, Wacom WEZ01 pen,
Samsung-specific display/PCIe/WCN fixes.  Identical sources to the running
postmarketOS build, repackaged so Fedora can own the kernel.  The uname is
%{kversion}-%{flavor} (localversion), distinct from the pmOS kernel so both
module trees can coexist during the transition.

%prep
%setup -q -n linux-prepared

%build
unset LDFLAGS
make ARCH=arm64 LLVM=1 %{?_smp_mflags} \
     KBUILD_BUILD_VERSION="%{release}.%{flavor}"

%install
# The boot files are installed directly, not via "make zinstall": zinstall
# routes through the build host's installkernel/kernel-install hooks, and
# the rolling Fedora 44 CI container grew a 50-dracut.install that fails
# hard inside rpmbuild (no /lib/modules at the container root).  The hook
# chain produced nothing beyond these two files anyway.
krel=$(make ARCH=arm64 kernelrelease)
make ARCH=arm64 LLVM=1 \
     modules_install dtbs_install \
     INSTALL_MOD_PATH=%{buildroot}/usr \
     INSTALL_DTBS_PATH=%{buildroot}/boot/dtbs-%{kversion}-%{flavor} \
     INSTALL_MOD_STRIP=1
install -Dm0644 arch/arm64/boot/vmlinuz.efi %{buildroot}/boot/vmlinuz-$krel
install -Dm0644 System.map %{buildroot}/boot/System.map-$krel

depmod -b %{buildroot}/usr -a %{kversion}-%{flavor}
rm -f %{buildroot}/usr/lib/modules/*/build %{buildroot}/usr/lib/modules/*/source

%check
# These drivers are configured as modules and are the only Linux-facing
# fingerprint reader/secure-element interfaces.  A Kconfig entry alone is not
# enough: an invalid Makefile registration used to silently omit both modules
# from the RPM while leaving the build green.
modules_root=%{buildroot}/usr/lib/modules/%{kversion}-%{flavor}
for module in egis_el721 snvm; do
    if ! find "$modules_root" -type f \
        \( -name "$module.ko" -o -name "$module.ko.*" \) \
        -print -quit | grep -q .; then
        echo "required fingerprint module missing from kernel RPM: $module" >&2
        exit 1
    fi
done

# Check the actuator at the point where the kernel and DTB artifacts are
# actually packaged. The source node has no vcc-supply: the X810 CYG1 motor is
# GPIO-powered, so the port patch makes gpio-vibra tolerate that valid wiring.
for option in CONFIG_INPUT_GPIO_VIBRA=y \
              CONFIG_CPU_FREQ_GOV_SCHEDUTIL=y \
              CONFIG_CPU_FREQ_GOV_PERFORMANCE=y; do
    grep -Fqx "$option" .config || {
        echo "X810 kernel is missing required CPU/haptics option: $option" >&2
        exit 1
    }
done
vibrator_dtb="%{buildroot}/boot/dtbs-%{kversion}-%{flavor}/qcom/sm8550-samsung-gts9wifi.dtb"
if [ ! -s "$vibrator_dtb" ]; then
    echo "kernel RPM is missing the X810 device tree: $vibrator_dtb" >&2
    exit 1
fi
python3 - "$vibrator_dtb" <<'PY'
import subprocess
import sys

dtb = sys.argv[1]

def run_fdtget(command):
    result = subprocess.run(
        ["fdtget", *command],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        return None
    return result.stdout.strip()

def prop(node, name, kind="s"):
    return run_fdtget(["-t", kind, dtb, node, name])

compatible = prop("/vibrator", "compatible")
if compatible != "gpio-vibrator":
    raise SystemExit(f"wrong/missing X810 vibrator compatible: {compatible!r}")
status = prop("/vibrator", "status")
if status == "disabled":
    raise SystemExit("X810 vibrator DT node is disabled")

cells = prop("/vibrator", "enable-gpios", "x")
try:
    gpio_phandle, gpio_line, gpio_flags = (int(value, 16) for value in cells.split())
except (AttributeError, ValueError):
    raise SystemExit(f"invalid X810 vibrator enable-gpios cells: {cells!r}")
if gpio_line != 18 or gpio_flags != 0:
    raise SystemExit(
        f"X810 vibrator must use active-high GPIO18; got line={gpio_line}, flags={gpio_flags:#x}"
    )

# Phandle values are assigned by dtc and can change with unrelated DTS edits;
# resolve the label exported by the board DTB rather than pinning a numeric
# phandle or an address-bearing node path.
gpio_controller = prop("/__symbols__", "tlmm")
if not gpio_controller:
    raise SystemExit("X810 DTB does not export the TLMM label")
tlmm_phandle = prop(gpio_controller, "phandle", "x")
if not tlmm_phandle or int(tlmm_phandle, 16) != gpio_phandle:
    raise SystemExit("X810 vibrator enable-gpios does not reference TLMM")
controller_compatible = prop(gpio_controller, "compatible") or ""
gpio_count = prop(gpio_controller, "#gpio-cells", "x")
if "qcom,sm8550-tlmm" not in controller_compatible.split() or gpio_count != "2":
    raise SystemExit(
        f"X810 vibrator GPIO is not on SM8550 TLMM: {gpio_controller} "
        f"compatible={controller_compatible!r} #gpio-cells={gpio_count!r}"
    )
print("X810 haptics kernel/DTB contract OK")
PY

%files
%license COPYING
/boot/*
/usr/lib/modules/*

%changelog
* Sun Sep 20 2026 nacht20-de <318505313+nacht20-de@users.noreply.github.com> - 7.2.0-0.1
- Rebase onto the 7.2 stable release.  The 7.2.6 kernel loses the WCN6855
  MHI power-up deterministically (BHI load fails) with no delta in the port
  itself; 7.2 is the base while that regression is chased separately.

* Sat Aug 29 2026 nacht20-de <318505313+nacht20-de@users.noreply.github.com> - 7.2.0-0.1.rc3
- First RPM packaging of the gts9wifi mainline kernel (pmOS APKBUILD translation).
