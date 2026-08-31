#!/usr/bin/env python3
"""Resilient serial logger for the Orange Pi 5 Plus debug UART."""

from __future__ import annotations

import argparse
import codecs
import datetime as dt
import sys
import time
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
VENDOR_DIR = REPO_ROOT / "tmp" / "rktools-win" / "python-packages"
if VENDOR_DIR.is_dir():
    sys.path.insert(0, str(VENDOR_DIR))

try:
    import serial
    from serial.tools import list_ports
except ModuleNotFoundError as exc:
    raise SystemExit(
        "pyserial is missing. Install it with: "
        f'"{sys.executable}" -m pip install --target "{VENDOR_DIR}" pyserial==3.5'
    ) from exc


DEFAULT_VID = 0x1A86
DEFAULT_PID = 0x7523


def parse_int(value: str) -> int:
    return int(value, 0)


def timestamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def available_ports() -> list:
    return sorted(list_ports.comports(), key=lambda item: item.device)


def print_ports() -> None:
    ports = available_ports()
    if not ports:
        print("No serial ports found.")
        return
    for item in ports:
        vid_pid = (
            f"{item.vid:04X}:{item.pid:04X}"
            if item.vid is not None and item.pid is not None
            else "----:----"
        )
        print(f"{item.device:8} {vid_pid} {item.description} | {item.hwid}")


def find_port(requested: str, vid: int, pid: int) -> str | None:
    if requested.lower() != "auto":
        return requested

    candidates = [
        item
        for item in available_ports()
        if item.vid == vid and item.pid == pid
    ]
    if len(candidates) == 1:
        return candidates[0].device
    if len(candidates) > 1:
        names = ", ".join(item.device for item in candidates)
        raise RuntimeError(f"Multiple matching serial ports found: {names}")
    return None


def event(event_file, message: str) -> None:
    line = f"[{timestamp()}] {message}\n"
    event_file.write(line)
    event_file.flush()
    print(line, end="", flush=True)


def open_serial(port: str, baudrate: int):
    connection = serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.25,
        write_timeout=1,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    connection.dtr = False
    connection.rts = False
    return connection


def monitor(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    session_name = dt.datetime.now().astimezone().strftime("serial-%Y%m%d-%H%M%S")
    raw_path = output_dir / f"{session_name}.bin"
    text_path = output_dir / f"{session_name}.log"
    event_path = output_dir / f"{session_name}.events.log"
    deadline = time.monotonic() + args.duration if args.duration > 0 else None

    print(f"Raw log:   {raw_path}")
    print(f"Text log:  {text_path}")
    print(f"Event log: {event_path}")

    with (
        raw_path.open("ab", buffering=0) as raw_file,
        text_path.open("a", encoding="utf-8", newline="") as text_file,
        event_path.open("a", encoding="utf-8", newline="") as event_file,
    ):
        connection = None
        active_port = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        event(
            event_file,
            f"Monitor started for {args.vid:04X}:{args.pid:04X}",
        )
        try:
            while deadline is None or time.monotonic() < deadline:
                if connection is None:
                    try:
                        active_port = find_port(args.port, args.vid, args.pid)
                        if active_port is None:
                            event(
                                event_file,
                                f"Waiting for USB serial {args.vid:04X}:{args.pid:04X}",
                            )
                            time.sleep(args.reconnect_delay)
                            continue
                        event(event_file, f"Opening {active_port}")
                        decoder.reset()
                        connection = open_serial(active_port, args.baudrate)
                        event(
                            event_file,
                            f"Opened {active_port} at {args.baudrate} baud, 8-N-1",
                        )
                        if args.poke:
                            connection.write(b"\r\n")
                            connection.flush()
                            event(event_file, "Sent CRLF after opening the port")
                    except (OSError, RuntimeError, serial.SerialException) as exc:
                        event(event_file, f"Open failed: {exc}")
                        connection = None
                        time.sleep(args.reconnect_delay)
                        continue

                try:
                    chunk = connection.read(max(connection.in_waiting, 1))
                    if not chunk:
                        continue
                    raw_file.write(chunk)
                    decoded = decoder.decode(chunk)
                    text_file.write(decoded)
                    text_file.flush()
                    output_buffer = getattr(sys.stdout, "buffer", None)
                    if output_buffer is not None:
                        output_buffer.write(chunk)
                        output_buffer.flush()
                    else:
                        sys.stdout.write(decoded)
                        sys.stdout.flush()
                except (OSError, serial.SerialException) as exc:
                    event(event_file, f"Disconnected from {active_port}: {exc}")
                    try:
                        connection.close()
                    except (OSError, serial.SerialException):
                        pass
                    connection = None
                    active_port = None
                    decoder.reset()
                    time.sleep(args.reconnect_delay)
        except KeyboardInterrupt:
            event(event_file, "Stopped by Ctrl+C")
        finally:
            if connection is not None and connection.is_open:
                connection.close()
            decoded_tail = decoder.decode(b"", final=True)
            if decoded_tail:
                text_file.write(decoded_tail)
                text_file.flush()
            event(event_file, "Monitor exited")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Log the Orange Pi debug UART with automatic reconnect."
    )
    parser.add_argument("--port", default="auto", help="COM port or 'auto'")
    parser.add_argument("--baudrate", type=int, default=1_500_000)
    parser.add_argument("--vid", type=parse_int, default=DEFAULT_VID)
    parser.add_argument("--pid", type=parse_int, default=DEFAULT_PID)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tmp" / "rktools-win" / "serial-logs",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=1.0,
        help="Seconds between reconnect attempts",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="Stop after this many seconds; zero means run until Ctrl+C",
    )
    parser.add_argument(
        "--poke",
        action="store_true",
        help="Send CRLF after opening or reopening the port",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List serial ports and exit",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list:
        print_ports()
        return 0
    return monitor(args)


if __name__ == "__main__":
    raise SystemExit(main())
