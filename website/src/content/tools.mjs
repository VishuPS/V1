export const toolGroups=["Discover & Create","Organise & Manage","Design & Process","Developer Tools"];
// Only working tools belong here. Later phases add entries after acceptance tests.
export const tools=[
 {id:"assets",name:"Asset Tracker",group:toolGroups[1],href:"/assets/",description:"Assign equipment, print identifying labels and record check-in, movements and maintenance.",tier:"Starter and Growth"},
 {id:"inventory",name:"Inventory Manager",group:toolGroups[1],href:"/inventory/",description:"Track stock, low-stock thresholds, purchase valuation and auditable movements.",tier:"Starter and Growth"},
 {id:"bulk",name:"Bulk Barcode Studio",group:toolGroups[2],href:"/bulk-studio/",description:"Process CSV/XLSX files with progress, retries, reports and barcode ZIP downloads.",tier:"Creator and above"},
 {id:"auditor",name:"Catalogue Auditor",group:toolGroups[2],href:"/catalogue-auditor/",description:"Review transparent quality checks and correct flagged private product records.",tier:"Starter and Growth"},
 {id:"catalogue",name:"Product Catalogue",group:toolGroups[1],href:"/catalogue/",description:"Manage variants, additional identifiers, suppliers and bulk product edits.",tier:"Free / premium workflows"},
 {id:"skus",name:"SKU Manager",group:toolGroups[1],href:"/sku-manager/",description:"Allocate unique internal SKUs, associate products and import or export a register.",tier:"Creator and above"},
 {id:"studio",name:"Label Studio",group:toolGroups[2],href:"/label-studio/",description:"Design reusable A4 or thermal labels and export PDF, SVG or PNG.",tier:"Creator and above"},
 {id:"lookup",name:"Barcode Lookup",group:toolGroups[0],href:"/barcode-lookup/",description:"Find available product details from a UPC, EAN or GTIN.",tier:"Free lookup credits"},
 {id:"generator",name:"Barcode Generator",group:toolGroups[0],href:"/barcode-generator/",description:"Create SVG and PNG barcodes; paid plans add ZIP batches.",tier:"Free / paid batches"},
 {id:"scanner",name:"Barcode Scanner",group:toolGroups[0],href:"/barcode-scanner/",description:"Read a photo or captured frame and save its identifier.",tier:"Free allowance"},
 {id:"validator",name:"Barcode Validator",group:toolGroups[0],href:"/barcode-validator/",description:"Check retail identifier lengths and check digits.",tier:"Free"},
 {id:"converter",name:"Barcode Converter",group:toolGroups[0],href:"/barcode-converter/",description:"Find equivalent UPC, EAN and GTIN representations.",tier:"Free"},
 {id:"vault",name:"Barcode Vault",group:toolGroups[1],href:"/vault/",description:"Save and organise your private product records and collections.",tier:"25 records free"},
 {id:"labels",name:"Barcode Labels",group:toolGroups[2],href:"/barcode-labels/",description:"Print a PDF sheet from a CSV or saved Vault products.",tier:"Free allowance"},
 {id:"enrichment",name:"Product Data Enrichment",group:toolGroups[2],href:"/csv-product-enrichment/",description:"Preview a barcode CSV and enrich available product details.",tier:"Plan allowance"},
 {id:"exports",name:"Data Export",group:toolGroups[2],href:"/catalogue/",description:"Export a private product collection as CSV, Excel or JSON.",tier:"Creator and above"},
 {id:"api",name:"Barcode API",group:toolGroups[3],href:"/barcode-api/",description:"Integrate structured barcode and product data into your software.",tier:"Free / paid quotas"},
 {id:"docs",name:"API Documentation",group:toolGroups[3],href:"/docs/",description:"Requests, responses, quotas and integration examples.",tier:"Public"},
 {id:"keys",name:"API Key Management",group:toolGroups[3],href:"/api-keys/",description:"Create and manage your integration credentials.",tier:"Account required"},
];
