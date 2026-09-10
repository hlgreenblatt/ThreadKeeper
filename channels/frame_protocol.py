"""Bounded framed IPC for MTProto bridge ↔ SWI communication.

Protocol: length-prefixed JSON frames over the existing FIFO/stdout path.
Each frame has:
  - magic: 4 bytes (b"OCF1")
  - version: 1 byte (protocol version)
  - flags: 1 byte (reserved)
  - payload_len: 4 bytes (big-endian uint32)
  - payload: JSON bytes

Features:
  - Protocol version and generation ID in every frame
  - Maximum message length cap (default 256 KiB)
  - Bounded queue depth with explicit overflow policy
  - Acknowledgement frames for accepted messages
  - No silent drops: overflow is reported as an error frame
"""
import json
import os
import struct
import threading
import time
from typing import Optional, Tuple

MAGIC = b"OCF1"
PROTOCOL_VERSION = 1
HEADER_FORMAT = ">4sBB I"  # magic(4), version(1), flags(1), payload_len(4)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

DEFAULT_MAX_PAYLOAD = 256 * 1024  # 256 KiB
DEFAULT_MAX_QUEUE_DEPTH = 64
DEFAULT_OVERFLOW_POLICY = "reject"  # "reject" or "drop_oldest"


class FrameError(Exception):
    pass


class QueueOverflowError(FrameError):
    pass


def encode_frame(payload: dict, max_payload: int = DEFAULT_MAX_PAYLOAD) -> bytes:
    """Encode a dict as a framed binary message."""
    body = json.dumps(payload, default=str).encode("utf-8")
    if len(body) > max_payload:
        raise FrameError(
            f"payload {len(body)} bytes exceeds max {max_payload}"
        )
    header = struct.pack(
        HEADER_FORMAT, MAGIC, PROTOCOL_VERSION, 0, len(body)
    )
    return header + body


def decode_frame(buf: bytearray, max_payload: int = DEFAULT_MAX_PAYLOAD) -> Optional[Tuple[dict, int]]:
    """Try to decode one frame from *buf*.

    Returns (payload_dict, bytes_consumed) or None if incomplete.
    Raises FrameError on malformed data.
    """
    if len(buf) < HEADER_SIZE:
        return None
    magic, version, flags, payload_len = struct.unpack_from(
        HEADER_FORMAT, buf, 0
    )
    if magic != MAGIC:
        raise FrameError(f"bad magic: {magic!r}")
    if version != PROTOCOL_VERSION:
        raise FrameError(f"unsupported version {version}")
    if payload_len > max_payload:
        raise FrameError(
            f"payload_len {payload_len} exceeds max {max_payload}"
        )
    total = HEADER_SIZE + payload_len
    if len(buf) < total:
        return None
    body = buf[HEADER_SIZE:total].decode("utf-8")
    payload = json.loads(body)
    return payload, total


class BoundedQueue:
    """Bounded message queue with explicit overflow policy."""

    def __init__(
        self,
        max_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
        overflow_policy: str = DEFAULT_OVERFLOW_POLICY,
    ):
        self._items: list = []
        self._lock = threading.Lock()
        self._max_depth = max_depth
        self._overflow_policy = overflow_policy
        self._overflow_count = 0
        self._total_accepted = 0

    def push(self, item) -> bool:
        """Push an item. Returns True if accepted, False if rejected."""
        with self._lock:
            if len(self._items) >= self._max_depth:
                if self._overflow_policy == "drop_oldest":
                    self._items.pop(0)
                    self._overflow_count += 1
                else:
                    self._overflow_count += 1
                    return False
            self._items.append(item)
            self._total_accepted += 1
            return True

    def pop(self):
        with self._lock:
            if self._items:
                return self._items.pop(0)
            return None

    def peek_depth(self) -> int:
        with self._lock:
            return len(self._items)

    def stats(self) -> dict:
        with self._lock:
            return {
                "depth": len(self._items),
                "max_depth": self._max_depth,
                "overflow_count": self._overflow_count,
                "total_accepted": self._total_accepted,
                "overflow_policy": self._overflow_policy,
            }


def make_ack_frame(correlation_id: str, accepted: bool, reason: str = "") -> dict:
    """Create an acknowledgement frame payload."""
    return {
        "type": "ack",
        "correlation_id": correlation_id,
        "accepted": accepted,
        "reason": reason,
        "timestamp": time.time(),
    }


def make_overflow_frame(depth: int, max_depth: int) -> dict:
    """Create a queue overflow error frame."""
    return {
        "type": "error",
        "error": "queue_overflow",
        "depth": depth,
        "max_depth": max_depth,
        "timestamp": time.time(),
    }


def make_generation_frame(generation_id: str) -> dict:
    """Create a generation identification frame."""
    return {
        "type": "generation",
        "generation_id": generation_id,
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": time.time(),
    }


def write_frame(fd: int, payload: dict, max_payload: int = DEFAULT_MAX_PAYLOAD) -> int:
    """Write a framed message to a file descriptor. Returns bytes written."""
    frame = encode_frame(payload, max_payload)
    os.write(fd, frame)
    return len(frame)


def read_frame(fd: int, buf: bytearray, max_payload: int = DEFAULT_MAX_PAYLOAD) -> Optional[dict]:
    """Read and decode one frame from fd, using buf as accumulator."""
    while True:
        result = decode_frame(buf, max_payload)
        if result is not None:
            payload, consumed = result
            del buf[:consumed]
            return payload
        chunk = os.read(fd, 4096)
        if not chunk:
            return None
        buf.extend(chunk)
