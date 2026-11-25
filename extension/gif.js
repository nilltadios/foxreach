"use strict";
// A small animated GIF writer: a fixed 6x7x6 colour cube, LZW, one second per frame.
// Uses typed-array slabs (1 byte/byte) instead of a JS number Array (8 bytes/byte in SpiderMonkey)
// and supports pre-quantized 1-byte/pixel frames to avoid retaining 4x raw RGBA buffers.

const GIF_CHUNK_BYTES = 65_536;

function createByteWriter() {
  const chunks = [];
  let buf = new Uint8Array(GIF_CHUNK_BYTES);
  let offset = 0;
  let total = 0;
  const flushChunk = () => {
    if (!offset) return;
    chunks.push(buf.subarray(0, offset));
    buf = new Uint8Array(GIF_CHUNK_BYTES);
    offset = 0;
  };
  const push = (byte) => {
    if (offset === buf.length) flushChunk();
    buf[offset++] = byte & 255;
    total++;
  };
  const pushBytes = (src, len = src.length) => {
    let i = 0;
    while (i < len) {
      if (offset === buf.length) flushChunk();
      const take = Math.min(len - i, buf.length - offset);
      buf.set(src.subarray(i, i + take), offset);
      offset += take;
      total += take;
      i += take;
    }
  };
  const word = (n) => {
    push(n & 255);
    push((n >> 8) & 255);
  };
  const toUint8Array = () => {
    const out = new Uint8Array(total);
    let pos = 0;
    for (const c of chunks) {
      out.set(c, pos);
      pos += c.length;
    }
    if (offset) out.set(buf.subarray(0, offset), pos);
    return out;
  };
  return { push, pushBytes, word, toUint8Array };
}

function quantizeRgba(rgba, width, height) {
  const pixels = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < rgba.length && p < pixels.length; i += 4, p++) {
    pixels[p] = Math.round(rgba[i] / 51) * 42 + Math.round(rgba[i + 1] / 42.5) * 6 + Math.round(rgba[i + 2] / 51);
  }
  return pixels;
}

function encodeGif(frames) {
  if (!frames?.length) throw new Error("No recorded frames to encode.");
  const { width, height } = frames[0];
  const out = createByteWriter();
  for (const ch of "GIF89a") out.push(ch.charCodeAt(0));
  out.word(width);
  out.word(height);
  out.push(0xf7);
  out.push(0);
  out.push(0); // global table of 256 colours
  for (let i = 0; i < 256; i++) {
    const r = Math.floor(i / 42);
    const g = Math.floor((i % 42) / 6);
    const b = i % 6;
    if (i < 252) {
      out.push(r * 51);
      out.push(Math.round(g * 42.5));
      out.push(b * 51);
    } else {
      out.push(0);
      out.push(0);
      out.push(0);
    }
  }
  out.push(0x21);
  out.push(0xff);
  out.push(11);
  for (const ch of "NETSCAPE2.0") out.push(ch.charCodeAt(0));
  out.push(3);
  out.push(1);
  out.push(0);
  out.push(0);
  out.push(0); // loop forever

  for (const frame of frames) {
    out.push(0x21);
    out.push(0xf9);
    out.push(4);
    out.push(0);
    out.word(100); // hundredths of a second
    out.push(0);
    out.push(0);
    out.push(0x2c);
    out.word(0);
    out.word(0);
    out.word(frame.width);
    out.word(frame.height);
    out.push(0);
    const pixels = frame.pixels ?? quantizeRgba(frame.rgba, frame.width, frame.height);
    lzw(pixels, out);
  }
  out.push(0x3b);
  return out.toUint8Array();
}

function lzw(pixels, out) {
  const MIN = 8;
  const CLEAR = 1 << MIN;
  const END = CLEAR + 1;
  out.push(MIN);
  const block = new Uint8Array(255);
  let blockLen = 0;
  let bits = 0;
  let nbits = 0;
  const flushBlock = () => {
    if (blockLen) {
      out.push(blockLen);
      out.pushBytes(block, blockLen);
      blockLen = 0;
    }
  };
  const emit = (code, size) => {
    bits |= code << nbits;
    nbits += size;
    while (nbits >= 8) {
      block[blockLen++] = bits & 255;
      bits >>>= 8;
      nbits -= 8;
      if (blockLen === 255) flushBlock();
    }
  };
  let size = MIN + 1;
  let next = END + 1;
  let table = new Map();
  emit(CLEAR, size);
  let prefix = pixels[0];
  for (let i = 1; i < pixels.length; i++) {
    const key = prefix * 256 + pixels[i];
    const known = table.get(key);
    if (known !== undefined) {
      prefix = known;
      continue;
    }
    emit(prefix, size);
    if (next === 4096) {
      emit(CLEAR, size);
      table = new Map();
      size = MIN + 1;
      next = END + 1;
    } else {
      table.set(key, next);
      if (next === 1 << size) size++;
      next++;
    }
    prefix = pixels[i];
  }
  emit(prefix, size);
  emit(END, size);
  if (nbits > 0) block[blockLen++] = bits & 255;
  flushBlock();
  out.push(0);
}

function bytesToBase64(bytes) {
  const parts = [];
  for (let i = 0; i < bytes.length; i += 0x8000) {
    parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)));
  }
  return btoa(parts.join(""));
}
