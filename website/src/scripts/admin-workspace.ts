import {formatDate} from "./admin-api";
export const toolNames:Record<string,string>={audit_rows:"Catalogue audit rows",sku_generation:"Generated SKUs",bulk_rows:"Bulk processing rows",generation:"Generated images",scans:"Scan attempts",labels:"Printed labels",enrichment:"Enrichment rows",validation:"Validation checks",vault_exports:"Vault exports prepared"};
export type WorkspaceUsage={asset_records?:number;asset_events?:number;inventory_records?:number;stock_movements?:number;sku_allocations?:number;sku_capacity?:number;label_templates?:number;label_template_capacity?:number;period_start:string;tools:Record<string,{used:number;limit:number|null;remaining:number|null;last_used_at:string|null}>;vault_products:number;vault_capacity:number;vault_created_this_month:number;vault_last_updated_at:string|null};
const n=(value:number)=>new Intl.NumberFormat().format(value);
export function usageCell(data:WorkspaceUsage|null|undefined){
 const cell=document.createElement("td");if(!data){cell.textContent="Unavailable";return cell;}
 for(const [key,label] of Object.entries(toolNames)){const line=document.createElement("span");line.style.display="block";line.textContent=`${label}: ${n(data.tools[key]?.used||0)}`;cell.append(line);}
 const vault=document.createElement("strong");vault.style.display="block";vault.textContent=`Vault: ${n(data.vault_products)} / ${n(data.vault_capacity)}`;cell.append(vault);return cell;
}
export function renderWorkspace(root:HTMLElement,data:WorkspaceUsage|null|undefined){
 root.replaceChildren();if(!data){root.textContent="Workspace statistics unavailable.";return;}
 const period=document.createElement("p");period.textContent=`Tool usage since ${data.period_start} · UTC calendar month. Website and API-key activity share the same counters.`;
 const wrapper=document.createElement("div");wrapper.className="admin-table-wrap";wrapper.tabIndex=0;wrapper.setAttribute("role","region");wrapper.setAttribute("aria-label","User tool usage");
 const table=document.createElement("table");table.className="admin-table";const head=document.createElement("thead"),heading=document.createElement("tr");
 for(const label of ["Tool","Used","Plan limit","Remaining","Latest use this month"]){const th=document.createElement("th");th.textContent=label;heading.append(th);}head.append(heading);table.append(head);
 const body=document.createElement("tbody");for(const [key,label]of Object.entries(toolNames)){const item=data.tools[key]||{used:0,limit:null,remaining:null,last_used_at:null};const row=document.createElement("tr");for(const text of [label,n(item.used),item.limit===null?"Not quota-metered":n(item.limit),item.remaining===null?"—":n(item.remaining),formatDate(item.last_used_at,true)]){const td=document.createElement("td");td.textContent=text;row.append(td);}body.append(row);}table.append(body);wrapper.append(table);
 const vault=document.createElement("p");vault.textContent=`Vault storage: ${n(data.vault_products)} / ${n(data.vault_capacity)} records. ${n(data.vault_created_this_month)} currently saved records were created this month. Last saved-product update: ${formatDate(data.vault_last_updated_at,true)}. Storage does not reset monthly.`;
 const note=document.createElement("p");note.className="portal-note";note.textContent="Scans count accepted images, including no-match results; labels include copies; enrichment counts input rows. Validation and export tracking starts with this release. These are usage statistics, not private product contents or a complete event log.";
 const premium=document.createElement("p");premium.textContent=`Assets: ${n(data.asset_records||0)} · Asset history entries: ${n(data.asset_events||0)} · Inventory records: ${n(data.inventory_records||0)} · Stock movements: ${n(data.stock_movements||0)} · SKU register: ${n(data.sku_allocations||0)} / ${n(data.sku_capacity||0)} · Label templates: ${n(data.label_templates||0)} / ${n(data.label_template_capacity||0)}`;
 root.append(period,wrapper,vault,premium,note);
}
