import crypto from "crypto";
import { TextEncoder } from "util";
import { computeFileSha256, createIncrementalSha256, writeLengthBits64BE } from "./sha256";

function nodeSha256(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

describe("streaming SHA-256 helper (A26)", () => {
  test("matches Node crypto across boundary lengths and arbitrary chunk splits", () => {
    const enc = new TextEncoder();
    const samples = [
      new Uint8Array(0),
      enc.encode("abc"),
      new Uint8Array(55).fill(0x61),
      new Uint8Array(56).fill(0x62),
      new Uint8Array(63).fill(0x63),
      new Uint8Array(64).fill(0x64),
      new Uint8Array(65).fill(0x65),
      Uint8Array.from({ length: 150000 }, (_, i) => (i * 31) & 0xff),
    ];

    for (const sample of samples) {
      const hasher = createIncrementalSha256();
      for (let off = 0; off < sample.length; off += 4093) {
        hasher.update(sample.subarray(off, Math.min(off + 4093, sample.length)));
      }
      expect(hasher.digest()).toBe(nodeSha256(sample));
    }
  });

  test("computeFileSha256 reads only slices and never calls whole-file arrayBuffer()", async () => {
    const payload = new TextEncoder().encode("header\n" + "ACGT".repeat(4096));
    const file = new File([payload], "large.vcf", { type: "text/plain" });
    file.arrayBuffer = jest.fn(() => Promise.reject(new Error("whole-file arrayBuffer must not be called")));
    const sliceSpy = jest.spyOn(file, "slice");

    const digest = await computeFileSha256(file, { chunkSize: 1024 });
    expect(digest).toBe(nodeSha256(payload));
    expect(file.arrayBuffer).not.toHaveBeenCalled();
    expect(sliceSpy.mock.calls.length).toBe(Math.ceil(payload.length / 1024));
  });

  test("encodes 64-bit bit length accurately above 2 GB (> 2^32 bits)", () => {
    const buf = new Uint8Array(8);
    const twoGibPlusSeven = 2 * 1024 * 1024 * 1024 + 7;
    writeLengthBits64BE(twoGibPlusSeven, buf, 0);
    const view = new DataView(buf.buffer);
    const expectedBits = BigInt(twoGibPlusSeven) * 8n;
    expect(view.getBigUint64(0, false)).toBe(expectedBits);
  });
});
