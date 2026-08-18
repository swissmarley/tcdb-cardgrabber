"""Image dimension probing using only the standard library.

Reads just enough of each file's header to recover width and height. This keeps
the package dependency-free and avoids decoding full images during a scan of
thousands of files.
"""

from __future__ import annotations

import struct
from pathlib import Path

# JPEG start-of-frame markers. C4 (Huffman table), C8 (JPEG extension) and
# CC (arithmetic coding conditioning) share the range but are not frame headers.
_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


class UnreadableImage(Exception):
    """Raised when a file is not an image we can measure."""


def probe(path: Path) -> tuple[int, int]:
    """Return (width, height) for an image file.

    Raises UnreadableImage if the format is unsupported or the header is
    malformed or truncated.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
            if len(head) < 16:
                raise UnreadableImage(f"file too short: {path}")

            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return _png(head, path)
            if head[:2] == b"\xff\xd8":
                return _jpeg(handle, path)
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp(handle, head, path)
            if head[:2] == b"BM":
                return _bmp(head, path)
    except OSError as exc:
        raise UnreadableImage(f"cannot read {path}: {exc}") from exc

    raise UnreadableImage(f"unrecognised image format: {path}")


def _png(head: bytes, path: Path) -> tuple[int, int]:
    if head[12:16] != b"IHDR":
        raise UnreadableImage(f"PNG missing IHDR: {path}")
    return struct.unpack(">II", head[16:24])


def _bmp(head: bytes, path: Path) -> tuple[int, int]:
    if len(head) < 26:
        raise UnreadableImage(f"BMP header truncated: {path}")
    width, height = struct.unpack("<ii", head[18:26])
    # A negative height signals a top-down row order; the magnitude is the size.
    return abs(width), abs(height)


def _jpeg(handle, path: Path) -> tuple[int, int]:
    """Walk the JPEG segment chain until a start-of-frame header appears."""
    handle.seek(2)
    while True:
        byte = handle.read(1)
        if not byte:
            raise UnreadableImage(f"JPEG ended before a frame header: {path}")
        if byte != b"\xff":
            continue

        # Runs of 0xFF are legal padding before a marker.
        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if not marker:
            raise UnreadableImage(f"JPEG ended mid-marker: {path}")

        code = marker[0]
        # Standalone markers carry no length field.
        if code in (0x01, 0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
            continue

        length_bytes = handle.read(2)
        if len(length_bytes) < 2:
            raise UnreadableImage(f"JPEG segment length truncated: {path}")
        (length,) = struct.unpack(">H", length_bytes)
        if length < 2:
            raise UnreadableImage(f"JPEG segment length invalid: {path}")

        if code in _SOF_MARKERS:
            frame = handle.read(5)
            if len(frame) < 5:
                raise UnreadableImage(f"JPEG frame header truncated: {path}")
            height, width = struct.unpack(">HH", frame[1:5])
            return width, height

        handle.seek(length - 2, 1)


def _webp(handle, head: bytes, path: Path) -> tuple[int, int]:
    """Handle the three WebP payload variants: lossy, lossless and extended."""
    chunk = head[12:16]

    if chunk == b"VP8X":
        handle.seek(24)
        data = handle.read(6)
        if len(data) < 6:
            raise UnreadableImage(f"VP8X header truncated: {path}")
        width = int.from_bytes(data[0:3], "little") + 1
        height = int.from_bytes(data[3:6], "little") + 1
        return width, height

    if chunk == b"VP8 ":
        handle.seek(26)
        data = handle.read(4)
        if len(data) < 4:
            raise UnreadableImage(f"VP8 header truncated: {path}")
        width, height = struct.unpack("<HH", data)
        return width & 0x3FFF, height & 0x3FFF

    if chunk == b"VP8L":
        handle.seek(21)
        data = handle.read(4)
        if len(data) < 4:
            raise UnreadableImage(f"VP8L header truncated: {path}")
        (bits,) = struct.unpack("<I", data)
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1

    raise UnreadableImage(f"unknown WebP chunk {chunk!r}: {path}")
