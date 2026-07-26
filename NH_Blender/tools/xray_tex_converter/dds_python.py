#!/usr/bin/env python3
"""Small dependency-free DDS to PNG converter for NH Blender tools."""

from __future__ import annotations

import argparse
import binascii
import os
import struct
import sys
import zlib


class DDSInvalidError(RuntimeError):
    """Raised when the DDS container is malformed or truncated."""


class DDSUnsupportedFormatError(RuntimeError):
    """Raised when the DDS container is valid but the pixel format is unsupported."""


def _invalid(reason: str) -> DDSInvalidError:
    return DDSInvalidError(f"Invalid DDS file: {reason}")


def _unsupported(fmt: str) -> DDSUnsupportedFormatError:
    return DDSUnsupportedFormatError(f"Unsupported DDS format: {fmt}")


def _read_u32(data: bytes, offset: int, reason: str = "header is truncated") -> int:
    if offset < 0 or offset + 4 > len(data):
        raise _invalid(reason)
    return struct.unpack_from("<I", data, offset)[0]


def _read_u16(data: bytes, offset: int, reason: str = "pixel data is truncated") -> int:
    if offset < 0 or offset + 2 > len(data):
        raise _invalid(reason)
    return struct.unpack_from("<H", data, offset)[0]


def _fourcc(data: bytes, offset: int) -> str:
    if offset < 0 or offset + 4 > len(data):
        raise _invalid("header is truncated")
    return data[offset:offset + 4].decode("ascii", errors="replace").replace("\x00", "").strip()


def _round_div(value: int, denom: int) -> int:
    return int((value / denom) + 0.5)


def _decode_565(value: int) -> tuple[int, int, int, int]:
    r = (value >> 11) & 31
    g = (value >> 5) & 63
    b = value & 31
    return (
        _round_div(r * 255, 31),
        _round_div(g * 255, 63),
        _round_div(b * 255, 31),
        255,
    )


def _write_pixel(out: bytearray, width: int, height: int, x: int, y: int, rgba) -> None:
    if x >= width or y >= height:
        return
    idx = (y * width + x) * 4
    out[idx:idx + 4] = bytes(rgba)


def _color_block_colors(c0: int, c1: int, force_four_color: bool):
    a = _decode_565(c0)
    b = _decode_565(c1)
    colors = [a, b, (0, 0, 0, 255), (0, 0, 0, 255)]
    if force_four_color or c0 > c1:
        colors[2] = (
            _round_div(2 * a[0] + b[0], 3),
            _round_div(2 * a[1] + b[1], 3),
            _round_div(2 * a[2] + b[2], 3),
            255,
        )
        colors[3] = (
            _round_div(a[0] + 2 * b[0], 3),
            _round_div(a[1] + 2 * b[1], 3),
            _round_div(a[2] + 2 * b[2], 3),
            255,
        )
    else:
        colors[2] = (
            _round_div(a[0] + b[0], 2),
            _round_div(a[1] + b[1], 2),
            _round_div(a[2] + b[2], 2),
            255,
        )
        colors[3] = (0, 0, 0, 0)
    return colors


def _decode_color_block(
    data: bytes,
    offset: int,
    out: bytearray,
    width: int,
    height: int,
    block_x: int,
    block_y: int,
    force_four_color: bool,
    alpha_values=None,
) -> None:
    if offset + 8 > len(data):
        raise _invalid("pixel data is truncated")
    c0, c1, bits = struct.unpack_from("<HHI", data, offset)
    colors = _color_block_colors(c0, c1, force_four_color)

    for row in range(4):
        for col in range(4):
            pixel = row * 4 + col
            code = (bits >> (2 * pixel)) & 3
            rgba = list(colors[code])
            if alpha_values is not None:
                rgba[3] = alpha_values[pixel]
            _write_pixel(out, width, height, block_x * 4 + col, block_y * 4 + row, rgba)


def _block_counts(width: int, height: int) -> tuple[int, int]:
    return max(1, (width + 3) // 4), max(1, (height + 3) // 4)


def _require_data_size(data: bytes, data_offset: int, required_bytes: int) -> None:
    if data_offset < 0 or data_offset > len(data):
        raise _invalid("pixel data offset is outside the file")
    if len(data) - data_offset < required_bytes:
        raise _invalid("pixel data is truncated")


def _decode_dxt1(data: bytes, data_offset: int, width: int, height: int) -> bytearray:
    blocks_wide, blocks_high = _block_counts(width, height)
    _require_data_size(data, data_offset, blocks_wide * blocks_high * 8)
    out = bytearray(width * height * 4)
    offset = data_offset
    for by in range(blocks_high):
        for bx in range(blocks_wide):
            _decode_color_block(data, offset, out, width, height, bx, by, False, None)
            offset += 8
    return out


def _decode_dxt3(data: bytes, data_offset: int, width: int, height: int) -> bytearray:
    blocks_wide, blocks_high = _block_counts(width, height)
    _require_data_size(data, data_offset, blocks_wide * blocks_high * 16)
    out = bytearray(width * height * 4)
    offset = data_offset
    for by in range(blocks_high):
        for bx in range(blocks_wide):
            alpha = [0] * 16
            for i in range(16):
                packed = data[offset + (i // 2)]
                alpha[i] = _round_div(((packed >> ((i % 2) * 4)) & 15) * 255, 15)
            _decode_color_block(data, offset + 8, out, width, height, bx, by, True, alpha)
            offset += 16
    return out


def _dxt5_alpha_values(data: bytes, offset: int) -> list[int]:
    if offset + 8 > len(data):
        raise _invalid("pixel data is truncated")
    a0 = data[offset]
    a1 = data[offset + 1]
    table = [a0, a1]
    if a0 > a1:
        table.extend([
            _round_div(6 * a0 + 1 * a1, 7),
            _round_div(5 * a0 + 2 * a1, 7),
            _round_div(4 * a0 + 3 * a1, 7),
            _round_div(3 * a0 + 4 * a1, 7),
            _round_div(2 * a0 + 5 * a1, 7),
            _round_div(1 * a0 + 6 * a1, 7),
        ])
    else:
        table.extend([
            _round_div(4 * a0 + 1 * a1, 5),
            _round_div(3 * a0 + 2 * a1, 5),
            _round_div(2 * a0 + 3 * a1, 5),
            _round_div(1 * a0 + 4 * a1, 5),
            0,
            255,
        ])

    bits = int.from_bytes(data[offset + 2:offset + 8], "little")
    alpha = [0] * 16
    for i in range(16):
        alpha[i] = table[(bits >> (3 * i)) & 7]
    return alpha


def _decode_dxt5(data: bytes, data_offset: int, width: int, height: int) -> bytearray:
    blocks_wide, blocks_high = _block_counts(width, height)
    _require_data_size(data, data_offset, blocks_wide * blocks_high * 16)
    out = bytearray(width * height * 4)
    offset = data_offset
    for by in range(blocks_high):
        for bx in range(blocks_wide):
            alpha = _dxt5_alpha_values(data, offset)
            _decode_color_block(data, offset + 8, out, width, height, bx, by, True, alpha)
            offset += 16
    return out


def _mask_info(mask: int):
    mask &= 0xFFFFFFFF
    if not mask:
        return None
    shift = 0
    while shift < 32 and ((mask >> shift) & 1) == 0:
        shift += 1
    bits = 0
    while shift + bits < 32 and ((mask >> (shift + bits)) & 1) == 1:
        bits += 1
    return {"mask": mask, "shift": shift, "max": (1 << bits) - 1}


def _channel_from_mask(pixel: int, info, fallback: int) -> int:
    if not info or not info["max"]:
        return fallback
    return _round_div(((pixel & info["mask"]) >> info["shift"]) * 255, info["max"])


def _decode_uncompressed(data: bytes, data_offset: int, width: int, height: int, header) -> bytearray:
    bpp = header["rgb_bit_count"]
    if bpp not in (24, 32):
        raise _unsupported(f"RGBA{bpp}")

    bytes_per_pixel = bpp // 8
    row_bytes = width * bytes_per_pixel
    pitch = header["pitch_or_linear_size"] if header["pitch_or_linear_size"] > 0 else row_bytes
    if pitch < row_bytes:
        raise _invalid(f"row pitch is smaller than row data: {pitch} < {row_bytes}")
    _require_data_size(data, data_offset, pitch * height)

    out = bytearray(width * height * 4)
    r_info = _mask_info(header["r_mask"])
    g_info = _mask_info(header["g_mask"])
    b_info = _mask_info(header["b_mask"])
    a_info = _mask_info(header["a_mask"])

    for y in range(height):
        row_offset = data_offset + y * pitch
        for x in range(width):
            offset = row_offset + x * bytes_per_pixel
            if bytes_per_pixel == 4:
                pixel = _read_u32(data, offset, "pixel data is truncated")
            else:
                if offset + 3 > len(data):
                    raise _invalid("pixel data is truncated")
                pixel = data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)
            idx = (y * width + x) * 4
            out[idx] = _channel_from_mask(pixel, r_info, data[offset + 2])
            out[idx + 1] = _channel_from_mask(pixel, g_info, data[offset + 1])
            out[idx + 2] = _channel_from_mask(pixel, b_info, data[offset])
            out[idx + 3] = _channel_from_mask(pixel, a_info, 255)
    return out


def _parse_dds(data: bytes) -> dict:
    if len(data) < 128:
        raise _invalid("file is too small for a DDS header")
    if data[:4] != b"DDS ":
        raise _invalid("missing DDS magic")

    _read_u32(data, 4)
    pixel_format_size = _read_u32(data, 76)
    if pixel_format_size != 32:
        raise _invalid(f"unexpected pixel format size: {pixel_format_size}")

    header = {
        "height": _read_u32(data, 12),
        "width": _read_u32(data, 16),
        "pitch_or_linear_size": _read_u32(data, 20),
        "pixel_format_flags": _read_u32(data, 80),
        "fourcc": _fourcc(data, 84),
        "rgb_bit_count": _read_u32(data, 88),
        "r_mask": _read_u32(data, 92),
        "g_mask": _read_u32(data, 96),
        "b_mask": _read_u32(data, 100),
        "a_mask": _read_u32(data, 104),
        "data_offset": 128,
    }
    if not header["width"] or not header["height"]:
        raise _invalid(f"DDS image has invalid dimensions: {header['width']}x{header['height']}")

    if header["fourcc"].upper() == "DX10":
        if len(data) < 148:
            raise _invalid("DDS DX10 header is truncated")
        dxgi_format = _read_u32(data, 128, "DDS DX10 header is truncated")
        header["data_offset"] = 148
        if dxgi_format in (71, 72):
            header["fourcc"] = "DXT1"
        elif dxgi_format in (74, 75):
            header["fourcc"] = "DXT3"
        elif dxgi_format in (77, 78):
            header["fourcc"] = "DXT5"
        else:
            raise _unsupported(f"DX10/{dxgi_format}")
    return header


def _header_has_alpha_channel(header: dict) -> bool:
    fmt = str(header.get("fourcc") or "").upper()
    if fmt in ("DXT2", "DXT3", "DXT4", "DXT5", "BC2", "BC3"):
        return True
    if fmt in ("DXT1", "BC1"):
        return bool(int(header.get("pixel_format_flags", 0)) & 0x1)
    if not fmt:
        return bool(int(header.get("a_mask", 0)) or (int(header.get("pixel_format_flags", 0)) & 0x1))
    return False


def dds_has_alpha_channel(input_path: str) -> bool:
    """Return whether the DDS pixel format carries an alpha channel."""
    with open(input_path, "rb") as f:
        data = f.read(148)
    return _header_has_alpha_channel(_parse_dds(data))


def _decode_dds(data: bytes) -> tuple[int, int, bytearray, str]:
    header = _parse_dds(data)
    width = header["width"]
    height = header["height"]
    fmt = header["fourcc"].upper()
    data_offset = header["data_offset"]

    if fmt in ("DXT1", "BC1"):
        return width, height, _decode_dxt1(data, data_offset, width, height), fmt
    if fmt in ("DXT3", "BC2"):
        return width, height, _decode_dxt3(data, data_offset, width, height), fmt
    if fmt in ("DXT5", "BC3"):
        return width, height, _decode_dxt5(data, data_offset, width, height), fmt
    if not fmt:
        return width, height, _decode_uncompressed(data, data_offset, width, height, header), "RGBA"
    raise _unsupported(fmt)


def _transform_mode(rgba: bytearray, mode: str) -> bytearray:
    mode = (mode or "diffuse").lower()
    if mode == "diffuse":
        return rgba
    out = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        r = rgba[i]
        g = rgba[i + 1]
        b = rgba[i + 2]
        a = rgba[i + 3]
        if mode == "nohq":
            out[i] = a
            out[i + 1] = b
            out[i + 2] = g
            out[i + 3] = 255
        elif mode == "smdi":
            out[i] = 255
            out[i + 1] = r
            out[i + 2] = 0
            out[i + 3] = 255
        else:
            raise RuntimeError(f"Unsupported mode: {mode}")
    return out


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _encode_png(width: int, height: int, rgba: bytearray, include_alpha: bool = True) -> bytes:
    if width <= 0 or height <= 0:
        raise _invalid(f"PNG image has invalid dimensions: {width}x{height}")
    expected = width * height * 4
    if len(rgba) != expected:
        raise _invalid(f"decoded pixel buffer has unexpected size: {len(rgba)} != {expected}")

    channels = 4 if include_alpha else 3
    stride = width * channels
    raw = bytearray((stride + 1) * height)
    for y in range(height):
        row_start = y * (stride + 1)
        raw[row_start] = 0
        if include_alpha:
            src_start = y * stride
            raw[row_start + 1:row_start + 1 + stride] = rgba[src_start:src_start + stride]
        else:
            dst = row_start + 1
            src = y * width * 4
            for _x in range(width):
                raw[dst:dst + 3] = rgba[src:src + 3]
                dst += 3
                src += 4

    color_type = 6 if include_alpha else 2
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", ihdr),
        _png_chunk(b"IDAT", zlib.compress(bytes(raw))),
        _png_chunk(b"IEND", b""),
    ])


def convert_dds_to_png(input_path: str, output_path: str, mode: str = "diffuse") -> str:
    """Convert a DDS file to PNG and return the output path."""
    try:
        with open(input_path, "rb") as f:
            data = f.read()
        source_has_alpha = _header_has_alpha_channel(_parse_dds(data))
        width, height, rgba, _fmt = _decode_dds(data)
        mode = (mode or "diffuse").lower()
        rgba = _transform_mode(rgba, mode)
        include_alpha = bool(source_has_alpha and mode == "diffuse")
        folder = os.path.dirname(output_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(_encode_png(width, height, rgba, include_alpha=include_alpha))
    except (DDSInvalidError, DDSUnsupportedFormatError):
        raise
    except OSError as e:
        raise RuntimeError(str(e))
    except (struct.error, IndexError, ValueError) as e:
        raise _invalid(str(e))
    return output_path


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Convert DDS textures to PNG without external dependencies.")
    parser.add_argument("--input", required=True, help="Source DDS file")
    parser.add_argument("--output", required=True, help="Target PNG file")
    parser.add_argument("--mode", default="diffuse", help="diffuse, nohq, or smdi")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        convert_dds_to_png(args.input, args.output, args.mode)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"converted {args.input} -> {args.output} ({(args.mode or 'diffuse').lower()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
