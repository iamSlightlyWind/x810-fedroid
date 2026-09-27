#!/bin/bash
# Build a Fedora aarch64 rootfs for the Samsung Galaxy Tab S9 Wi-Fi (gts9wifi).
#
# MUST run inside the pinned aarch64 Fedora environment — either the GitHub
# Actions arm64 runner container (see .github/workflows/rootfs.yml) or locally:
#
#   podman run --rm -it -v "$PWD:/work:Z" -w /work \
#       -e BUILDER_IMAGE=quay.io/fedora/fedora@sha256:78839470162801db2182957e00652f09c4fecfd552435f4fe4569604679a9030 \
#       quay.io/fedora/fedora@sha256:78839470162801db2182957e00652f09c4fecfd552435f4fe4569604679a9030 \
#       ./rootfs/build-rootfs.sh
#
# Optional X810_DNF_CACHE_DIR selects an external DNF5 cache directory; it
# defaults to /tmp/x810-dnf-cache for local builds. CI mounts its cache there.
#
# Everything is native arm64: no qemu, no cross toolchain.
#
# Default image = Fedora Workstation (GNOME + gdm), full device stack,
# firmware payload and pinned kernel RPM.  It deliberately has no human user,
# no password, and a locked root account; the installer provisions the user.
# The supported X810 layout is Android + Fedora on UFS: the matching Android
# boot bundle mounts the dedicated `linuxroot` partition by GPT PARTLABEL.
# GTS9_DESKTOP=core builds the smaller headless debug image.

set -euo pipefail

fedora_release="${FEDORA_RELEASE:-44}"
kver="${GTS9_KERNEL_VERSION:-7.2}"
# gnome = Fedora Workstation environment + gdm (the release image);
# core = headless @core only (smaller bring-up/debug image).
desktop="${GTS9_DESKTOP:-gnome}"
# Updater version is independent of kernel/rootfs workflow tags.  Developer
# and debug builds stay explicitly unversioned unless the caller supplies an
# actual numeric port release version.
port_version="${PORT_VERSION:-unknown}"
if [[ "$port_version" != "unknown" &&
      ! "$port_version" =~ ^(0|[1-9][0-9]{0,19})\.(0|[1-9][0-9]{0,19})\.(0|[1-9][0-9]{0,19})$ ]]; then
    echo "ERROR: PORT_VERSION must be 'unknown' or numeric MAJOR.MINOR.PATCH" >&2
    exit 2
fi
# CI passes a downloaded, sha256-verified firmware payload and the pinned
# kernel RPM here so the release image is self-contained: firmware blobs and
# a module tree matching the boot bundle live inside the rootfs.
firmware_tar="${FIRMWARE_TARBALL:-}"
kernel_rpm="${KERNEL_RPM:-}"

# Compose repositories are immutable snapshots, not mirrorlists.  Their
# primary/group metadata and package versions are therefore fixed for a given
# pair.  Fedora 44 defaults are the stable release compose and an explicitly
# pinned updates compose; building another Fedora release requires specifying
# both matching compose names.
fedora_base_compose="${FEDORA_BASE_COMPOSE:-Fedora-44-20260422.1}"
fedora_updates_compose="${FEDORA_UPDATES_COMPOSE:-Fedora-44-updates-20260926.0}"
builder_image="${BUILDER_IMAGE:-unknown-local-environment}"
repo_root="https://kojipkgs.fedoraproject.org/compose"
# DNF5 otherwise places an installroot's system cache under its /var/cache.
# Use an explicit host/container path instead; local builds get an ephemeral
# cache, while CI may point this at a runner cache mounted into the builder.
dnf_cache_dir="${X810_DNF_CACHE_DIR:-/tmp/x810-dnf-cache}"
if [[ "$dnf_cache_dir" != /* ]]; then
    echo "ERROR: X810_DNF_CACHE_DIR must be an absolute path outside the rootfs: $dnf_cache_dir" >&2
    exit 2
fi

if [[ ! "$fedora_base_compose" =~ ^Fedora-${fedora_release}-[0-9]{8}\.[0-9]+$ ]]; then
    echo "ERROR: FEDORA_BASE_COMPOSE must be an immutable Fedora-${fedora_release}-YYYYMMDD.N compose" >&2
    exit 2
fi
if [[ ! "$fedora_updates_compose" =~ ^Fedora-${fedora_release}-updates-[0-9]{8}\.[0-9]+$ ]]; then
    echo "ERROR: FEDORA_UPDATES_COMPOSE must be an immutable Fedora-${fedora_release}-updates-YYYYMMDD.N compose" >&2
    exit 2
fi
fedora_base_url="$repo_root/${fedora_release}/${fedora_base_compose}/compose/Everything/aarch64/os/"
fedora_updates_url="$repo_root/updates/${fedora_updates_compose}/compose/Everything/aarch64/os/"
fedora_repo_id="x810-${fedora_base_compose}"
updates_repo_id="x810-${fedora_updates_compose}"
# The repos below are added with --repofrompath, so they do not inherit the
# Fedora repo definitions' gpgkey setting. Keep package signature checking
# enabled and explicitly attach Fedora's release/architecture signing key.
# The pinned Fedora 44 aarch64 builder carries this standard path as a
# symlink to RPM-GPG-KEY-fedora-44-primary.
fedora_gpgkey_file="/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-${fedora_release}-aarch64"
fedora_gpgkey_url="file://${fedora_gpgkey_file}"
if [ ! -s "$fedora_gpgkey_file" ]; then
    echo "ERROR: Fedora ${fedora_release} aarch64 RPM signing key is missing: $fedora_gpgkey_file" >&2
    echo "Run this rootfs build in the pinned Fedora builder with fedora-gpg-keys installed." >&2
    exit 2
fi
dnf_repo_args=(
    --disablerepo='*'
    --repofrompath="$fedora_repo_id,$fedora_base_url"
    --repofrompath="$updates_repo_id,$fedora_updates_url"
    --setopt="$fedora_repo_id.gpgcheck=1"
    --setopt="$updates_repo_id.gpgcheck=1"
    --setopt="$fedora_repo_id.gpgkey=$fedora_gpgkey_url"
    --setopt="$updates_repo_id.gpgkey=$fedora_gpgkey_url"
    --setopt=cachedir="$dnf_cache_dir"
    --setopt=system_cachedir="$dnf_cache_dir"
    --setopt=keepcache=True
    --enablerepo="$fedora_repo_id"
    --enablerepo="$updates_repo_id"
)

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_dir="$(dirname "$script_dir")"
rootfs="${ROOTFS_DIR:-$repo_dir/out/rootfs}"
outdir="${OUT_DIR:-$repo_dir/out}"
assets="$repo_dir/local-assets"
rootfs_real="$(realpath -m "$rootfs")"
dnf_cache_real="$(realpath -m "$dnf_cache_dir")"
if [[ "$dnf_cache_real" == "$rootfs_real" || "$dnf_cache_real" == "$rootfs_real/"* ]]; then
    echo "ERROR: X810_DNF_CACHE_DIR must be outside the rootfs ($rootfs_real): $dnf_cache_real" >&2
    exit 2
fi
mkdir -p "$dnf_cache_dir"

tree_sha256() {
    local tree="$1"
    if [ ! -d "$tree" ]; then
        printf 'missing\n' | sha256sum | cut -d' ' -f1
        return
    fi
    (cd "$tree" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
        | sha256sum | cut -d' ' -f1
}

source_inputs_sha256() {
    (
        cd "$repo_dir"
        {
            sha256sum rootfs/build-rootfs.sh rootfs/stage-public-firmware.sh \
                tools/bdftool.py tools/stamp-port-metadata.py \
                tools/build-port-support-rpm.sh tools/test-port-build-contract.py \
                tools/verify-x810-rootfs-archive.py
            find rootfs/overlay specs -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
        } | LC_ALL=C sort -k2
    ) | sha256sum | cut -d' ' -f1
}

# Fallback for local runs: the fetched payload in local-assets.
[ -n "$firmware_tar" ] || firmware_tar="$assets/firmware.tar.gz"

missing_assets=()

# The four non-Fedora projects below are built from immutable upstream commit
# snapshots. Verify exact archive bytes before extraction so a moved tag,
# changed archive, or compromised mirror fails the build closed. This locks
# these source inputs only. Fedora packages come from immutable compose URLs
# recorded in the build manifest.
fetch_locked_source() {
    local label="$1" url="$2" expected_sha="$3" dest="$4"
    local archive actual
    archive="$(mktemp)"
    if ! curl -fLsS --max-time 300 -o "$archive" "$url"; then
        echo "FAILED to download locked source: $label" >&2
        rm -f "$archive"
        return 1
    fi
    actual="$(sha256sum "$archive" | cut -d' ' -f1)"
    if [ "$actual" != "$expected_sha" ]; then
        echo "FAILED checksum for locked source: $label" >&2
        echo "  got:  $actual" >&2
        echo "  want: $expected_sha" >&2
        rm -f "$archive"
        return 1
    fi
    if ! tar xzf "$archive" -C "$dest" --strip-components=1; then
        echo "FAILED to extract locked source: $label" >&2
        rm -f "$archive"
        return 1
    fi
    rm -f "$archive"
}

echo ">>> Fedora $fedora_release rootfs for gts9wifi, kernel $kver"
mkdir -p "$rootfs" "$outdir"
# Never silently re-use a previous installroot: stale files/packages would
# make a nominally identical build produce a different image.
if find "$rootfs" -mindepth 1 -print -quit | grep -q .; then
    echo "ERROR: installroot is not empty: $rootfs" >&2
    echo "Choose a fresh ROOTFS_DIR (or remove this generated build directory) and retry." >&2
    exit 2
fi
# Prevent a prior versioned local build from leaking its RPM into an
# unversioned/debug build's release assets.
old_port_rpms=("$outdir"/x810-fedora-port-*.noarch.rpm)
if [ -e "${old_port_rpms[0]:-}" ]; then
    rm -f "${old_port_rpms[@]}"
fi

echo ">>> Installing rootfs packages"
# --use-host-config supplies the container's Fedora RPM trust keys.  The
# repos themselves are passed explicitly above, never through mutable mirrors.
dnf -y --installroot="$rootfs" --releasever="$fedora_release" \
    --use-host-config "${dnf_repo_args[@]}" \
    --setopt=install_weak_deps=False --setopt=tsflags=nodocs install \
    @core \
    NetworkManager NetworkManager-wifi wpa_supplicant \
    openssh-server openssh-clients \
    sudo chrony zram-generator python3 \
    bluez bluez-tools \
    qrtr \
    alsa-ucm alsa-utils dtc \
    libqmi libqrtr-glib protobuf-c libmbim \
    systemd-pam \
    atheros-firmware qcom-firmware \
    e2fsprogs kmod

if [ "$desktop" = "gnome" ]; then
    echo ">>> Installing the GNOME Workstation environment"
    # Same environment group the Fedora Workstation install uses; gdm,
    # pipewire, gnome-shell and the Wayland session come with it.  GNOME's
    # touch support (on-screen keyboard, gestures) needs no extra setup.
    dnf -y --installroot="$rootfs" --releasever="$fedora_release" \
        --use-host-config "${dnf_repo_args[@]}" \
        --setopt=install_weak_deps=False --setopt=tsflags=nodocs install \
        '@^workstation-product-environment'
    # The first-login welcome wizard has nothing to offer in a pre-provisioned
    # image; drop it so the first boot goes straight to the gdm login.
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" -q remove \
        gnome-initial-setup || true
    # The group also pulls libcamera's qcam demo app.  The port's camera app is
    # GNOME Snapshot, so drop qcam and its Qt dependency.  Keep
    # libcamera-tools: its `cam` is the libcamera diagnostic used to verify the
    # HI1337 sensors and the media graph.
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" -q remove \
        libcamera-qcam || true
    # The workstation group pulls the linux-firmware meta as mandatory; this
    # board needs none of it (the device payload plus atheros/qcom cover
    # every consumer), and it is half a gigabyte of dead weight.
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" -q remove \
        linux-firmware amd-gpu-firmware intel-gpu-firmware nvidia-gpu-firmware \
        iwlwifi-dvm-firmware iwlwifi-mld-firmware iwlwifi-mvm-firmware \
        iwlegacy-firmware mt7xxx-firmware realtek-firmware tiwilink-firmware \
        libertas-firmware brcmfmac-firmware nxpwireless-firmware \
        qcom-wwan-firmware || true
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" --setopt=tsflags=nodocs \
        install atheros-firmware qcom-firmware || true
    # GNOME's font defaults name Adwaita Sans/Mono, but those packages ride in
    # as weak deps that install_weak_deps=False strips.  Missing, Pango
    # resolves "Adwaita Mono" to proportional Noto Sans and every terminal
    # renders letterspaced (verified on hardware, 2026-09-05).
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" --setopt=tsflags=nodocs \
        install adwaita-mono-fonts adwaita-sans-fonts
    # The camera stack: the Workstation group installs no libcamera userspace
    # at all — Snapshot reaches cameras through PipeWire's libcamera monitor,
    # so without these the app reports "connect a camera" while the kernel
    # side probes fine.  v4l-utils is also what the rear-focus udev rule
    # drives; without it the rule is a silent no-op.
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" \
        --setopt=tsflags=nodocs \
        install libcamera libcamera-ipa libcamera-tools \
        pipewire-plugin-libcamera v4l-utils
    # Fedora GNOME exposes power profiles through the PPD D-Bus API. TuneD's
    # compatibility daemon maps those profiles onto this device's CPUFreq
    # governors without pretending to provide ACPI platform-profile support.
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" \
        --setopt=tsflags=nodocs install tuned-ppd
fi

echo ">>> Installing native build dependencies (build container only)"
# systemd: the base container image ships without it, but systemctl --root=
# below needs the binary.
dnf -y -q "${dnf_repo_args[@]}" install systemd meson ninja-build gcc git curl tar patch make \
    "pkgconf-pkg-config" \
    glib2-devel libgudev-devel systemd-devel polkit-devel kmod \
    libqmi-devel protobuf-c-devel qrtr-devel xz-devel \
    python3-devel python3-protobuf

echo ">>> Building libssc 0.4.4 (not in Fedora)"
# Same source the pmOS port uses; provides libssc.so + ssccli.
# Upstream commit 0cf77b93b55752da34dca2dcecc06fca8665184b.
sscdir="$(mktemp -d)"
fetch_locked_source "libssc 0.4.4" \
    "https://codeberg.org/DylanVanAssche/libssc/archive/0cf77b93b55752da34dca2dcecc06fca8665184b.tar.gz" \
    716d6bd6b34d2d753060c6b54c9a87e34fae75b724c763bf9ef487efa3621587 \
    "$sscdir"
meson setup "$sscdir/build" "$sscdir" -Dprefix=/usr -Db_lto=true
meson compile -C "$sscdir/build"
DESTDIR="$sscdir/staging" meson install --no-rebuild -C "$sscdir/build"
cp -a "$sscdir/staging/." "$rootfs/"
# Also install libssc into the build container itself: the iio-sensor-proxy
# meson check links against the .pc's libdir, which only resolves if the
# library really exists at /usr/lib64 in the container.  Without this the
# proxy silently builds the kernel-IIO backend and serves no sensors.
cp -a "$sscdir/staging/." /
export PKG_CONFIG_PATH="/usr/lib64/pkgconfig:/usr/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

echo ">>> Building pd-mapper 1.1 (not in Fedora)"
# Binary only: the sm8550 ADSP boots without service-registry JSONs (verified
# on the pmOS device).  Ships its own systemd unit.
# Upstream commit 5ecd2fe926aca7abfe40724177f63b942cff3947.
pdmdir="$(mktemp -d)"
fetch_locked_source "pd-mapper 1.1" \
    "https://github.com/andersson/pd-mapper/archive/5ecd2fe926aca7abfe40724177f63b942cff3947.tar.gz" \
    08972b8813d08da5e20d27e57c5989398a0b750be92cd4398b5b21190c6ccdd0 \
    "$pdmdir"
make -C "$pdmdir" -j"$(nproc)" prefix=/usr
make -C "$pdmdir" install prefix=/usr DESTDIR="$pdmdir/staging"
cp -a "$pdmdir/staging/." "$rootfs/"

echo ">>> Building hexagonrpcd 0.4.0 with Samsung patches"
# Upstream tag + the two Samsung patches from the pmOS port (large FastRPC
# inbufs; Samsung sensor-registry writes) + Alpine's systemd-units patch.
# Upstream tag v0.4.0 resolves to commit 23a69640bf10dc498226c602c5b5db11d8cb3d8e.
hexdir="$(mktemp -d)"
mkdir -p "$hexdir/src"
fetch_locked_source "hexagonrpc 0.4.0" \
    "https://github.com/linux-msm/hexagonrpc/archive/23a69640bf10dc498226c602c5b5db11d8cb3d8e.tar.gz" \
    7dbcf703182d59d19e0e7613b7cea7c71ef8aff6297987ed59377b85453b1a9f \
    "$hexdir/src"
for p in "$repo_dir"/specs/hexagonrpcd-samsung/patches/*.patch; do
    patch -d "$hexdir/src" -p1 < "$p"
done
meson setup "$hexdir/build" "$hexdir/src" -Dprefix=/usr -Db_lto=true
meson compile -C "$hexdir/build"
DESTDIR="$hexdir/staging" meson install --no-rebuild -C "$hexdir/build"
install -Dm644 "$repo_dir"/specs/hexagonrpcd-samsung/patches/10-fastrpc.rules \
    -t "$hexdir/staging/usr/lib/udev/rules.d/"
cp -a "$hexdir/staging/." "$rootfs/"
# The patch installs units to libdir/systemd/system, which lands in
# usr/lib64 on Fedora — a path systemd does not search. Move them next to
# every other system unit.
if [ -d "$rootfs/usr/lib64/systemd/system" ]; then
    mkdir -p "$rootfs/usr/lib/systemd/system"
    mv "$rootfs"/usr/lib64/systemd/system/* "$rootfs/usr/lib/systemd/system/"
    rmdir "$rootfs/usr/lib64/systemd/system" "$rootfs/usr/lib64/systemd" \
        2>/dev/null || true
fi

# The Workstation environment installs Fedora's iio-sensor-proxy (kernel
# I/O backend).  Do NOT dnf-remove it: mutter/gnome-shell require it and the
# transaction cascades the whole desktop out of the image.  Drop just the
# rpmdb entry; our libssc-linked build overwrites the files anyway.
chroot "$rootfs" rpm -e --nodeps iio-sensor-proxy || true

echo ">>> Building iio-sensor-proxy 3.9 with libssc support"
# Fedora's own build may not link libssc; build it exactly like the pmOS port
# (libssc + notify-slow-sensor-discovery + start-polling-claimed-while-starting).
# Upstream tag 3.9 resolves to commit 0085ddf8ecb173a1c5fcf2344aa40e561125354f.
ispdir="$(mktemp -d)"
fetch_locked_source "iio-sensor-proxy 3.9" \
    "https://gitlab.freedesktop.org/hadess/iio-sensor-proxy/-/archive/0085ddf8ecb173a1c5fcf2344aa40e561125354f/iio-sensor-proxy-0085ddf8ecb173a1c5fcf2344aa40e561125354f.tar.gz" \
    800682aa591fc672e959d2f3a43d1f4f7160a4c1cdabffd0ebff2bb8f3bb29be \
    "$ispdir"
for p in "$repo_dir"/specs/iio-sensor-proxy-libssc/patches/*.patch; do
    patch -d "$ispdir" -p1 < "$p"
done
meson setup "$ispdir/build" "$ispdir" -Dprefix=/usr -Dssc-support=enabled
meson compile -C "$ispdir/build"
DESTDIR="$ispdir/staging" meson install --no-rebuild -C "$ispdir/build"
cp -a "$ispdir/staging/." "$rootfs/"

# hexagonrpcd units run as the fastrpc system user (Alpine pre-install equivalent)
groupadd --root "$rootfs" -r fastrpc
useradd --root "$rootfs" -r -g fastrpc -s /usr/sbin/nologin -d / fastrpc

echo ">>> Applying device overlay"
# ROOTFS_DIR can be reused for local builds. Remove an older package first so
# an unknown/debug build cannot retain a stale updater version and a release
# build can never silently keep an older/newer RPM from a prior run.
if rpm --root "$rootfs" -q x810-fedora-port >/dev/null 2>&1; then
    echo "    Removing previous x810-fedora-port package from the staging root"
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" remove x810-fedora-port
fi
cp -a "$repo_dir/rootfs/overlay/." "$rootfs/"

# The overlay ships a gschema override that enables the port's own Shell
# extension (gnome-gts9wifi) by default, which only takes effect once the
# schema cache is rebuilt.  Recompiling is cheap and also repairs the cache if
# anything else dropped a schema in.
if command -v glib-compile-schemas >/dev/null 2>&1; then
    echo ">>> Rebuilding the GSettings schema cache"
    glib-compile-schemas "$rootfs/usr/share/glib-2.0/schemas"
else
    echo "    (glib-compile-schemas not found: the gnome-gts9wifi extension will not be enabled by default)" >&2
fi

echo ">>> Injecting local assets"
if [ -f "$firmware_tar" ]; then
    tar xzf "$firmware_tar" -C "$rootfs"
else
    missing_assets+=("firmware.tar.gz (Wi-Fi/BT/ADSP/audio blobs: run rootfs/fetch-local-assets.sh)")
fi
if [ -d "$assets/firmware-overrides" ]; then
    echo ">>> Applying firmware overrides (BT NVM/rampatch + IOE Wi-Fi set + CS35L45 speaker protection)"
    # Replaces the linux-firmware BT blobs with Samsung's device-tuned ones
    # (hpnv21g.bin + hpbtfw21.tlv): without them BT traffic lags under
    # 2.4GHz Wi-Fi activity (docs/Known-Issues.md, issue 3).  May also carry
    # the WCN6855 IOE 04866.5 amss/m3 that mainline runs best on, plus the
    # board-2.bin with the LE_X13S payload that restores 5 GHz RX
    # (2.4/5 GHz fixes: docs/Known-Issues.md issue 7, docs/Hardware-Notes.md), and the
    # Cirrus CS35L45 speaker-protection firmware (cirrus/) that lets the DSP
    # limiter bound cone excursion, which is what allows the per-amp volume
    # above the old -19 dB cap (#17).
    # Applied last so it wins over both the dnf linux-firmware package and
    # the firmware payload.
    cp -a "$assets/firmware-overrides/." "$rootfs/"
else
    echo "    (no firmware-overrides/ in local-assets: staging the public set below)" >&2
fi

echo ">>> Staging the device-independent firmware overrides"
# CI cannot run rootfs/fetch-local-assets.sh (it needs a running tablet), and
# while that was the only place the non-repo firmware was staged, every CI
# image silently shipped linux-firmware's generic blobs and lost the CS35L45
# speaker protection (#17), the iris VPU firmware (#16) and the validated
# WCN6855 Wi-Fi set with the 5 GHz RX BDF (#7).  The script stages all three
# from pinned public sources (or relocates them out of the firmware payload)
# and is a no-op over a tree fetch-local-assets.sh already populated.
#
# It fails the build when a piece cannot be staged, because each one is a
# silent-regression fix and the UCM sets the per-amp volume to 428 assuming the
# speaker-protection firmware is loaded.  GTS9_SKIP_PUBLIC_FIRMWARE=1 builds
# offline and reports what was skipped instead.
#
# Only the board-2.bin container edit needs python3 (tools/bdftool.py), and the
# Fedora base image does not ship it.
if ! command -v python3 >/dev/null 2>&1; then
    echo ">>> Installing python3 in the build container (Wi-Fi BDF fix)"
    dnf -y "${dnf_repo_args[@]}" install python3
fi
"$script_dir/stage-public-firmware.sh" "$rootfs"

if [ -d "$assets/modules/$kver" ]; then
    mkdir -p "$rootfs/usr/lib/modules"
    cp -a "$assets/modules/$kver" "$rootfs/usr/lib/modules/"
    depmod -b "$rootfs" "$kver"
elif [ -n "$kernel_rpm" ]; then
    :   # kernel modules come from the pinned RPM below
else
    missing_assets+=("modules/$kver (kernel modules matching eMMC kernel: run rootfs/fetch-local-assets.sh)")
fi

if [ -n "$kernel_rpm" ]; then
    echo ">>> Installing the pinned kernel RPM (module tree + /boot)"
    # Keeps the rootfs self-contained: modules signed by the same CI run as
    # the boot bundle, so Wi-Fi/BT load on first boot without any pairing
    # step (see the README rule about bundle/RPM pairing).
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" \
        --setopt=tsflags=nodocs install "$kernel_rpm"
    depmod -b "$rootfs" -a "${kver}-gts9wifi" 2>/dev/null || true
fi

echo ">>> Base system configuration"
cat > "$rootfs/etc/fstab" <<EOF
PARTLABEL=linuxroot / ext4 defaults 0 0
EOF
echo "localhost.localdomain" > "$rootfs/etc/hostname"
if [ -f "$rootfs/etc/selinux/config" ]; then
    sed -i 's/^SELINUX=.*/SELINUX=permissive/' "$rootfs/etc/selinux/config"
else
    echo "    WARN: /etc/selinux/config not found; SELinux left at default" >&2
fi

# USB gadget network comes up configured by the pmOS initramfs; keep the
# address after switch_root so first-boot debugging works over SSH.
mkdir -p "$rootfs/etc/NetworkManager/system-connections"
cat > "$rootfs/etc/NetworkManager/system-connections/usb0.nmconnection" <<EOF
[connection]
id=usb0
interface-name=usb0
type=ethernet
autoconnect=true

[ipv4]
address1=172.16.42.1/24
method=manual

[ipv6]
method=disabled
EOF
chmod 600 "$rootfs/etc/NetworkManager/system-connections/usb0.nmconnection"

echo ">>> Restoring root ownership"
# Every copy above preserves the SOURCE owner, and cp -a applies it to the
# destination directories themselves -- which is how a built image ends up
# with / and /etc owned by the checkout's uid and /usr by the dev host's:
#
#   cp -a "$repo_dir/rootfs/overlay/." "$rootfs/"   # chowns $rootfs and $rootfs/etc
#   tar xzf "$firmware_tar" -C "$rootfs"            # a "usr/" entry chowns $rootfs/usr
#
# That is not cosmetic.  systemd refuses to canonicalize any path with a
# non-root ancestor ("Detected unsafe path transition / (owned by 1001) ->
# /var"), so every tmpfiles.d entry in the image is inert -- and
# systemd-tmpfiles-setup.service hides it behind SuccessExitStatus=DATAERR
# CANTCREAT, so it exits 73 on every boot and still reports success.  It also
# leaves ~2400 packaged files owned by the build user (rpm -Va flags U/G on /,
# /etc, /usr and thousands more); on an image whose first user is uid 1000 that
# user then owns, and can rewrite, parts of /usr.
#
# Two steps, because the two classes need different treatment.
#
# 1. Ask the packages: rpm records the correct uid/gid/mode for every packaged
#    file, so it restores them exactly.  A blanket `chown -R root:root` here
#    would be actively harmful -- it strips the group ownership some paths need
#    (/var/log/journal is root:systemd-journal), takes the service accounts'
#    trees (/var/cache/httpd is apache:apache, /var/log/chrony chrony:chrony,
#    /var/spool/abrt-upload abrt:abrt), and would clobber legitimately
#    service-specific ownership such as /var/spool/mail.
#
#    rpm exits non-zero here because packages that list Python bytecode fail
#    with "restored failed" for the .pyc files the image does not carry; that is
#    expected and it still restores every file it can, so this is a note rather
#    than an error.
if ! rpm --root="$rootfs" -a --setugids --setperms >/dev/null 2>&1; then
    echo "    NOTE: rpm restore reported failures (expected: pruned .pyc); it restored the rest" >&2
fi

# 2. The overlay and firmware-override trees are not packages, so chown them by
#    hand: every entry the tree installs, plus each parent directory leading to
#    it (a root-owned file under a user-owned directory is still inert, because
#    the ancestors are what systemd canonicalizes).
chown root:root "$rootfs"
for tree in "$repo_dir/rootfs/overlay" "$assets/firmware-overrides"; do
    if [ -d "$tree" ]; then
        while IFS= read -r -d '' rel; do
            rel="${rel#./}"
            if [ -e "$rootfs/$rel" ]; then
                chown root:root "$rootfs/$rel"
                parent="$(dirname "$rel")"
                while [ "$parent" != "." ] && [ "$parent" != "/" ]; do
                    chown root:root "$rootfs/$parent" 2>/dev/null || true
                    parent="$(dirname "$parent")"
                done
            fi
        done < <(cd "$tree" && find . -print0)
    fi
done

# The firmware payload arrives as a tarball whose layout is not enumerated
# here, and firmware and kernel modules are root-owned by definition.
for d in "$rootfs/usr/lib/firmware" "$rootfs/lib/firmware" "$rootfs/usr/lib/modules"; do
    if [ -d "$d" ]; then
        chown -R root:root "$d"
    fi
done

echo ">>> Keeping the image credential-neutral"
# The installer creates the requested human account at install time.  Keep
# root locked and do not bake a password or a developer SSH key into releases.
chroot "$rootfs" passwd --lock root

# rmtfs arrives as a preset-enabled neighbour of qrtr but this board has no
# modem remoteproc; left enabled it restart-loops forever ("Failed to get
# rprocfd").
systemctl --root="$rootfs" mask rmtfs.service >/dev/null 2>&1 || true

echo ">>> Enabling services"
if [ "$desktop" = "gnome" ]; then
    systemctl --root="$rootfs" enable gdm >/dev/null 2>&1 \
        || echo "    WARN: gdm not found" >&2
    systemctl --root="$rootfs" set-default graphical.target >/dev/null 2>&1 || true
    # The Workstation firewall zone blocks inbound ssh, which would defeat
    # both the usb0 debug network and remote support; allow it permanently.
    chroot "$rootfs" firewall-offline-cmd --add-service=ssh >/dev/null 2>&1 \
        || echo "    WARN: could not allow ssh in the firewall" >&2
    # abrt only produces signature-error noise on an RTC-less tablet whose
    # clock starts wrong; and dnf-removing it cascades dnf itself out of
    # the image (libreport/python3-dnf dependency chain).  Mask it instead.
    systemctl --root="$rootfs" mask abrtd.service abrt-journal-core.service \
        abrt-oops.service abrt-vmcore.service abrt-xorg.service \
        abrt-dump-journal-core.service abrt-dump-oops.service >/dev/null 2>&1 || true
fi
for unit in \
    sshd NetworkManager \
    hexagonrpcd-adsp-rootpd \
    pd-mapper \
    gts9wifi-wait-sensor-proxy \
    gts9wifi-bt-provision bluetooth gts9wifi-mem-reclaim \
    gts9wifi-panel-coldboot-recover \
    gts9wifi-grow-rootfs \
    gts9wifi-usb-net gts9wifi-wifi-recover gts9wifi-sensor-registry-perms \
    gts9wifi-x11-dir-fix.path gts9wifi-chronyd \
    tuned.service tuned-ppd.service \
    mnt-vendor-persist.mount vendor-dsp.mount vendor-firmware_mnt.mount
do
    systemctl --root="$rootfs" enable "$unit" >/dev/null 2>&1 \
        || echo "    WARN: unit not found (check name after hexagonrpcd patch): $unit"
done
# Enforce the known-safe default even if a package preset or earlier image
# enabled these links. Do not mask them: they remain available for manual,
# one-at-a-time diagnostics once ADSP startup is understood.
systemctl --root="$rootfs" disable gts9wifi-adsp-boot.service \
    hexagonrpcd-adsp-sensorspd.service >/dev/null 2>&1 || true
# Deliberately NOT enabled, matching hard-won pmOS experience:
# - hexagonrpcd-adsp-sensorspd: pulls in gts9wifi-adsp-boot via the hexagonfs
#   drop-in's Requires=; the ADSP start can hang or reset the SoC, and doing
#   it while panel-coldboot-recover runs its pm_test suspend froze the board
#   completely.  Start it manually and watch.
# - gts9wifi-adsp-boot.service: same, ships disabled in the pmOS port.
# - gts9wifi-bt-revive.service: started by hand when the WCN sequencer
#   takes hci0 down.
# The preset in overlay/usr/lib/systemd/system-preset/85-gts9wifi.preset
# keeps first-boot preset-all from stripping the enablement above.
# TODO(phase-1.5): vendor make-dynpart-mappings and enable
# gts9wifi-android-parts.service + vendor.mount (super -> erofs /vendor).

echo ">>> Cleaning"
rm -rf "$rootfs/var/cache/libdnf5" "$rootfs/var/cache/dnf" \
    "$rootfs/var/cache/rpm" "$rootfs/var/log/dnf*"
rm -f "$rootfs/etc/machine-id" "$rootfs/var/lib/systemd/random-seed"

# Stamp after all overlays/assets have been installed so this is the exact
# metadata in the archived rootfs. Never derive it from a workflow/ref tag.
stamped_version="$(python3 "$repo_dir/tools/stamp-port-metadata.py" \
    "$rootfs" "$port_version" "$fedora_release")"
if [ "$stamped_version" != "$port_version" ]; then
    echo "ERROR: port metadata helper returned an unexpected version" >&2
    exit 1
fi
echo "    Tab Companion port version: $stamped_version"

support_rpm=""
if [ "$port_version" != "unknown" ]; then
    echo ">>> Building and installing the port-owned noarch support RPM"
    # Build dependencies live only in the build container, never in the image.
    dnf -y -q "${dnf_repo_args[@]}" install rpm-build
    support_rpm="$(bash "$repo_dir/tools/build-port-support-rpm.sh" \
        "$rootfs" "$port_version" "$outdir")"
    dnf -y --installroot="$rootfs" --use-host-config "${dnf_repo_args[@]}" \
        --setopt=install_weak_deps=False install "$support_rpm"
    rm -rf "$rootfs/var/cache/libdnf5" "$rootfs/var/cache/dnf" \
        "$rootfs/var/cache/rpm" "$rootfs/var/log/dnf*"
fi

echo ">>> Packing"
kernel_rpm_sha256=none
kernel_rpm_nevra=none
if [ -n "$kernel_rpm" ]; then
    kernel_rpm_sha256="$(sha256sum "$kernel_rpm" | cut -d' ' -f1)"
    kernel_rpm_nevra="$(rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$kernel_rpm")"
fi
firmware_sha256=none
if [ -f "$firmware_tar" ]; then
    firmware_sha256="$(sha256sum "$firmware_tar" | cut -d' ' -f1)"
fi
if [ -n "$support_rpm" ]; then
    port_package_name="$(rpm -qp --qf '%{NAME}' "$support_rpm")"
    port_package_version="$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$support_rpm")"
    port_package_arch="$(rpm -qp --qf '%{ARCH}' "$support_rpm")"
    port_package_asset="$(basename "$support_rpm")"
else
    port_package_name="none"
    port_package_version="unknown"
    port_package_arch="none"
    port_package_asset="none"
fi
{
    printf 'port_id=x810-fedora\n'
    printf 'port_version=%s\n' "$port_version"
    printf 'device_id=SM-X810\n'
    printf 'os_id=fedora\n'
    printf 'os_version=%s\n' "$fedora_release"
    printf 'arch=aarch64\n'
    printf 'port_package_name=%s\n' "$port_package_name"
    printf 'port_package_version=%s\n' "$port_package_version"
    printf 'port_package_arch=%s\n' "$port_package_arch"
    printf 'port_package_asset=%s\n' "$port_package_asset"
    printf 'kernel_rpm_nevra=%s\n' "$kernel_rpm_nevra"
    printf 'kernel_rpm_sha256=%s\n' "$kernel_rpm_sha256"
    printf 'firmware_sha256=%s\n' "$firmware_sha256"
    printf 'builder_image=%s\n' "$builder_image"
    printf 'fedora_base_compose=%s\n' "$fedora_base_compose"
    printf 'fedora_base_repo_url=%s\n' "$fedora_base_url"
    printf 'fedora_updates_compose=%s\n' "$fedora_updates_compose"
    printf 'fedora_updates_repo_url=%s\n' "$fedora_updates_url"
    printf 'dnf_install_weak_deps=false\n'
    printf 'dnf_tsflags=nodocs\n'
    printf 'firmware_source_url=%s\n' "${FIRMWARE_SOURCE_URL:-local-or-unspecified}"
    printf 'firmware_source_sha256=%s\n' "$firmware_sha256"
    printf 'firmware_tree_sha256=%s\n' "$(tree_sha256 "$rootfs/usr/lib/firmware")"
    printf 'kernel_module_tree_sha256=%s\n' "$(tree_sha256 "$rootfs/usr/lib/modules")"
    printf 'source_inputs_sha256=%s\n' "$(source_inputs_sha256)"
    printf 'source_libssc_commit=0cf77b93b55752da34dca2dcecc06fca8665184b\n'
    printf 'source_libssc_archive_sha256=716d6bd6b34d2d753060c6b54c9a87e34fae75b724c763bf9ef487efa3621587\n'
    printf 'source_pd_mapper_commit=5ecd2fe926aca7abfe40724177f63b942cff3947\n'
    printf 'source_pd_mapper_archive_sha256=08972b8813d08da5e20d27e57c5989398a0b750be92cd4398b5b21190c6ccdd0\n'
    printf 'source_hexagonrpc_commit=23a69640bf10dc498226c602c5b5db11d8cb3d8e\n'
    printf 'source_hexagonrpc_archive_sha256=7dbcf703182d59d19e0e7613b7cea7c71ef8aff6297987ed59377b85453b1a9f\n'
    printf 'source_iio_sensor_proxy_commit=0085ddf8ecb173a1c5fcf2344aa40e561125354f\n'
    printf 'source_iio_sensor_proxy_archive_sha256=800682aa591fc672e959d2f3a43d1f4f7160a4c1cdabffd0ebff2bb8f3bb29be\n'
    printf 'rootfs_archive_format=sorted-pax-mtime-0-numeric-owner-gzip-n\n'
    printf 'builder_container_packages='
    LC_ALL=C rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort | paste -sd ';' -
    printf '\n'
    LC_ALL=C rpm -qa --root "$rootfs" --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | LC_ALL=C sort
} > "$outdir/rootfs-manifest.txt"
if [ -n "$support_rpm" ]; then
    python3 "$repo_dir/tools/test-port-build-contract.py" \
        --rootfs "$rootfs" --manifest "$outdir/rootfs-manifest.txt" \
        --version "$port_version" --rpm "$support_rpm"
else
    python3 "$repo_dir/tools/test-port-build-contract.py" \
        --rootfs "$rootfs" --manifest "$outdir/rootfs-manifest.txt" \
        --version "$port_version"
fi
archive="$outdir/x810-fedora-$fedora_release-rootfs.tar.gz"
# Normalize traversal order, ownership, mtimes and gzip header metadata so
# packaging itself is reproducible.  RPM database install times and compiler
# toolchain behavior are still captured by the manifest rather than claimed
# to be bit-for-bit reproducible across every host/kernel.
tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
    --format=pax --pax-option=delete=atime,delete=ctime \
    --xattrs --xattrs-include='security.capability' \
    -C "$rootfs" -cf - . | gzip -n -9 > "$archive"
python3 "$repo_dir/tools/verify-x810-rootfs-archive.py" \
    "$archive" --manifest "$outdir/rootfs-manifest.txt"

echo ">>> Done: $archive"
if [ "${#missing_assets[@]}" -gt 0 ]; then
    echo ""
    echo ">>> NOTE: not provided in this build:"
    for m in "${missing_assets[@]}"; do echo "    - $m"; done
    echo ">>> (release CI builds provide the firmware payload and kernel RPM;"
    echo ">>> personal firmware extracted from stock partitions remains local-only.)"
fi
