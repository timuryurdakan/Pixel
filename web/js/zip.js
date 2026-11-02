// F4-4: a stored (uncompressed) ZIP writer for downloading the browser's virtual folder. No dependency: a few dozen
// lines of the PKWARE APPNOTE format (local headers, central directory, end record), UTF-8 names (flag bit 11).
// tests/test_f44_zip.py reads the committed fixture with Python's zipfile; tests/js/zip.test.js rebuilds it byte for
// byte from the same input.

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const MAX_ENTRIES = 65535;                    // without the ZIP64 extension
const MAX_BYTES = 0xffffffff;
const MIN_YEAR = 1980;                         // DOS dates start here

/** A JS time (ms) → DOS [time, date] in the local time zone, like unzip shows it. */
export function dosDateTime(ms) {
  const d = new Date(Number.isFinite(ms) ? ms : 0);
  if (d.getFullYear() < MIN_YEAR) return [0, (0 << 9) | (1 << 5) | 1];
  const time = (d.getHours() << 11) | (d.getMinutes() << 5) | Math.floor(d.getSeconds() / 2);
  const date = ((d.getFullYear() - MIN_YEAR) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  return [time, date];
}

/**
 * @param {{path: string, data: Uint8Array, mtime?: number}[]} entries  relative paths with "/" (checked by the caller)
 * @returns {Uint8Array} the .zip file
 */
export function buildZip(entries) {
  if (!Array.isArray(entries) || entries.length > MAX_ENTRIES) throw new Error('too many entries');
  const enc = new TextEncoder();
  const parts = [];
  const central = [];
  let offset = 0;
  for (const e of entries) {
    const name = enc.encode(e.path);
    const data = e.data;
    if (!(data instanceof Uint8Array) || !name.length || name.length > 0xffff) throw new Error('bad entry');
    const [time, date] = dosDateTime(e.mtime);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);       // local file header
    local.setUint16(4, 20, true);               // version needed: 2.0
    local.setUint16(6, 0x0800, true);           // UTF-8 names
    local.setUint16(8, 0, true);                // stored
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, name.length, true);
    local.setUint16(28, 0, true);
    parts.push(new Uint8Array(local.buffer), name, data);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true);          // central directory header
    cd.setUint16(4, 20, true);                  // made by: 2.0, MS-DOS attributes
    cd.setUint16(6, 20, true);
    cd.setUint16(8, 0x0800, true);
    cd.setUint16(10, 0, true);
    cd.setUint16(12, time, true);
    cd.setUint16(14, date, true);
    cd.setUint32(16, crc, true);
    cd.setUint32(20, data.length, true);
    cd.setUint32(24, data.length, true);
    cd.setUint16(28, name.length, true);
    cd.setUint32(42, offset, true);             // extra, comment, disk, attributes stay 0
    central.push(new Uint8Array(cd.buffer), name);
    offset += 30 + name.length + data.length;
    if (offset > MAX_BYTES) throw new Error('too large');
  }
  const cdSize = central.reduce((n, p) => n + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);           // end of central directory
  end.setUint16(8, entries.length, true);
  end.setUint16(10, entries.length, true);
  end.setUint32(12, cdSize, true);
  end.setUint32(16, offset, true);
  const all = [...parts, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of all) { out.set(p, at); at += p.length; }
  return out;
}
