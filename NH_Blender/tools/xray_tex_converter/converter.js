#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

function fail(message) {
  console.error(message);
  process.exit(1);
}

function parseArgs(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (!key.startsWith('--')) {
      continue;
    }
    const name = key.slice(2);
    const value = argv[i + 1];
    if (value === undefined || value.startsWith('--')) {
      out[name] = '1';
    } else {
      out[name] = value;
      i += 1;
    }
  }
  return out;
}

function fourCC(buffer, offset) {
  return buffer.toString('ascii', offset, offset + 4).replace(/\0/g, '');
}

function decode565(value) {
  const r = (value >> 11) & 31;
  const g = (value >> 5) & 63;
  const b = value & 31;
  return [
    Math.round((r * 255) / 31),
    Math.round((g * 255) / 63),
    Math.round((b * 255) / 31),
    255,
  ];
}

function writePixel(out, width, height, x, y, rgba) {
  if (x >= width || y >= height) {
    return;
  }
  const idx = (y * width + x) * 4;
  out[idx] = rgba[0];
  out[idx + 1] = rgba[1];
  out[idx + 2] = rgba[2];
  out[idx + 3] = rgba[3];
}

function colorBlockColors(c0, c1, forceFourColor) {
  const a = decode565(c0);
  const b = decode565(c1);
  const colors = [a, b, [0, 0, 0, 255], [0, 0, 0, 255]];
  if (forceFourColor || c0 > c1) {
    colors[2] = [
      Math.round((2 * a[0] + b[0]) / 3),
      Math.round((2 * a[1] + b[1]) / 3),
      Math.round((2 * a[2] + b[2]) / 3),
      255,
    ];
    colors[3] = [
      Math.round((a[0] + 2 * b[0]) / 3),
      Math.round((a[1] + 2 * b[1]) / 3),
      Math.round((a[2] + 2 * b[2]) / 3),
      255,
    ];
  } else {
    colors[2] = [
      Math.round((a[0] + b[0]) / 2),
      Math.round((a[1] + b[1]) / 2),
      Math.round((a[2] + b[2]) / 2),
      255,
    ];
    colors[3] = [0, 0, 0, 0];
  }
  return colors;
}

function decodeColorBlock(buffer, offset, out, width, height, blockX, blockY, forceFourColor, alphaValues) {
  const c0 = buffer.readUInt16LE(offset);
  const c1 = buffer.readUInt16LE(offset + 2);
  const bits = buffer.readUInt32LE(offset + 4);
  const colors = colorBlockColors(c0, c1, forceFourColor);

  for (let row = 0; row < 4; row += 1) {
    for (let col = 0; col < 4; col += 1) {
      const pixel = row * 4 + col;
      const code = (bits >> (2 * pixel)) & 3;
      const rgba = colors[code].slice();
      if (alphaValues) {
        rgba[3] = alphaValues[pixel];
      }
      writePixel(out, width, height, blockX * 4 + col, blockY * 4 + row, rgba);
    }
  }
}

function decodeDXT1(buffer, dataOffset, width, height) {
  const out = Buffer.alloc(width * height * 4);
  const blocksWide = Math.max(1, Math.ceil(width / 4));
  const blocksHigh = Math.max(1, Math.ceil(height / 4));
  let offset = dataOffset;
  for (let by = 0; by < blocksHigh; by += 1) {
    for (let bx = 0; bx < blocksWide; bx += 1) {
      decodeColorBlock(buffer, offset, out, width, height, bx, by, false, null);
      offset += 8;
    }
  }
  return out;
}

function decodeDXT3(buffer, dataOffset, width, height) {
  const out = Buffer.alloc(width * height * 4);
  const blocksWide = Math.max(1, Math.ceil(width / 4));
  const blocksHigh = Math.max(1, Math.ceil(height / 4));
  let offset = dataOffset;
  for (let by = 0; by < blocksHigh; by += 1) {
    for (let bx = 0; bx < blocksWide; bx += 1) {
      const alpha = new Array(16);
      for (let i = 0; i < 16; i += 1) {
        const packed = buffer[offset + Math.floor(i / 2)];
        alpha[i] = Math.round((((packed >> ((i % 2) * 4)) & 15) * 255) / 15);
      }
      decodeColorBlock(buffer, offset + 8, out, width, height, bx, by, true, alpha);
      offset += 16;
    }
  }
  return out;
}

function dxt5AlphaValues(buffer, offset) {
  const a0 = buffer[offset];
  const a1 = buffer[offset + 1];
  const table = [a0, a1];
  if (a0 > a1) {
    table.push(
      Math.round((6 * a0 + 1 * a1) / 7),
      Math.round((5 * a0 + 2 * a1) / 7),
      Math.round((4 * a0 + 3 * a1) / 7),
      Math.round((3 * a0 + 4 * a1) / 7),
      Math.round((2 * a0 + 5 * a1) / 7),
      Math.round((1 * a0 + 6 * a1) / 7),
    );
  } else {
    table.push(
      Math.round((4 * a0 + 1 * a1) / 5),
      Math.round((3 * a0 + 2 * a1) / 5),
      Math.round((2 * a0 + 3 * a1) / 5),
      Math.round((1 * a0 + 4 * a1) / 5),
      0,
      255,
    );
  }

  let bits = 0n;
  for (let i = 5; i >= 0; i -= 1) {
    bits = (bits << 8n) + BigInt(buffer[offset + 2 + i]);
  }

  const alpha = new Array(16);
  for (let i = 0; i < 16; i += 1) {
    alpha[i] = table[Number((bits >> BigInt(3 * i)) & 7n)];
  }
  return alpha;
}

function decodeDXT5(buffer, dataOffset, width, height) {
  const out = Buffer.alloc(width * height * 4);
  const blocksWide = Math.max(1, Math.ceil(width / 4));
  const blocksHigh = Math.max(1, Math.ceil(height / 4));
  let offset = dataOffset;
  for (let by = 0; by < blocksHigh; by += 1) {
    for (let bx = 0; bx < blocksWide; bx += 1) {
      const alpha = dxt5AlphaValues(buffer, offset);
      decodeColorBlock(buffer, offset + 8, out, width, height, bx, by, true, alpha);
      offset += 16;
    }
  }
  return out;
}

function maskInfo(mask) {
  mask >>>= 0;
  if (!mask) {
    return null;
  }
  let shift = 0;
  while (((mask >>> shift) & 1) === 0 && shift < 32) {
    shift += 1;
  }
  let bits = 0;
  while (((mask >>> (shift + bits)) & 1) === 1 && shift + bits < 32) {
    bits += 1;
  }
  return { mask, shift, max: (1 << bits) - 1 };
}

function channelFromMask(pixel, info, fallback) {
  if (!info || !info.max) {
    return fallback;
  }
  return Math.round((((pixel >>> 0) & info.mask) >>> info.shift) * 255 / info.max);
}

function decodeUncompressed(buffer, dataOffset, width, height, header) {
  const bpp = header.rgbBitCount;
  if (![24, 32].includes(bpp)) {
    throw new Error(`Unsupported uncompressed DDS bit depth: ${bpp}`);
  }
  const bytesPerPixel = bpp / 8;
  const rowBytes = width * bytesPerPixel;
  const pitch = header.pitchOrLinearSize > 0 ? header.pitchOrLinearSize : rowBytes;
  const out = Buffer.alloc(width * height * 4);
  const rInfo = maskInfo(header.rMask);
  const gInfo = maskInfo(header.gMask);
  const bInfo = maskInfo(header.bMask);
  const aInfo = maskInfo(header.aMask);

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = dataOffset + y * pitch + x * bytesPerPixel;
      let pixel = 0;
      if (bytesPerPixel === 4) {
        pixel = buffer.readUInt32LE(offset);
      } else {
        pixel = buffer[offset] | (buffer[offset + 1] << 8) | (buffer[offset + 2] << 16);
      }
      const idx = (y * width + x) * 4;
      out[idx] = channelFromMask(pixel, rInfo, buffer[offset + 2]);
      out[idx + 1] = channelFromMask(pixel, gInfo, buffer[offset + 1]);
      out[idx + 2] = channelFromMask(pixel, bInfo, buffer[offset]);
      out[idx + 3] = channelFromMask(pixel, aInfo, 255);
    }
  }
  return out;
}

function parseDDS(buffer) {
  if (buffer.length < 128 || buffer.toString('ascii', 0, 4) !== 'DDS ') {
    throw new Error('Not a DDS file');
  }
  const header = {
    height: buffer.readUInt32LE(12),
    width: buffer.readUInt32LE(16),
    pitchOrLinearSize: buffer.readUInt32LE(20),
    fourCC: fourCC(buffer, 84),
    rgbBitCount: buffer.readUInt32LE(88),
    rMask: buffer.readUInt32LE(92),
    gMask: buffer.readUInt32LE(96),
    bMask: buffer.readUInt32LE(100),
    aMask: buffer.readUInt32LE(104),
    dataOffset: 128,
  };
  if (!header.width || !header.height) {
    throw new Error(`DDS image has invalid dimensions: ${header.width}x${header.height}`);
  }
  if (header.fourCC === 'DX10') {
    if (buffer.length < 148) {
      throw new Error('DDS DX10 header is truncated');
    }
    const dxgi = buffer.readUInt32LE(128);
    header.dataOffset = 148;
    if ([71, 72].includes(dxgi)) header.fourCC = 'DXT1';
    else if ([74, 75].includes(dxgi)) header.fourCC = 'DXT3';
    else if ([77, 78].includes(dxgi)) header.fourCC = 'DXT5';
    else throw new Error(`Unsupported DX10 DDS format: ${dxgi}`);
  }
  return header;
}

function decodeDDS(buffer) {
  const header = parseDDS(buffer);
  const fmt = header.fourCC.toUpperCase();
  if (fmt === 'DXT1' || fmt === 'BC1') {
    return { width: header.width, height: header.height, rgba: decodeDXT1(buffer, header.dataOffset, header.width, header.height), format: fmt };
  }
  if (fmt === 'DXT3' || fmt === 'BC2') {
    return { width: header.width, height: header.height, rgba: decodeDXT3(buffer, header.dataOffset, header.width, header.height), format: fmt };
  }
  if (fmt === 'DXT5' || fmt === 'BC3') {
    return { width: header.width, height: header.height, rgba: decodeDXT5(buffer, header.dataOffset, header.width, header.height), format: fmt };
  }
  if (!fmt) {
    return { width: header.width, height: header.height, rgba: decodeUncompressed(buffer, header.dataOffset, header.width, header.height, header), format: 'RGBA' };
  }
  throw new Error(`Unsupported DDS format: ${fmt}`);
}

function transformMode(rgba, mode) {
  if (mode === 'diffuse') {
    return rgba;
  }
  const out = Buffer.alloc(rgba.length);
  for (let i = 0; i < rgba.length; i += 4) {
    const r = rgba[i];
    const g = rgba[i + 1];
    const b = rgba[i + 2];
    const a = rgba[i + 3];
    if (mode === 'nohq') {
      out[i] = a;
      out[i + 1] = b;
      out[i + 2] = g;
      out[i + 3] = 255;
    } else if (mode === 'smdi') {
      out[i] = 255;
      out[i + 1] = r;
      out[i + 2] = 0;
      out[i + 3] = 255;
    } else {
      throw new Error(`Unsupported mode: ${mode}`);
    }
  }
  return out;
}

const crcTable = new Uint32Array(256);
for (let n = 0; n < 256; n += 1) {
  let c = n;
  for (let k = 0; k < 8; k += 1) {
    c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
  }
  crcTable[n] = c >>> 0;
}

function crc32(buffer) {
  let c = 0xffffffff;
  for (let i = 0; i < buffer.length; i += 1) {
    c = crcTable[(c ^ buffer[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, 'ascii');
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, crc]);
}

function encodePNG(width, height, rgba) {
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y += 1) {
    const rowStart = y * (width * 4 + 1);
    raw[rowStart] = 0;
    rgba.copy(raw, rowStart + 1, y * width * 4, (y + 1) * width * 4);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', zlib.deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

const args = parseArgs(process.argv);
const input = args.input;
const output = args.output;
const mode = (args.mode || 'diffuse').toLowerCase();

if (!input || !output) {
  fail('Usage: node converter.js --input <file.dds> --output <file.png> --mode <diffuse|nohq|smdi>');
}

try {
  const dds = fs.readFileSync(input);
  const decoded = decodeDDS(dds);
  const rgba = transformMode(decoded.rgba, mode);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, encodePNG(decoded.width, decoded.height, rgba));
  if (!fs.existsSync(output)) {
    throw new Error('PNG was not created');
  }
  console.log(`converted ${input} -> ${output} (${decoded.width}x${decoded.height}, ${decoded.format}, ${mode})`);
} catch (err) {
  fail(err && err.stack ? err.stack : String(err));
}
