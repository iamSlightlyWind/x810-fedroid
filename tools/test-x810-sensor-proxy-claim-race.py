#!/usr/bin/env python3
"""Regression test for the slow-SSC early-claim race in iio-sensor-proxy."""

from __future__ import annotations

import sys
from pathlib import Path


def function_body(source: str, name: str, next_name: str) -> str:
    start = source.index(name)
    end = source.index(next_name, start + len(name))
    return source[start:end]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} PATCHED_IIO_SOURCE_DIR")

    source_path = Path(sys.argv[1]) / "src/iio-sensor-proxy.c"
    source = source_path.read_text(encoding="utf-8")
    bus_acquired = function_body(
        source,
        "bus_acquired_handler (GDBusConnection",
        "name_acquired_handler (GDBusConnection",
    )
    name_acquired = function_body(
        source,
        "name_acquired_handler (GDBusConnection",
        "setup_dbus (SensorData",
    )
    claim_handler = function_body(
        source,
        "handle_generic_method_call (SensorData",
        "handle_method_call (GDBusConnection",
    )

    first_object_registration = bus_acquired.index("g_dbus_connection_register_object")
    for initialization in (
        "data->client = g_udev_client_new (subsystems)",
        "data->clients[i] = create_clients_hash_table ()",
        "data->sensor_startup_dbus_invocations_delayed[i] = g_ptr_array_new ()",
    ):
        assert bus_acquired.index(initialization) < first_object_registration, (
            f"{initialization!r} must precede D-Bus object publication"
        )

    assert "data->sensor_startup_dbus_invocations_delayed[i] = g_ptr_array_new ()" not in name_acquired
    assert "driver_type_exists (data, driver_type) &&\n\t\t    DEVICE_FOR_TYPE(driver_type) != NULL" in claim_handler
    opened = name_acquired.index("DEVICE_FOR_TYPE(i) = sensor_device")
    start_if_claimed = name_acquired.index("if (g_hash_table_size (data->clients[i]) > 0)")
    assert start_if_claimed > opened
    assert "driver_set_polling (sensor_device, TRUE)" in name_acquired[start_if_claimed:]

    print("PASS: SSC claims are initialized before D-Bus publication, guarded until device open, and resumed after discovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
