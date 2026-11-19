const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const SHA256_INIT = new Uint32Array([
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
]);

function rotr(x, n) {
  return (x >>> n) | (x << (32 - n));
}

function compressBlock(state, block, offset, w) {
  for (let i = 0; i < 16; i += 1) {
    const j = offset + i * 4;
    w[i] = ((block[j] << 24) | (block[j + 1] << 16) | (block[j + 2] << 8) | block[j + 3]) >>> 0;
  }
  for (let i = 16; i < 64; i += 1) {
    const w15 = w[i - 15];
    const w2 = w[i - 2];
    const s0 = (rotr(w15, 7) ^ rotr(w15, 18) ^ (w15 >>> 3)) >>> 0;
    const s1 = (rotr(w2, 17) ^ rotr(w2, 19) ^ (w2 >>> 10)) >>> 0;
    w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
  }

  let a = state[0];
  let b = state[1];
  let c = state[2];
  let d = state[3];
  let e = state[4];
  let f = state[5];
  let g = state[6];
  let h = state[7];

  for (let i = 0; i < 64; i += 1) {
    const S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
    const ch = ((e & f) ^ (~e & g)) >>> 0;
    const temp1 = (h + S1 + ch + SHA256_K[i] + w[i]) >>> 0;
    const S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
    const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
    const temp2 = (S0 + maj) >>> 0;

    h = g;
    g = f;
    f = e;
    e = (d + temp1) >>> 0;
    d = c;
    c = b;
    b = a;
    a = (temp1 + temp2) >>> 0;
  }

  state[0] = (state[0] + a) >>> 0;
  state[1] = (state[1] + b) >>> 0;
  state[2] = (state[2] + c) >>> 0;
  state[3] = (state[3] + d) >>> 0;
  state[4] = (state[4] + e) >>> 0;
  state[5] = (state[5] + f) >>> 0;
  state[6] = (state[6] + g) >>> 0;
  state[7] = (state[7] + h) >>> 0;
}

export function writeLengthBits64BE(totalBytes, target, offset = 0) {
  // Split totalBytes before multiplying by 8 so >512 MiB (2+ GB) files never overflow 32-bit shifts.
  const hi = Math.floor(totalBytes / 0x20000000) >>> 0;
  const lo = ((totalBytes & 0x1fffffff) * 8) >>> 0;
  target[offset] = (hi >>> 24) & 0xff;
  target[offset + 1] = (hi >>> 16) & 0xff;
  target[offset + 2] = (hi >>> 8) & 0xff;
  target[offset + 3] = hi & 0xff;
  target[offset + 4] = (lo >>> 24) & 0xff;
  target[offset + 5] = (lo >>> 16) & 0xff;
  target[offset + 6] = (lo >>> 8) & 0xff;
  target[offset + 7] = lo & 0xff;
}

export function createIncrementalSha256() {
  const state = new Uint32Array(SHA256_INIT);
  const buffer = new Uint8Array(64);
  const work = new Uint32Array(64);
  let bufLen = 0;
  let totalBytes = 0;

  return {
    update(chunk) {
      const data = chunk instanceof Uint8Array ? chunk : new Uint8Array(chunk);
      let offset = 0;
      totalBytes += data.length;

      if (bufLen > 0) {
        const take = Math.min(64 - bufLen, data.length);
        buffer.set(data.subarray(0, take), bufLen);
        bufLen += take;
        offset += take;
        if (bufLen === 64) {
          compressBlock(state, buffer, 0, work);
          bufLen = 0;
        }
      }

      while (offset + 64 <= data.length) {
        compressBlock(state, data, offset, work);
        offset += 64;
      }

      if (offset < data.length) {
        buffer.set(data.subarray(offset), 0);
        bufLen = data.length - offset;
      }
    },

    digest() {
      const finalState = new Uint32Array(state);
      const padLen = bufLen < 56 ? 64 : 128;
      const tail = new Uint8Array(padLen);
      tail.set(buffer.subarray(0, bufLen), 0);
      tail[bufLen] = 0x80;
      writeLengthBits64BE(totalBytes, tail, padLen - 8);

      compressBlock(finalState, tail, 0, work);
      if (padLen === 128) {
        compressBlock(finalState, tail, 64, work);
      }

      let hex = "";
      for (let i = 0; i < 8; i += 1) {
        hex += finalState[i].toString(16).padStart(8, "0");
      }
      return hex;
    },
  };
}

export async function readBlobAsUint8Array(blob) {
  if (typeof blob.arrayBuffer === "function") {
    return new Uint8Array(await blob.arrayBuffer());
  }
  if (typeof FileReader !== "undefined") {
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(new Uint8Array(reader.result));
      reader.onerror = () => reject(reader.error || new Error("Gagal membaca potongan berkas."));
      reader.readAsArrayBuffer(blob);
    });
  }
  return new Uint8Array(await new Response(blob).arrayBuffer());
}

export async function computeFileSha256(file, { chunkSize = 10 * 1024 * 1024, signal } = {}) {
  const hasher = createIncrementalSha256();
  for (let offset = 0; offset < file.size; offset += chunkSize) {
    if (signal?.aborted) {
      throw new Error("Perhitungan checksum dibatalkan.");
    }
    const end = Math.min(offset + chunkSize, file.size);
    const bytes = await readBlobAsUint8Array(file.slice(offset, end));
    hasher.update(bytes);
  }
  return hasher.digest();
}
