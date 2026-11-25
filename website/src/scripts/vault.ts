import {authFetch,requireUser,responseMessage} from "./auth-context";
import {uploadTable,downloadBlob,moneyMinor,handoffProducts} from "./workspace-client";
type Product={id:string;revision:number;barcode:string;barcode_type:string;name:string;sku:string;brand:string;category:string;collection:string;supplier:string;tags:string[];variant_of:string|null;notes:string;image_url:string;price_minor:number|null;currency:string;custom_fields:Record<string,string>};
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const form=$<HTMLFormElement>("vault-form");
const status=$("vault-status");
let editing:Product|null=null,offset=0,total=0,items:Product[]=[],busy=false;
let ownerId="";
const selected=new Set<string>();
const field=(name:string)=>form.elements.namedItem(name) as HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement;
function message(text:string){status.textContent=text;}
async function request(path:string,init?:RequestInit){const response=await authFetch(path,init);if(!response.ok)throw new Error(await responseMessage(response));return response;}
function download(blob:Blob,name:string){const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
function edit(item:Product|null){
 editing=item;form.reset();$("vault-editor").hidden=false;$("editor-heading").textContent=item?"Edit private product":"Add a private product";
 const parent=field("variant_of") as HTMLSelectElement;parent.replaceChildren(new Option("No parent product",""));for(const product of items.filter(p=>p.id!==item?.id))parent.add(new Option(product.name,product.id));if(item?.variant_of&&!items.some(p=>p.id===item.variant_of))parent.add(new Option("Current parent product",item.variant_of));
 if(item){for(const [key,value]of Object.entries(item)){if(form.elements.namedItem(key))field(key).value=String(value??"");}field("tags").value=(item.tags||[]).join(", ");field("price").value=item.price_minor===null?"":(item.price_minor/100).toFixed(2);field("custom_fields").value=Object.entries(item.custom_fields).map(([k,v])=>`${k}=${v}`).join("\n");}
 $("product-identifiers").hidden=!item;if(item)loadIdentifiers().catch(()=>{$("identifier-list").textContent="Identifiers are temporarily unavailable.";});
 $("editor-heading").focus();$("vault-editor").scrollIntoView({behavior:"auto",block:"start"});
}
function button(text:string,action:()=>void){const b=document.createElement("button");b.type="button";b.className="button button-secondary button-small";b.textContent=text;b.addEventListener("click",action);return b;}
function showItems(){
 const root=$("vault-products");root.replaceChildren();selected.clear();$("vault-selected").textContent="Select products to create a PDF.";
 if(!items.length){const p=document.createElement("p");p.className="empty-state";p.textContent=total?"No products on this page.":"No products found. Add your first product, or save a result from the scanner or generator.";root.append(p);}
 for(const item of items){
  const article=document.createElement("article"),check=document.createElement("input");check.type="checkbox";check.setAttribute("aria-label",`Select ${item.name}`);
  check.addEventListener("change",()=>{if(check.checked)selected.add(item.id);else selected.delete(item.id);$("vault-selected").textContent=`${selected.size} selected · one label per product. Label allowance applies.`;});
  const body=document.createElement("div"),name=document.createElement("h3"),code=document.createElement("p"),detail=document.createElement("p");name.textContent=item.name;code.className="vault-code";code.textContent=`${item.barcode} · ${item.barcode_type}`;detail.textContent=[item.sku&&`SKU: ${item.sku}`,item.brand,item.category,item.collection&&`Collection: ${item.collection}`,item.price_minor!==null?`${item.currency} ${(item.price_minor/100).toFixed(2)}`:null].filter(Boolean).join(" · ");body.append(name,code,detail);
  const actions=document.createElement("div");actions.className="vault-row-actions";actions.append(button("Edit",()=>edit(item)));
  if(["ean13","ean8","upca","code128","code39","qr"].includes(item.barcode_type)){const a=document.createElement("a");a.className="button button-secondary button-small";a.textContent="Generate image";a.href="/barcode-generator/?"+new URLSearchParams({type:item.barcode_type,value:item.barcode});actions.append(a);}
  if(!item.sku)actions.append(button("Assign SKU",()=>handoffProducts([item.id],ownerId,"/sku-manager/")));
  actions.append(button("Delete",async()=>{if(!confirm(`Delete “${item.name}” from your Vault?`))return;try{await request("/account/vault/"+item.id,{method:"DELETE"});if(editing?.id===item.id)$("vault-editor").hidden=true;await load();message("Product removed from your private Vault.");}catch(e){message(String(e instanceof Error?e.message:e));}}));
  article.append(check,body,actions);root.append(article);
 }
 $("vault-page").textContent=total?`${offset+1}–${Math.min(offset+50,total)} of ${total}`:"0 products";
 $<HTMLButtonElement>("vault-prev").disabled=offset===0;$<HTMLButtonElement>("vault-next").disabled=offset+50>=total;
}
async function load(){
 const params=new URLSearchParams({q:$<HTMLInputElement>("vault-query").value,collection:$<HTMLSelectElement>("vault-collection").value,offset:String(offset)});
 for(const [id,key]of [["filter-category","category"],["filter-supplier","supplier"],["filter-price","with_price"],["filter-sort","sort"]]){const input=document.getElementById(id) as HTMLInputElement|null;if(input?.value)params.set(key,input.value);}
 const data=await(await request("/account/vault?"+params)).json();items=data.items;total=data.total;
 if(offset>=total&&offset>0){offset=Math.max(0,Math.floor((total-1)/50)*50);return load();}
 $("vault-capacity").textContent=`${data.used.toLocaleString()} / ${data.capacity.toLocaleString()} saved products · ${data.plan}`;
 const current=$<HTMLSelectElement>("vault-collection").value;const filter=$<HTMLSelectElement>("vault-collection"),list=$("vault-collections-list");filter.replaceChildren(new Option("All collections",""));list.replaceChildren();
 for(const value of data.collections){filter.add(new Option(value,value));const option=document.createElement("option");option.value=value;list.append(option);}filter.value=current;
 $("vault-export-note").textContent=data.export_included?"Export your current collection as CSV or JSON. When importing CSV into Excel, set barcode columns to Text.":"Unlock CSV and JSON exports with Creator, Starter or Growth. Your saved products remain available on Free.";
 showItems();
}
$("vault-new").addEventListener("click",()=>edit(null));$("vault-cancel").addEventListener("click",()=>{$("vault-editor").hidden=true;editing=null;});
$("vault-search").addEventListener("submit",e=>{e.preventDefault();offset=0;load().catch(e=>message(e.message));});
$("vault-prev").addEventListener("click",()=>{offset=Math.max(0,offset-50);load().catch(e=>message(e.message));});$("vault-next").addEventListener("click",()=>{offset+=50;load().catch(e=>message(e.message));});
form.addEventListener("submit",async e=>{
 e.preventDefault();if(busy)return;busy=true;$<HTMLFieldSetElement>("vault-fields").disabled=true;
 try{
  const custom_fields:Record<string,string>=Object.create(null);
  for(const line of field("custom_fields").value.split(/\r?\n/).filter(l=>l.trim())){const pos=line.indexOf("=");if(pos<1)throw new Error("Use Name=Value for each custom field.");const key=line.slice(0,pos).trim();if(key in custom_fields)throw new Error("Custom field names must be unique.");custom_fields[key]=line.slice(pos+1).trim();}
  const price=field("price").value.trim();if(price&&!/^\d+(\.\d{1,2})?$/.test(price))throw new Error("Use a price with up to two decimal places.");
  const payload:Record<string,unknown>={custom_fields,price_minor:price?Math.round(Number(price)*100):null};
  for(const key of ["barcode","barcode_type","name","sku","brand","category","collection","supplier","notes","image_url","currency"])payload[key]=field(key).value;
  payload.tags=field("tags").value.split(",").map(value=>value.trim()).filter(Boolean);payload.variant_of=field("variant_of").value||null;
  if(editing)payload.revision=editing.revision;
  const response=await request("/account/vault"+(editing?"/"+editing.id:""),{method:editing?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const result=await response.json();$("vault-editor").hidden=true;editing=null;await load();message(result.created===false?"This barcode was already in your Vault. Its existing details were kept.":"Product saved to your private Vault.");
 }catch(e){message(e instanceof Error?e.message:"Product could not be saved.");}finally{busy=false;$<HTMLFieldSetElement>("vault-fields").disabled=false;}
});
$("vault-lookup").addEventListener("click",async()=>{
 const b=$<HTMLButtonElement>("vault-lookup");b.disabled=true;message("Looking up product details. This uses your lookup allowance…");
 try{const data=await(await request("/account/products/"+encodeURIComponent(field("barcode").value))).json();field("name").value=data.product?.name||field("name").value;field("brand").value=data.product?.brand||"";if(data.product?.image_url?.startsWith("https://"))field("image_url").value=data.product.image_url;message("Details loaded. Review them and select Save product to keep your changes.");}catch(e){message(e instanceof Error?e.message:"Lookup unavailable. You can enter your own product details.");}finally{b.disabled=false;}
});
for(const format of ["csv","xlsx","json"])document.getElementById("vault-export-"+format)?.addEventListener("click",async()=>{
 const b=$<HTMLButtonElement>("vault-export-"+format);b.disabled=true;
 try{const params=new URLSearchParams({format,collection:$<HTMLSelectElement>("vault-collection").value});const path=document.getElementById("catalogue-import")?"/account/catalogue/export":"/account/vault/export";const response=await request(path+"?"+params);download(await response.blob(),"barcodenest-vault."+format);message("Your collection export is ready.");}catch(e){message(e instanceof Error?e.message:"Export unavailable.");}finally{b.disabled=false;}
});
$("vault-labels").addEventListener("click",async()=>{
 const b=$<HTMLButtonElement>("vault-labels");b.disabled=true;
 try{
  const chosen=items.filter(item=>selected.has(item.id));if(!chosen.length)throw new Error("Select at least one product to print.");
  if(chosen.some(item=>!["ean13","ean8","upca","code128","code39","qr"].includes(item.barcode_type)))throw new Error("Label printing supports EAN-13, EAN-8, UPC-A, Code 128, Code 39 and QR. Remove unsupported formats from your selection.");
  const response=await request("/account/labels/render",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({paper:"a4",items:chosen.map(item=>({value:item.barcode,symbology:item.barcode_type,title:item.name.slice(0,70),detail:(item.sku||item.brand).slice(0,70),copies:1}))})});
  download(await response.blob(),"barcodenest-vault-labels.pdf");message(`${chosen.length} labels created. Your label allowance was used; lookup credits were unchanged.`);
 }catch(e){message(e instanceof Error?e.message:"Labels unavailable.");}finally{b.disabled=false;}
});
async function loadIdentifiers(){if(!editing)return;const data=await(await request(`/account/catalogue/${editing.id}/identifiers`)).json();const list=$("identifier-list");list.replaceChildren();for(const item of data.items){const li=document.createElement("li");li.textContent=`${item.value} · ${item.kind} `;li.append(button("Remove",async()=>{if(!editing||!confirm("Remove this additional identifier?"))return;try{await request(`/account/catalogue/${editing.id}/identifiers/${item.id}?revision=${editing.revision}`,{method:"DELETE"});editing.revision+=1;await loadIdentifiers();}catch(error){message(error instanceof Error?error.message:"Identifier could not be removed.");}}));list.append(li);}}
$("identifier-add").addEventListener("click",async()=>{if(!editing)return;try{const response=await request(`/account/catalogue/${editing.id}/identifiers`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({value:$<HTMLInputElement>("identifier-value").value,kind:$<HTMLSelectElement>("identifier-kind").value,revision:editing.revision})});const data=await response.json();editing.revision=data.revision;$<HTMLInputElement>("identifier-value").value="";await loadIdentifiers();message("Identifier added. Other product edits still need Save product.");}catch(error){message(error instanceof Error?error.message:"Identifier could not be added.");}});
$("vault-assign-skus").addEventListener("click",()=>{if(!selected.size)return message("Select products before assigning SKUs.");handoffProducts([...selected],ownerId,"/sku-manager/");});
$("vault-label-studio").addEventListener("click",()=>{if(!selected.size)return message("Select products to design labels.");handoffProducts([...selected],ownerId,"/label-studio/");});
let importReport:unknown=null;
document.getElementById("catalogue-import")?.addEventListener("submit",async event=>{event.preventDefault();const button=(event.currentTarget as HTMLFormElement).querySelector<HTMLButtonElement>('button')!;button.disabled=true;try{const file=$<HTMLInputElement>("catalogue-file").files?.[0];if(!file)throw new Error("Choose a file first.");const upload=await uploadTable(file);const result=await(await request('/account/catalogue/import',{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(upload)})).json();importReport=result;$("catalogue-report").hidden=false;await load();message(`${result.accepted} products imported. Review the report for skipped or invalid rows. Existing records were kept.`);}catch(error){message(error instanceof Error?error.message:"Import failed.");}finally{button.disabled=false;}});
document.getElementById("catalogue-report")?.addEventListener("click",()=>downloadBlob(new Blob([JSON.stringify(importReport,null,2)],{type:'application/json'}),'catalogue-import-report.json'));
document.getElementById("catalogue-bulk-edit")?.addEventListener("submit",async event=>{event.preventDefault();try{const chosen=items.filter(item=>selected.has(item.id));if(!chosen.length)throw new Error("Select products first.");const key=$<HTMLSelectElement>("bulk-field").value,value=$<HTMLInputElement>("bulk-value").value,updates:Record<string,unknown>={};updates[key]=key==='tags'?value.split(',').map(v=>v.trim()).filter(Boolean):key==='price_minor'?moneyMinor(value):value;if(key==='price_minor')updates.currency=$<HTMLSelectElement>("bulk-currency").value;const response=await request('/account/catalogue/bulk-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_ids:chosen.map(item=>item.id),revisions:Object.fromEntries(chosen.map(item=>[item.id,item.revision])),updates})});const result=await response.json();await load();message(`${result.updated} products updated.`);}catch(error){message(error instanceof Error?error.message:'Bulk edit failed.');}});
requireUser().then(user=>{if(user){ownerId=user.id;return load();}}).catch(e=>message(e.message));
