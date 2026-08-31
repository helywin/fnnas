#!/usr/bin/env python3
"""Log in to a serial console and run marker-delimited diagnostics."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

from serial_monitor import (
    DEFAULT_PID,
    DEFAULT_VID,
    REPO_ROOT,
    find_port,
    open_serial,
    parse_int,
)


def emit(raw_file, text_file, data: bytes) -> None:
    raw_file.write(data)
    raw_file.flush()
    text_file.write(data.decode("utf-8", errors="replace"))
    text_file.flush()
    output_buffer = getattr(sys.stdout, "buffer", None)
    if output_buffer is not None:
        output_buffer.write(data)
        output_buffer.flush()


def read_until(connection, patterns: list[bytes], timeout: float, raw_file, text_file):
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        chunk = connection.read(max(connection.in_waiting, 1))
        if not chunk:
            continue
        received.extend(chunk)
        emit(raw_file, text_file, chunk)
        for pattern in patterns:
            if pattern in received:
                return bytes(received), pattern
    expected = ", ".join(pattern.decode("ascii", errors="replace") for pattern in patterns)
    raise TimeoutError(f"Timed out waiting for: {expected}")


def send_line(connection, value: str) -> None:
    connection.write(value.encode("utf-8") + b"\r")
    connection.flush()


def login(connection, args, password: str, raw_file, text_file) -> None:
    connection.write(b"\r\n")
    connection.flush()
    _, matched = read_until(
        connection,
        [b"login:", b"# "],
        args.login_timeout,
        raw_file,
        text_file,
    )
    if matched == b"# ":
        return

    send_line(connection, args.username)
    _, matched = read_until(
        connection,
        [b"Password:", b"# "],
        args.login_timeout,
        raw_file,
        text_file,
    )
    if matched == b"Password:":
        send_line(connection, password)
        output, matched = read_until(
            connection,
            [b"# ", b"Login incorrect"],
            args.login_timeout,
            raw_file,
            text_file,
        )
        if matched == b"Login incorrect":
            raise RuntimeError("Serial login was rejected")
        if b"# " not in output:
            raise RuntimeError("Root shell prompt was not detected")


def run_command(connection, command: str, timeout: float, raw_file, text_file) -> int:
    token = f"{time.time_ns():x}"
    done_marker = f"__CODEX_DONE_{token}__".encode("ascii")
    wrapped = (
        f"{command}; rc=$?; "
        f"printf '\\n__CODEX_%s_{token}=%s__\\n' 'RC' \"$rc\"; "
        f"printf '__CODEX_%s_{token}__\\n' 'DONE'"
    )
    send_line(connection, wrapped)
    output, _ = read_until(
        connection,
        [done_marker],
        timeout,
        raw_file,
        text_file,
    )
    match = re.search(rb"__CODEX_RC_" + token.encode("ascii") + rb"=(\d+)__", output)
    if not match:
        raise RuntimeError("Command completion marker was missing")
    return int(match.group(1))


def execute(args: argparse.Namespace) -> int:
    password = os.environ.get(args.password_env)
    if password is None:
        raise RuntimeError(f"Missing environment variable: {args.password_env}")

    port = find_port(args.port, args.vid, args.pid)
    if port is None:
        raise RuntimeError(f"USB serial {args.vid:04X}:{args.pid:04X} was not found")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("serial-exec-%Y%m%d-%H%M%S")
    raw_path = output_dir / f"{stamp}.bin"
    text_path = output_dir / f"{stamp}.log"
    print(f"Port:       {port}")
    print(f"Transcript: {text_path}")

    with (
        raw_path.open("ab", buffering=0) as raw_file,
        text_path.open("a", encoding="utf-8", newline="") as text_file,
        open_serial(port, args.baudrate) as connection,
    ):
        login(connection, args, password, raw_file, text_file)
        result = 0
        for command in args.command:
            result = run_command(
                connection,
                command,
                args.command_timeout,
                raw_file,
                text_file,
            )
            if result != 0:
                return result
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Log in to a root serial console and run commands."
    )
    parser.add_argument("--port", default="auto")
    parser.add_argument("--baudrate", type=int, default=1_500_000)
    parser.add_argument("--vid", type=parse_int, default=DEFAULT_VID)
    parser.add_argument("--pid", type=parse_int, default=DEFAULT_PID)
    parser.add_argument("--username", default="root")
    parser.add_argument("--password-env", default="FNNAS_SERIAL_PASSWORD")
    parser.add_argument("--login-timeout", type=float, default=15)
    parser.add_argument("--command-timeout", type=float, default=60)
    parser.add_argument("--command", action="append", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tmp" / "rktools-win" / "serial-logs",
    )
    return parser


def main() -> int:
    try:
        return execute(build_parser().parse_args())
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
