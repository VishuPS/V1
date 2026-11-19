export const formats = {8: 'EAN-8', 12: 'UPC-A', 13: 'EAN-13', 14: 'GTIN-14'};

export function checkDigit(body) {
  if (!/^[0-9]+$/.test(body)) throw new Error('Use ASCII digits 0–9 only.');
  const sum = [...body].reverse().reduce((total, digit, i) => total + Number(digit) * (i % 2 ? 1 : 3), 0);
  return String((10 - sum % 10) % 10);
}

/**
 * @typedef {{valid: true, value: string, format: string, expected: string, gtin14: string, equivalents: string[]} |
 * {valid: false, error: string, value?: string, format?: string, expected?: string}} ValidationResult
 */

/** @returns {ValidationResult} */
export function validateBarcode(raw) {
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (!value) return {valid:false, error:'Enter a barcode to check.'};
  if (!/^[0-9]+$/.test(value)) return {valid:false, error:'Use digits 0–9 only. Remove spaces, hyphens and other characters inside the barcode.'};
  const format = formats[value.length];
  if (!format) return {valid:false, error:`This barcode has ${value.length} digits. Use 8 (EAN-8), 12 (UPC-A), 13 (EAN-13), or 14 (GTIN-14) digits.`};
  const expected = checkDigit(value.slice(0,-1));
  if (value.at(-1) !== expected) return {valid:false, value, format, expected, error:`The check digit does not match. The final digit is ${value.at(-1)}; the expected check digit for the preceding digits is ${expected}. Check the entire number against the packaging before changing it.`};
  const gtin14 = value.padStart(14,'0');
  const equivalents = new Set([value, gtin14]);
  if (value.length === 12) equivalents.add(`0${value}`);
  if (value.length === 13 && value.startsWith('0')) equivalents.add(value.slice(1));
  if (value.length === 14 && value.startsWith('0')) equivalents.add(value.slice(1));
  if (value.length === 14 && value.startsWith('00')) equivalents.add(value.slice(2));
  return {valid:true, value, format, expected, gtin14, equivalents:[...equivalents]};
}
