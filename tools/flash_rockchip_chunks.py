#!/usr/bin/env python3
"""Flash and verify a raw Rockchip image in bounded chunks on Windows."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import re
import subprocess
import sys
from pathlib import Path


SECTOR_SIZE = 512
COPY_BUFFER_SIZE = 4 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_BUFFER_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def run_tool(tool: Path, arguments: list[str], log_file) -> str:
    command = [str(tool), *arguments]
    log_file.write(f"\n$ {' '.join(command)}\n")
    log_file.flush()
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    log_file.write(output)
    log_file.flush()
    if result.returncode != 0:
        raise RuntimeError(
            f"upgrade_tool exited with {result.returncode}: {' '.join(arguments)}"
        )
    return output


def require_single_device(tool: Path, log_file) -> None:
    output = run_tool(tool, ["ld"], log_file)
    match = re.search(r"connected\((\d+)\)", output, re.IGNORECASE)
    if not match or int(match.group(1)) != 1:
        raise RuntimeError("Expected exactly one Rockusb device")


def write_source_chunk(
    image_stream,
    output_path: Path,
    offset: int,
    length: int,
) -> str:
    digest = hashlib.sha256()
    image_stream.seek(offset)
    remaining = length
    with output_path.open("wb") as output:
        while remaining:
            data = image_stream.read(min(COPY_BUFFER_SIZE, remaining))
            if not data:
                raise RuntimeError("Unexpected end of source image")
            output.write(data)
            digest.update(data)
            remaining -= len(data)
    return digest.hexdigest()


def flash(args: argparse.Namespace) -> int:
    tool = args.tool.resolve()
    image = args.image.resolve()
    work_dir = args.work_dir.resolve()
    if not tool.is_file():
        raise FileNotFoundError(tool)
    if not image.is_file():
        raise FileNotFoundError(image)

    image_size = image.stat().st_size
    chunk_size = args.chunk_mib * 1024 * 1024
    if image_size % SECTOR_SIZE:
        raise ValueError("Image size must be aligned to 512-byte sectors")
    if chunk_size <= 0 or chunk_size % SECTOR_SIZE:
        raise ValueError("Chunk size must be a positive multiple of 512 bytes")

    work_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    log_path = work_dir / f"chunk-flash-{stamp}.log"
    source_chunk = work_dir / "source-chunk.bin"
    device_chunk = work_dir / "device-chunk.bin"
    chunk_count = math.ceil(image_size / chunk_size)

    print(f"Image:      {image}")
    print(f"Image size: {image_size} bytes")
    print(f"Chunk size: {chunk_size} bytes")
    print(f"Chunks:     {chunk_count}")
    print(f"Log:        {log_path}")

    with log_path.open("a", encoding="utf-8", newline="") as log_file:
        require_single_device(tool, log_file)
        if args.loader is not None:
            loader = args.loader.resolve()
            if not loader.is_file():
                raise FileNotFoundError(loader)
            run_tool(tool, ["db", str(loader)], log_file)
            run_tool(tool, ["rfi"], log_file)

        with image.open("rb") as image_stream:
            for index in range(chunk_count):
                offset = index * chunk_size
                length = min(chunk_size, image_size - offset)
                start_lba = offset // SECTOR_SIZE
                sector_count = length // SECTOR_SIZE
                source_hash = write_source_chunk(
                    image_stream,
                    source_chunk,
                    offset,
                    length,
                )

                label = f"[{index + 1:02}/{chunk_count:02}]"
                print(
                    f"{label} write LBA 0x{start_lba:X}, "
                    f"{length // (1024 * 1024)} MiB",
                    flush=True,
                )
                run_tool(tool, ["wl", f"0x{start_lba:X}", str(source_chunk)], log_file)

                if not args.no_verify:
                    run_tool(
                        tool,
                        [
                            "rl",
                            f"0x{start_lba:X}",
                            f"0x{sector_count:X}",
                            str(device_chunk),
                        ],
                        log_file,
                    )
                    device_hash = sha256_file(device_chunk)
                    if device_hash != source_hash:
                        raise RuntimeError(
                            f"Chunk {index + 1} verification failed: "
                            f"source={source_hash}, device={device_hash}"
                        )
                    print(f"{label} verify OK {source_hash}", flush=True)

    source_chunk.unlink(missing_ok=True)
    device_chunk.unlink(missing_ok=True)
    print("All chunks written and verified successfully.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a raw image with upgrade_tool in verified chunks."
    )
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--loader", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--chunk-mib", type=int, default=128)
    parser.add_argument("--no-verify", action="store_true")
    return parser


def main() -> int:
    try:
        return flash(build_parser().parse_args())
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
