"use strict";
// A small animated GIF writer: a fixed 6x7x6 colour cube, LZW, one second per frame.

function encodeGif(frames) {
  const { width, height } = frames[0];
  const out = [];
  const word = (n) => out.push(n & 255, (n >> 8) & 255);
  for (const ch of "GIF89a") out.push(ch.charCodeAt(0));
  word(width);
  word(height);
  out.push(0xf7, 0, 0); // global table of 256 colours
  for (let i = 0; i < 256; i++) {
    const r = Math.floor(i / 42);
    const g = Math.floor((i % 42) / 6);
    const b = i % 6;
    if (i < 252) out.push(r * 51, Math.round(g * 42.5), b * 51);
    else out.push(0, 0, 0);
  }
  out.push(0x21, 0xff, 11);
  for (const ch of "NETSCAPE2.0") out.push(ch.charCodeAt(0));
  out.push(3, 1, 0, 0, 0); // loop forever

  for (const frame of frames) {
    out.push(0x21, 0xf9, 4, 0);
    word(100); // hundredths of a second
    out.push(0, 0);
    out.push(0x2c);
    word(0);
    word(0);
    word(frame.width);
    word(frame.height);
    out.push(0);
    const pixels = new Uint8Array(frame.width * frame.height);
    for (let i = 0, p = 0; i < frame.rgba.length; i += 4, p++) {
      pixels[p] = Math.round(frame.rgba[i] / 51) * 42 + Math.round(frame.rgba[i + 1] / 42.5) * 6 + Math.round(frame.rgba[i + 2] / 51);
    }
    lzw(pixels, out);
  }
  out.push(0x3b);
  return Uint8Array.from(out);
}

function lzw(pixels, out) {
  const MIN = 8;
  const CLEAR = 1 << MIN;
  const END = CLEAR + 1;
  out.push(MIN);
  let block = [];
  let bits = 0;
  let nbits = 0;
  const flushBlock = () => {
    if (block.length) out.push(block.length, ...block);
    block = [];
  };
  const emit = (code, size) => {
    bits |= code << nbits;
    nbits += size;
    while (nbits >= 8) {
      block.push(bits & 255);
      bits >>>= 8;
      nbits -= 8;
      if (block.length === 255) flushBlock();
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
  if (nbits > 0) block.push(bits & 255);
  flushBlock();
  out.push(0);
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
