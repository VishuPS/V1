import { authFetch, responseMessage } from "./auth-context";
export type VaultDraft = {barcode:string;name:string;barcode_type?:string;brand?:string;image_url?:string;notes?:string};
export async function saveToVault(draft:VaultDraft) {
  const response=await authFetch("/account/vault",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(draft)});
  if(!response.ok)throw new Error(await responseMessage(response));
  return response.json();
}
