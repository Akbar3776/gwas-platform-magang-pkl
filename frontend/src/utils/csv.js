const FORMULA_PREFIX_RE = /^[=+\-@\t\r]/;
const NUMERIC_STRING_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

export function sanitizeCsvCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : "";
  }
  const str = String(value);
  if (FORMULA_PREFIX_RE.test(str)) {
    // Biarkan string angka murni (mis. '-0.42', '-1.5e-8') tanpa prefiks ' agar tetap terbaca numerik.
    const startsWithControl = /^[\t\r]/.test(str);
    if (!startsWithControl && NUMERIC_STRING_RE.test(str.trim())) {
      return str;
    }
    return `'${str}`;
  }
  return str;
}

function quoteCsvCell(value) {
  const safe = sanitizeCsvCell(value);
  return `"${safe.replace(/"/g, '""')}"`;
}

export function toCsv(headers, rows) {
  const headerLine = (headers || []).map(quoteCsvCell).join(",");
  const rowLines = (rows || []).map((row) =>
    (row || []).map(quoteCsvCell).join(",")
  );
  return [headerLine, ...rowLines].join("\n");
}
