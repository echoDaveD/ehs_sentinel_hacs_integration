"""Decode structure payloads using declarative repository fields."""

import logging
import re
from datetime import date
from string import Formatter

_LOGGER = logging.getLogger(__name__)
_FORMAT_SPEC = re.compile(r"(?:0?[1-9][0-9]?)?[dxX]?")


def _nonnegative_integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative integer")
    return value


def _decode_field(payload, field):
    if not isinstance(field, dict):
        raise ValueError("Field definition must be a mapping")
    if field.keys() - {"offset", "length", "encoding", "endian", "terminator"}:
        raise ValueError("Unknown field option")
    offset = _nonnegative_integer(field.get("offset", 0))
    length = _nonnegative_integer(field.get("length", len(payload) - offset))
    if offset > len(payload) or offset + length > len(payload):
        raise ValueError("Field exceeds payload bounds")
    data = payload[offset:offset + length]
    encoding = field.get("encoding")
    if "terminator" in field and encoding != "ascii":
        raise ValueError("Only ASCII fields support terminators")
    if encoding in ("uint", "int"):
        endian = field.get("endian", "big")
        if not data or endian not in ("big", "little"):
            raise ValueError("Invalid integer field")
        return int.from_bytes(data, byteorder=endian, signed=encoding == "int")
    if encoding == "hex":
        return data.hex()
    if encoding == "bcd_date":
        if len(data) != 3:
            raise ValueError("BCD date fields must contain three bytes")
        if data == b"\x00\x00\x00":
            return "unknown"
        if any((byte >> 4) > 9 or (byte & 0x0F) > 9 for byte in data):
            raise ValueError("Invalid BCD date")
        year, month, day = (
            (byte >> 4) * 10 + (byte & 0x0F) for byte in data
        )
        return date(2000 + year, month, day).isoformat()
    if encoding == "ascii":
        if "terminator" in field:
            terminator = bytes.fromhex(field["terminator"])
            if not terminator or terminator not in data:
                raise ValueError("Missing ASCII terminator")
            data = data.split(terminator, 1)[0]
        if not data or not all(0x20 <= byte <= 0x7E for byte in data):
            raise ValueError("Expected nonempty printable ASCII")
        return data.decode("ascii")
    raise ValueError("Unsupported field encoding")


def decode_structure(payload: bytes, schema=None) -> str | None:
    """Return a formatted structure, or its complete hex payload on failure."""
    if not payload:
        return None
    raw_hex = payload.hex()
    if schema is None:
        return raw_hex
    try:
        if not isinstance(schema, dict):
            raise ValueError("Decode definition must be a mapping")
        if schema.keys() - {"min_length", "fields", "format", "fallback"}:
            raise ValueError("Unknown decode option")
        if schema.get("fallback", "hex") != "hex":
            raise ValueError("Only hex fallback is supported")
        if len(payload) < _nonnegative_integer(schema.get("min_length", 0)):
            raise ValueError("Payload shorter than min_length")
        fields = schema.get("fields")
        template = schema.get("format")
        if not isinstance(fields, dict) or not fields or not isinstance(template, str):
            raise ValueError("Expected fields and a format string")
        if any(not isinstance(name, str) or not name.isidentifier() for name in fields):
            raise ValueError("Field names must be identifiers")
        values = {name: _decode_field(payload, field) for name, field in fields.items()}
        for literal, name, spec, conversion in Formatter().parse(template):
            if name is not None:
                if name not in values or conversion is not None or not _FORMAT_SPEC.fullmatch(spec):
                    raise ValueError("Unsupported format field or specification")
        return template.format_map(values)
    except (ValueError, TypeError, KeyError, OverflowError):
        _LOGGER.debug("Structure decoding failed; keeping raw hex", exc_info=True)
        return raw_hex