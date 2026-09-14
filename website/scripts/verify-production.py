"""Read-only deployment verification; never uses or prints credentials."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor

PATHS = ["/", "/pricing/", "/docs/barcode-lookup/", "/docs/rate-limits/", "/register/", "/blog/", "/robots.txt", "/sitemap.xml", "/blog/rss.xml",
         "/blog/best-barcode-lookup-apis-2026/", "/blog/upc-lookup-api/", "/blog/ean-lookup-api/", "/blog/free-barcode-lookup-api/", "/blog/gtin-lookup-api/", "/blog/build-barcode-scanner-app-api/", "/blog/barcode-api-pricing/", "/blog/upc-vs-ean-vs-gtin/", "/blog/get-product-data-from-barcode/", "/blog/barcode-database-for-developers/"]

def inspect(path):
    req = Request("https://barcodenest.com" + path, headers={"User-Agent": "BarcodeNest-deployment-verification/1.0"})
    try:
        with urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            return {"path": path, "status": response.status, "day1": "best-barcode-lookup-apis-2026" in text,
                    "day2": "upc-lookup-api" in text, "cache": response.headers.get("Cache-Control")}
    except HTTPError as error:
        return {"path": path, "status": error.code}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        print(json.dumps(list(pool.map(inspect, PATHS)), indent=2))
