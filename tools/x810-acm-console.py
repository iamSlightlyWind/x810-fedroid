#!/usr/bin/env python3
"""Connect to the X810 smoke image's USB ACM shell while holding DTR."""
import fcntl
import os
import select
import struct
import termios
import time

path = "/dev/ttyACM0"
deadline = time.monotonic() + 180
while not os.path.exists(path):
    if time.monotonic() > deadline:
        raise SystemExit("timed out waiting for /dev/ttyACM0")
    time.sleep(0.25)
fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
attrs = termios.tcgetattr(fd)
attrs[0] = 0
attrs[1] = 0
attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
attrs[3] = 0
attrs[4] = termios.B115200
attrs[5] = termios.B115200
attrs[6][termios.VMIN] = 0
attrs[6][termios.VTIME] = 1
termios.tcsetattr(fd, termios.TCSANOW, attrs)
try:
    fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", termios.TIOCM_DTR))
except OSError:
    pass
print("ACM open; holding DTR; waiting for prompt", flush=True)
os.write(fd, b"\n")
os.write(fd, b"echo X810_ACM_SHELL_OK\n")
until = time.monotonic() + 60
while time.monotonic() < until:
    ready, _, _ = select.select([fd], [], [], 1)
    if ready:
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if data:
            os.write(1, data)
os.close(fd)
