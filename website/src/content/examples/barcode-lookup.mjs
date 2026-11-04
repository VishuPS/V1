// Run on a server with Node.js 20+; never expose the API key in a browser bundle.
export async function lookupProduct(barcode, apiKey, fetchImpl = fetch) {
  if (typeof barcode !== 'string' || !/^(?:[0-9]{8}|[0-9]{12,14})$/.test(barcode)) {
    throw new TypeError('Pass an 8, 12, 13 or 14 digit barcode string');
  }
  if (!apiKey) throw new Error('BARCODENEST_API_KEY is not configured');
  const response = await fetchImpl(
    `https://api.barcodenest.com/v1/products/${encodeURIComponent(barcode)}`,
    { headers: { 'X-API-Key': apiKey }, signal: AbortSignal.timeout(15000) }
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.error?.message || `Lookup failed: HTTP ${response.status}`);
    error.status = response.status;
    error.code = body.error?.code;
    throw error;
  }
  return response.json();
}
