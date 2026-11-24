import { authFetch, getAuthState, responseMessage } from "./auth-context";
import { saveToVault } from "./vault-client";

const form = document.querySelector<HTMLFormElement>("#scanner-form")!;
const fieldset = form.querySelector<HTMLFieldSetElement>("fieldset")!;
const file = document.querySelector<HTMLInputElement>("#scan-file")!;
const status = document.querySelector<HTMLElement>("#scan-status")!;
const balance = document.querySelector<HTMLElement>("#scan-balance")!;
const results = document.querySelector<HTMLElement>("#scan-results")!;
const empty = document.querySelector<HTMLElement>("#scan-empty")!;
const video = document.querySelector<HTMLVideoElement>("#scan-video")!;
const camera = document.querySelector<HTMLElement>("#camera-panel")!;
let stream: MediaStream | null = null;
let busy = false;
let signedIn = false;
let cameraVersion = 0;

function stopCamera() {
  cameraVersion++;
  stream?.getTracks().forEach(track => track.stop());
  stream = null; video.srcObject = null; camera.hidden = true;
}
window.addEventListener("pagehide", stopCamera);
document.addEventListener("visibilitychange", () => { if (document.hidden) stopCamera(); });
document.querySelector("#stop-camera")!.addEventListener("click", stopCamera);

async function refreshBalance() {
  const response = await authFetch("/account/services/usage");
  if (!response.ok) throw new Error(await responseMessage(response));
  const data = await response.json();
  const scans = data.categories.scans;
  balance.textContent = `${scans.remaining.toLocaleString()} of ${scans.limit.toLocaleString()} scans remaining this month.`;
}
getAuthState().then(async auth => {
  signedIn = auth.status === "authenticated";
  if (signedIn) { try { await refreshBalance(); } catch { balance.textContent = "Scan balance is temporarily unavailable."; } }
});

document.querySelector("#start-camera")!.addEventListener("click", async () => {
  if (!signedIn || busy) return;
  if (!navigator.mediaDevices?.getUserMedia) { status.textContent = "Camera capture is unavailable in this browser. Upload a photo instead."; return; }
  stopCamera();
  const version = cameraVersion;
  try {
    status.textContent = "Waiting for camera permission…";
    const opened = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"},width:{ideal:1920},height:{ideal:1080}},audio:false});
    if (version !== cameraVersion || document.hidden) { opened.getTracks().forEach(track => track.stop()); return; }
    stream = opened; video.srcObject = stream; camera.hidden = false;
    await video.play();
    status.textContent = "Position the barcode in view, then choose Capture & read.";
  } catch { stopCamera(); status.textContent = "Camera access was unavailable or denied. You can upload a photo instead."; }
});

function imageData(source: CanvasImageSource, width: number, height: number): string {
  if (!width || !height) throw new Error("The camera or image is not ready. Try again.");
  const scale = Math.min(1, 2400 / Math.max(width,height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width*scale); canvas.height = Math.round(height*scale);
  const context = canvas.getContext("2d")!;
  context.fillStyle = "white"; context.fillRect(0,0,canvas.width,canvas.height);
  context.drawImage(source,0,0,canvas.width,canvas.height);
  return canvas.toDataURL("image/jpeg",.95).split(",")[1];
}

function showResults(items: Array<{value:string;format:string;gtin14:string|null}>) {
  results.replaceChildren(); empty.hidden = items.length > 0;
  empty.textContent = "No readable barcode found. Try a sharper photo with the full barcode and white margins visible.";
  for (const item of items) {
    const article = document.createElement("article");
    const heading = document.createElement("h3"); heading.textContent = item.format;
    const value = document.createElement("pre"); value.textContent = item.value;
    const actions = document.createElement("div"); actions.className = "tool-actions";
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "button button-secondary"; copy.textContent = "Copy value";
    copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(item.value); copy.textContent = "Copied"; } catch { status.textContent = "Select and copy the decoded text above."; } });
    actions.append(copy);
    const types: Record<string,string> = {"EAN-13":"ean13","UPC-A":"upca","Code 128":"code128","QRCode":"qr","QR Code":"qr"};
    const type = types[item.format];
    if (type && item.value.length <= 500) {
      const create = document.createElement("a"); create.className = "button button-secondary"; create.textContent = "Create barcode";
      create.href = "/barcode-generator/?"+new URLSearchParams({value:item.value,type}); actions.append(create);
    }
    const product = document.createElement("p"); product.className = "scan-product"; product.hidden = true;
    let productDetails: {name?:string;brand?:string;image_url?:string} = {};
    if(item.value.length<=500){
      const save=document.createElement("button");save.type="button";save.className="button button-secondary";save.textContent="Save to Vault";
      save.addEventListener("click",async()=>{save.disabled=true;try{
        const kind:Record<string,string>={"EAN-13":"ean13","UPC-A":"upca","EAN-8":"ean8","Code 128":"code128","QR Code":"qr","Data Matrix":"datamatrix"};
        const result=await saveToVault({barcode:item.value,barcode_type:kind[item.format]||"auto",name:(productDetails.name||`Barcode ${item.value}`).slice(0,200),brand:productDetails.brand||"",image_url:productDetails.image_url?.startsWith("https://")?productDetails.image_url:""});
        save.textContent="Saved to Vault";status.textContent=result.created?"Saved. Open My Vault to organize this product.":"Already saved. Your existing product details were kept.";
      }catch(error){status.textContent=error instanceof Error?error.message:"Save unavailable.";save.disabled=false;}});actions.append(save);
    }
    if (item.gtin14) {
      const lookup = document.createElement("button"); lookup.type = "button"; lookup.className = "button button-signal"; lookup.textContent = "Look up product · lookup allowance";
      lookup.addEventListener("click", async () => {
        lookup.disabled = true; product.hidden = false; product.textContent = "Looking up product…";
        try {
          const response = await authFetch("/account/products/"+encodeURIComponent(item.value));
          if (!response.ok) throw new Error(await responseMessage(response));
          const data = await response.json();
          productDetails=data.product||{};
          product.textContent = [data.product?.name, data.product?.brand, data.product?.quantity].filter(Boolean).join(" · ") || "Product found; no display details available.";
        } catch (error) { product.textContent = error instanceof Error ? error.message : "Product lookup unavailable."; }
        finally { lookup.disabled = false; }
      }); actions.append(lookup);
    }
    article.append(heading,value,actions,product); results.append(article);
  }
}

async function scan(makeImage: () => Promise<string> | string) {
  if (busy || !signedIn) return;
  busy = true; fieldset.disabled = true; status.textContent = "Preparing image…";
  results.replaceChildren(); empty.hidden = false; empty.textContent = "Reading your image…";
  try {
    const image = await makeImage();
    status.textContent = "Reading barcodes…";
    const response = await authFetch("/account/barcodes/scan", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({image_base64:image})});
    if (!response.ok) throw new Error(await responseMessage(response));
    const data = await response.json(); showResults(data.barcodes);
    status.textContent = data.count ? `${data.count} barcode${data.count===1?"":"s"} found. One scan used.` : "No barcode found. One scan used.";
  } catch (error) { status.textContent = error instanceof Error ? error.message : "Scanning unavailable. Try again."; empty.textContent = "Choose an image or capture another photo to try again."; }
  finally { busy = false; fieldset.disabled = !signedIn; try { await refreshBalance(); } catch {} }
}
form.addEventListener("submit", event => {
  event.preventDefault();
  scan(async () => {
    const selected = file.files?.[0];
    if (!selected) throw new Error("Choose a barcode photo first.");
    if (!["image/png","image/jpeg","image/webp"].includes(selected.type)) throw new Error("Choose a PNG, JPEG or WebP image.");
    if (selected.size > 12*1024*1024) throw new Error("Choose an image smaller than 12 MiB.");
    const bitmap = await createImageBitmap(selected);
    try { return imageData(bitmap, bitmap.width, bitmap.height); } finally { bitmap.close(); }
  });
});
document.querySelector("#capture-camera")!.addEventListener("click", () => scan(() => imageData(video,video.videoWidth,video.videoHeight)));
