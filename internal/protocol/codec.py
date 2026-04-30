"""Binary frame encoder/decoder for clipboard sync protocol.

Frame format (TLV + header):
  [2 bytes] magic: 0x4342 ("CB")
  [1 byte]  version
  [4 bytes] payload length L
  [4 bytes] message_id length N
  [N bytes] message_id (UUID hex)
  [1 byte]  source_device length M
  [M bytes] source_device
  [L bytes] payload (JSON-serialized content metadata + format descriptors)

Payload JSON structure:
{
  "types": {
    "TEXT": "<base64>",
    "HTML": "<base64>",
    "IMAGE_PNG": "<base64>"
  },
  "timestamp": 1234567890.123
}
"""

import base64
import json
import logging
import struct
import uuid
from io import BytesIO

logger = logging.getLogger(__name__)

from internal.clipboard.format import ClipboardContent, ContentType, SyncMessage

MAGIC = 0x4342  # "CB" for CopyBoard
VERSION = 1
HEADER_FMT = ">H B I"  # magic, version, payload_length (we'll use a simpler approach)
HEADER_SIZE = 7

_TYPE_NAME_MAP = {
    ContentType.TEXT: "TEXT",
    ContentType.HTML: "HTML",
    ContentType.RTF: "RTF",
    ContentType.IMAGE_PNG: "IMAGE_PNG",
}
_NAME_TYPE_MAP = {v: k for k, v in _TYPE_NAME_MAP.items()}


def encode_message(msg: SyncMessage) -> bytes:
    """Encode a SyncMessage to wire format bytes."""
    payload = {
        "types": {},
        "timestamp": msg.content.timestamp,
    }

    for content_type, data in msg.content.types.items():
        name = _TYPE_NAME_MAP[content_type]
        payload["types"][name] = base64.b64encode(data).decode("ascii")

    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    msg_id_bytes = (msg.msg_id or uuid.uuid4().hex).encode("ascii")
    src_str = msg.source_device[:255]
    while True:
        src_bytes = src_str.encode("utf-8")
        if len(src_bytes) <= 255:
            break
        src_str = src_str[:-1]  # trim one char to avoid mid-codepoint truncation

    buf = BytesIO()
    buf.write(struct.pack(HEADER_FMT, MAGIC, VERSION, len(payload_bytes)))
    buf.write(struct.pack(">I", len(msg_id_bytes)))
    buf.write(msg_id_bytes)
    buf.write(struct.pack(">B", len(src_bytes)))
    buf.write(src_bytes)
    buf.write(payload_bytes)

    return buf.getvalue()


def decode_message(data: bytes) -> SyncMessage | None:
    """Decode wire format bytes to a SyncMessage, or None if invalid."""
    if len(data) < HEADER_SIZE:
        return None

    magic, version, payload_len = struct.unpack_from(HEADER_FMT, data, 0)
    if magic != MAGIC:
        logger.debug("Frame magic mismatch: expected 0x%04x, got 0x%04x", MAGIC, magic)
        return None
    if version != VERSION:
        logger.debug("Frame version mismatch: expected %d, got %d", VERSION, version)
        return None

    offset = HEADER_SIZE

    if offset + 4 > len(data):
        return None
    msg_id_len = struct.unpack_from(">I", data, offset)[0]
    offset += 4

    if offset + msg_id_len > len(data):
        return None
    try:
        msg_id = data[offset:offset + msg_id_len].decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return None
    offset += msg_id_len

    if offset + 1 > len(data):
        return None
    src_len = struct.unpack_from(">B", data, offset)[0]
    offset += 1

    if offset + src_len > len(data):
        return None
    try:
        source_device = data[offset:offset + src_len].decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    offset += src_len

    if offset + payload_len > len(data):
        return None
    payload_bytes = data[offset:offset + payload_len]

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None

    content = ClipboardContent(
        timestamp=payload.get("timestamp", 0.0),
    )

    for name, b64_data in payload.get("types", {}).items():
        content_type = _NAME_TYPE_MAP.get(name)
        if content_type:
            try:
                content.types[content_type] = base64.b64decode(b64_data)
            except Exception:
                logger.debug("Invalid base64 for content type %s", name)
                continue

    return SyncMessage(
        content=content,
        msg_id=msg_id,
        source_device=source_device,
    )
