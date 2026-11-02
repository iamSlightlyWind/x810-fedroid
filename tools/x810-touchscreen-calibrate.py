#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Show Mutter's touch location or pair touch/mouse points for calibration."""

import argparse
import fcntl
import json
import math
import os
import struct
import sys
import time
from pathlib import Path

import gi
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

DEVICE_NAME = "FTS1BA90A Touchscreen"
EVENT = struct.Struct("@llHHi")
EV_SYN = 0
EV_ABS = 3
SYN_REPORT = 0
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39


def find_device(requested=None):
    if requested:
        return requested
    for name_path in sorted(Path("/sys/class/input").glob("event*/device/name")):
        try:
            if name_path.read_text().strip() == DEVICE_NAME:
                event = name_path.parent.parent.name
                path = Path("/dev/input") / event
                if path.exists():
                    return str(path)
        except OSError:
            pass
    raise RuntimeError(f"could not find {DEVICE_NAME}")


def axis_range(fd, code):
    size = struct.calcsize("@6i")
    request = (2 << 30) | (size << 16) | (ord("E") << 8) | (0x40 + code)
    info = bytearray(size)
    fcntl.ioctl(fd, request, info, True)
    _, minimum, maximum, _, _, _ = struct.unpack("@6i", info)
    if maximum <= minimum:
        raise RuntimeError(f"invalid touchscreen axis range for {code:#x}")
    return minimum, maximum


def solve_3x3(matrix, rhs):
    work = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) < 1e-12:
            raise RuntimeError("mouse points must span both screen axes")
        work[column], work[pivot] = work[pivot], work[column]
        scale = work[column][column]
        work[column] = [value / scale for value in work[column]]
        for row in range(3):
            if row == column:
                continue
            scale = work[row][column]
            work[row] = [
                work[row][index] - scale * work[column][index]
                for index in range(4)
            ]
    return [work[row][3] for row in range(3)]


def fit_affine(pairs, x_min, x_max, y_min, y_max, width, height):
    if len(pairs) < 3:
        raise RuntimeError("at least three touch/mouse pairs are required")
    rows = []
    target_x = []
    target_y = []
    for pair in pairs:
        raw_x = (pair["raw_x"] - x_min) / (x_max - x_min)
        raw_y = (pair["raw_y"] - y_min) / (y_max - y_min)
        rows.append([raw_x, raw_y, 1.0])
        target_x.append(pair["target_u"])
        target_y.append(pair["target_v"])
    normal = [
        [sum(row[i] * row[j] for row in rows) for j in range(3)]
        for i in range(3)
    ]
    rhs_x = [sum(row[i] * value for row, value in zip(rows, target_x)) for i in range(3)]
    rhs_y = [sum(row[i] * value for row, value in zip(rows, target_y)) for i in range(3)]
    x_coeff = solve_3x3(normal, rhs_x)
    y_coeff = solve_3x3(normal, rhs_y)
    matrix_values = x_coeff + y_coeff

    residual_pixels = []
    for row, expected_x, expected_y in zip(rows, target_x, target_y):
        actual_x = sum(a * b for a, b in zip(x_coeff, row))
        actual_y = sum(a * b for a, b in zip(y_coeff, row))
        residual_pixels.append(
            math.hypot((actual_x - expected_x) * width, (actual_y - expected_y) * height)
        )
    return {
        "matrix_values": matrix_values,
        "matrix": " ".join(f"{value:.8f}" for value in matrix_values),
        "max_residual_pixels": max(residual_pixels),
        "max_residual_percent_diagonal": max(residual_pixels) / math.hypot(width, height) * 100,
    }


class TouchscreenHelper:
    def __init__(self, app, args):
        self.app = app
        self.args = args
        self.mode = args.mode
        self.device_path = find_device(args.device)
        self.fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
        self.x_min, self.x_max = axis_range(self.fd, ABS_MT_POSITION_X)
        self.y_min, self.y_max = axis_range(self.fd, ABS_MT_POSITION_Y)

        self.channel = GLib.IOChannel.unix_new(self.fd)
        self.channel.set_encoding(None)
        self.channel.set_buffered(False)
        GLib.io_add_watch(
            self.channel,
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_HUP,
            self.on_input,
        )

        self.buffer = bytearray()
        self.slot = 0
        self.raw_active = False
        self.raw_x = 0
        self.raw_y = 0
        self.have_x = False
        self.have_y = False
        self.raw_pending = None
        self.touch_pending = None
        self.touches = []
        self.mouse_points = []
        self.pending_touches = []
        self.pairs = []
        self.last_touch = None
        self.status_message = ""
        self.fit = None
        self.width = 1
        self.height = 1
        self.max_pair_distance = args.max_pair_distance

        self.window = Gtk.ApplicationWindow(
            application=app, title="X810 Touchscreen Helper"
        )
        self.window.set_decorated(False)
        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_focusable(True)
        self.area.set_draw_func(self.draw)
        self.window.set_child(self.area)

        gesture = Gtk.GestureClick.new()
        gesture.set_button(0)
        gesture.connect("pressed", self.on_pressed)
        self.area.add_controller(gesture)

        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self.on_key)
        self.window.add_controller(keys)

        self.window.fullscreen()
        self.window.present()
        self.area.grab_focus()
        self.show_status()

    def show_status(self):
        if self.mode == "current":
            print(
                "CURRENT: touch anywhere; the cyan dot shows Mutter's reported point. "
                "Esc closes.",
                flush=True,
            )
        else:
            print(
                "CALIBRATE: touch anywhere, then mouse-click the physical spot you "
                "touched. The nearest unmatched touch is paired. Enter fits; "
                "Backspace undoes the last pair; Esc closes.",
                flush=True,
            )
        self.area.queue_draw()

    def draw(self, area, cr, width, height):
        self.width = max(1, width)
        self.height = max(1, height)
        cr.set_source_rgb(0.035, 0.045, 0.060)
        cr.paint()

        cr.set_line_width(1)
        cr.set_source_rgba(0.60, 0.68, 0.77, 0.15)
        for step in range(1, 10):
            x = width * step / 10
            y = height * step / 10
            cr.move_to(x, 0)
            cr.line_to(x, height)
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()

        cr.select_font_face("Sans", 0, 0)
        cr.set_source_rgb(0.94, 0.95, 0.97)
        cr.set_font_size(25)
        cr.move_to(32, 46)
        if self.mode == "current":
            cr.show_text("CURRENT: cyan dot = where GNOME reports each touch")
        else:
            cr.show_text("CALIBRATE: touch anywhere, then mouse-click the physical spot")

        cr.set_font_size(17)
        cr.move_to(32, 76)
        if self.mode == "current":
            cr.show_text("Touch grid points; the marker stays after you lift your finger. Esc closes.")
        else:
            cr.show_text(
                "Touch points are cyan; mouse marks are amber; matching pair numbers identify pairs. "
                "No lines are drawn."
            )

        if self.mode == "current":
            if self.last_touch:
                px = self.last_touch["gdk_x"]
                py = self.last_touch["gdk_y"]
                self.draw_dot(cr, px, py, 9, (0.12, 0.88, 1.0), ring=True)
                cr.set_source_rgb(0.95, 0.96, 0.98)
                cr.set_font_size(18)
                cr.move_to(32, height - 35)
                cr.show_text(
                    f"GNOME x={px:.1f}, y={py:.1f} of {width}x{height}; "
                    f"raw=({self.last_touch['raw_x']}, {self.last_touch['raw_y']})"
                )
            else:
                cr.set_source_rgb(0.78, 0.81, 0.86)
                cr.set_font_size(18)
                cr.move_to(32, height - 35)
                cr.show_text("No touch recorded yet.")
            return

        for touch in self.touches:
            pair_id = touch.get("pair_id")
            label = f"T{touch['id']}" if pair_id is None else f"T{touch['id']}/P{pair_id}"
            self.draw_dot(
                cr, touch["gdk_x"], touch["gdk_y"], 8,
                (0.12, 0.88, 1.0), ring=True
            )
            self.draw_label(cr, touch["gdk_x"] + 11, touch["gdk_y"] - 10, label, (0.48, 0.92, 1.0))

        for point in self.mouse_points:
            pair_id = point.get("pair_id")
            label = f"M{point['id']}" if pair_id is None else f"M{point['id']}/P{pair_id}"
            self.draw_dot(
                cr, point["x"], point["y"], 8,
                (1.0, 0.64, 0.16), ring=True
            )
            self.draw_label(cr, point["x"] + 11, point["y"] + 15, label, (1.0, 0.76, 0.40))

        pending = len(self.pending_touches)
        cr.set_source_rgb(0.91, 0.92, 0.95)
        cr.set_font_size(18)
        cr.move_to(32, height - 38)
        cr.show_text(
            f"Touches: {len(self.touches)}   Mouse marks: {len(self.mouse_points)}   "
            f"Pairs: {len(self.pairs)}   Unpaired touches: {pending}"
        )
        if self.status_message:
            cr.set_font_size(16)
            cr.move_to(32, height - 12)
            cr.show_text(self.status_message[:130])

        if self.fit:
            cr.set_font_size(16)
            cr.move_to(32, 112)
            cr.show_text("LIBINPUT_CALIBRATION_MATRIX=" + self.fit["matrix"])

    @staticmethod
    def draw_dot(cr, x, y, radius, color, ring=False):
        cr.set_source_rgb(*color)
        cr.set_line_width(2)
        cr.arc(x, y, radius, 0, 6.283185307179586)
        if ring:
            cr.stroke()
            cr.arc(x, y, 2.5, 0, 6.283185307179586)
            cr.fill()
        else:
            cr.fill()

    @staticmethod
    def draw_label(cr, x, y, text, color):
        cr.set_source_rgb(*color)
        cr.set_font_size(16)
        cr.move_to(x, y)
        cr.show_text(text)

    def on_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.app.quit()
            return True
        if self.mode == "calibrate":
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_f, Gdk.KEY_F):
                self.finish_fit()
                return True
            if keyval == Gdk.KEY_BackSpace:
                self.undo_pair()
                return True
        return False

    def on_pressed(self, gesture, n_press, x, y):
        device = gesture.get_current_event_device()
        if device is None:
            return
        source = device.get_source()
        if source == Gdk.InputSource.TOUCHSCREEN:
            self.touch_pending = {
                "gdk_x": float(x),
                "gdk_y": float(y),
                "time": time.monotonic(),
            }
            self.maybe_record_touch()
        elif source == Gdk.InputSource.MOUSE and self.mode == "calibrate":
            self.record_mouse(float(x), float(y))

    def on_input(self, channel, condition):
        if condition & GLib.IO_HUP:
            print("Touchscreen device disconnected.", file=sys.stderr, flush=True)
            self.app.quit()
            return GLib.SOURCE_REMOVE
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError as e:
            print(f"Touchscreen read failed: {e}", file=sys.stderr, flush=True)
            self.app.quit()
            return GLib.SOURCE_REMOVE
        if not data:
            return GLib.SOURCE_CONTINUE
        self.buffer.extend(data)
        while len(self.buffer) >= EVENT.size:
            chunk = bytes(self.buffer[:EVENT.size])
            del self.buffer[:EVENT.size]
            sec, usec, typ, code, value = EVENT.unpack(chunk)
            self.consume(sec + usec / 1_000_000, typ, code, value)
        return GLib.SOURCE_CONTINUE

    def consume(self, event_time, typ, code, value):
        if typ == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif self.slot == 0 and code == ABS_MT_TRACKING_ID:
                if value >= 0:
                    self.raw_active = True
                    self.have_x = False
                    self.have_y = False
                    self.raw_pending = None
                    if self.touch_pending and time.monotonic() - self.touch_pending["time"] > 0.5:
                        self.touch_pending = None
                else:
                    self.raw_active = False
            elif self.slot == 0 and self.raw_active and code == ABS_MT_POSITION_X:
                self.raw_x = value
                self.have_x = True
            elif self.slot == 0 and self.raw_active and code == ABS_MT_POSITION_Y:
                self.raw_y = value
                self.have_y = True
        elif typ == EV_SYN and code == SYN_REPORT:
            if self.raw_active and self.raw_pending is None and self.have_x and self.have_y:
                self.raw_pending = {
                    "raw_x": self.raw_x,
                    "raw_y": self.raw_y,
                    "time": event_time,
                }
                self.maybe_record_touch()

    def maybe_record_touch(self):
        if self.raw_pending is None or self.touch_pending is None:
            return
        if abs(time.monotonic() - self.touch_pending["time"]) > 1.0:
            self.status_message = "Touch event did not pair with raw input; try again."
            self.touch_pending = None
            self.area.queue_draw()
            return
        width = max(1, self.area.get_width())
        height = max(1, self.area.get_height())
        touch = {
            "id": len(self.touches) + 1,
            "raw_x": self.raw_pending["raw_x"],
            "raw_y": self.raw_pending["raw_y"],
            "gdk_x": self.touch_pending["gdk_x"],
            "gdk_y": self.touch_pending["gdk_y"],
            "gdk_u": self.touch_pending["gdk_x"] / width,
            "gdk_v": self.touch_pending["gdk_y"] / height,
            "time": time.monotonic(),
            "pair_id": None,
        }
        self.touches.append(touch)
        self.last_touch = touch
        self.raw_pending = None
        self.touch_pending = None
        if self.mode == "calibrate":
            self.pending_touches.append(touch)
            self.status_message = (
                f"Touch T{touch['id']} saved at ({touch['gdk_x']:.1f}, "
                f"{touch['gdk_y']:.1f}); click the physical spot with the mouse."
            )
            print(
                f"Touch T{touch['id']}: GNOME=({touch['gdk_x']:.1f},"
                f"{touch['gdk_y']:.1f}) raw=({touch['raw_x']},{touch['raw_y']})",
                flush=True,
            )
        else:
            self.status_message = (
                f"GNOME reports ({touch['gdk_x']:.1f}, {touch['gdk_y']:.1f})."
            )
            print(
                f"GNOME reports ({touch['gdk_x']:.1f}, {touch['gdk_y']:.1f}); "
                f"raw=({touch['raw_x']},{touch['raw_y']})",
                flush=True,
            )
        self.area.queue_draw()
        self.save_report()

    def record_mouse(self, x, y):
        point = {
            "id": len(self.mouse_points) + 1,
            "x": x,
            "y": y,
            "time": time.monotonic(),
            "pair_id": None,
        }
        self.mouse_points.append(point)
        width = max(1, self.area.get_width())
        height = max(1, self.area.get_height())
        candidate = min(
            (
                (
                    math.hypot(touch["gdk_x"] - x, touch["gdk_y"] - y),
                    index,
                    touch,
                )
                for index, touch in enumerate(self.pending_touches)
            ),
            default=None,
            key=lambda item: item[0],
        )
        if candidate is None:
            self.status_message = "No unpaired touchscreen press is waiting."
            print(self.status_message, flush=True)
            self.area.queue_draw()
            return
        distance, index, touch = candidate
        limit = self.max_pair_distance or min(width, height) * 0.35
        if distance > limit:
            self.status_message = (
                f"Nearest touch is {distance:.0f}px away (limit {limit:.0f}px); "
                "mouse mark was not paired."
            )
            print(self.status_message, flush=True)
            self.area.queue_draw()
            return

        self.pending_touches.pop(index)
        pair_id = len(self.pairs) + 1
        touch["pair_id"] = pair_id
        point["pair_id"] = pair_id
        pair = {
            "id": pair_id,
            "touch_id": touch["id"],
            "mouse_id": point["id"],
            "distance_px": distance,
            "raw_x": touch["raw_x"],
            "raw_y": touch["raw_y"],
            "target_u": x / width,
            "target_v": y / height,
            "reported_u": touch["gdk_u"],
            "reported_v": touch["gdk_v"],
        }
        self.pairs.append(pair)
        self.fit = None
        self.status_message = (
            f"Paired T{touch['id']} with M{point['id']} by nearest position "
            f"({distance:.0f}px)."
        )
        print(
            f"Pair P{pair_id}: T{touch['id']} + M{point['id']}, "
            f"distance={distance:.1f}px, target=({x:.1f},{y:.1f})",
            flush=True,
        )
        self.area.queue_draw()
        self.save_report()

    def undo_pair(self):
        if not self.pairs:
            self.status_message = "There is no pair to undo."
            self.area.queue_draw()
            return
        pair = self.pairs.pop()
        for touch in self.touches:
            if touch["id"] == pair["touch_id"]:
                touch["pair_id"] = None
                self.pending_touches.append(touch)
                break
        for point in self.mouse_points:
            if point["id"] == pair["mouse_id"]:
                point["pair_id"] = None
                break
        self.fit = None
        self.status_message = f"Undid pair P{pair['id']}."
        self.area.queue_draw()
        self.save_report()

    def finish_fit(self):
        try:
            self.fit = fit_affine(
                self.pairs, self.x_min, self.x_max, self.y_min, self.y_max,
                max(1, self.area.get_width()), max(1, self.area.get_height())
            )
        except RuntimeError as e:
            self.status_message = str(e)
            print(self.status_message, flush=True)
            self.area.queue_draw()
            return
        self.status_message = (
            f"Fit from {len(self.pairs)} pairs; max residual "
            f"{self.fit['max_residual_percent_diagonal']:.3f}% of screen diagonal."
        )
        print("LIBINPUT_CALIBRATION_MATRIX=" + self.fit["matrix"], flush=True)
        print(self.status_message, flush=True)
        self.save_report()
        self.area.queue_draw()

    def save_report(self):
        if self.mode != "calibrate":
            return
        report = {
            "device": self.device_path,
            "screen": {"width": self.area.get_width(), "height": self.area.get_height()},
            "axis": {
                "x_min": self.x_min,
                "x_max": self.x_max,
                "y_min": self.y_min,
                "y_max": self.y_max,
            },
            "touches": self.touches,
            "mouse_points": self.mouse_points,
            "pairs": self.pairs,
            "fit": self.fit,
        }
        self.args.output.parent.mkdir(parents=True, exist_ok=True)
        self.args.output.write_text(json.dumps(report, indent=2) + "\n")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("current", "calibrate"),
        default="current",
        help="show current Mutter touch coordinates or collect touch/mouse calibration pairs",
    )
    parser.add_argument("--device", help="touch event node; detected by device name by default")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "x810-touch-calibration.json",
        help="calibration report path in calibrate mode",
    )
    parser.add_argument(
        "--max-pair-distance",
        type=float,
        default=None,
        help="maximum allowed touch/mouse pairing distance in logical pixels",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    app = Gtk.Application(application_id="org.slightlywind.X810TouchscreenHelper")
    state = {}

    def activate(application):
        try:
            state["helper"] = TouchscreenHelper(application, args)
        except Exception as e:
            print(f"x810-touchscreen-calibrate: {e}", file=sys.stderr, flush=True)
            application.quit()

    app.connect("activate", activate)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    raise SystemExit(main())
