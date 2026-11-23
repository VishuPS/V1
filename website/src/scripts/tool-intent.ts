const toolPaths = new Set([
  "/barcode-scanner/",
  "/barcode-generator/", "/barcode-labels/", "/csv-product-enrichment/", "/barcode-validator/",
]);

export function pendingToolDestination(): string | null {
  const requested = new URLSearchParams(location.search).get("next");
  try {
    if (requested && toolPaths.has(requested)) {
      sessionStorage.setItem("barcodenest_tool_intent", requested);
      return requested;
    }
    const stored = sessionStorage.getItem("barcodenest_tool_intent");
    return stored && toolPaths.has(stored) ? stored : null;
  } catch {
    return requested && toolPaths.has(requested) ? requested : null;
  }
}
