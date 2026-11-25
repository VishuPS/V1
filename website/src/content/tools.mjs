export const toolGroups=["Discover & Create","Organise & Manage","Design & Process","Developer Tools"];
// Only working tools belong here. Later phases add entries after acceptance tests.
export const tools=[
 {id:"lookup",name:"Barcode Lookup",group:toolGroups[0],href:"/barcode-lookup/",description:"Find available product details from a UPC, EAN or GTIN.",tier:"Free lookup credits"},
 {id:"generator",name:"Barcode Generator",group:toolGroups[0],href:"/barcode-generator/",description:"Create SVG and PNG barcodes; paid plans add ZIP batches.",tier:"Free / paid batches"},
 {id:"scanner",name:"Barcode Scanner",group:toolGroups[0],href:"/barcode-scanner/",description:"Read a photo or captured frame and save its identifier.",tier:"Free allowance"},
 {id:"validator",name:"Barcode Validator",group:toolGroups[0],href:"/barcode-validator/",description:"Check retail identifier lengths and check digits.",tier:"Free"},
 {id:"converter",name:"Barcode Converter",group:toolGroups[0],href:"/barcode-converter/",description:"Find equivalent UPC, EAN and GTIN representations.",tier:"Free"},
 {id:"vault",name:"Barcode Vault",group:toolGroups[1],href:"/vault/",description:"Save and organise your private product records and collections.",tier:"25 records free"},
 {id:"labels",name:"Barcode Labels",group:toolGroups[2],href:"/barcode-labels/",description:"Print a PDF sheet from a CSV or saved Vault products.",tier:"Free allowance"},
 {id:"enrichment",name:"Product Data Enrichment",group:toolGroups[2],href:"/csv-product-enrichment/",description:"Preview a barcode CSV and enrich available product details.",tier:"Plan allowance"},
 {id:"exports",name:"Data Export",group:toolGroups[2],href:"/vault/",description:"Export a private product collection as CSV or JSON.",tier:"Creator and above"},
 {id:"api",name:"Barcode API",group:toolGroups[3],href:"/barcode-api/",description:"Integrate structured barcode and product data into your software.",tier:"Free / paid quotas"},
 {id:"docs",name:"API Documentation",group:toolGroups[3],href:"/docs/",description:"Requests, responses, quotas and integration examples.",tier:"Public"},
 {id:"keys",name:"API Key Management",group:toolGroups[3],href:"/api-keys/",description:"Create and manage your integration credentials.",tier:"Account required"},
];
