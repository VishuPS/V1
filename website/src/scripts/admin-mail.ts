import { adminFetch, requireAdmin, responseMessage } from "./admin-api";

type Folder = { id: string; name: string; flags: string[] };
type Message = { uid: string; subject: string; sender: string; to: string; cc: string; date: string; seen: boolean; message_id: string; reply_to: string; references: string; body?: string; attachments?: { index: number; name: string; size: number }[] };
const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const status = el("mail-status");
const editor = el<HTMLDialogElement>("mail-editor");
const form = el<HTMLFormElement>("mail-editor-form");
let folders: Folder[] = [], folder = "INBOX", page = 1, total = 0, validity = "", query = "";
let current: Message | null = null, messages: Message[] = [], listVersion = 0, readVersion = 0, sending = false;
let requestId = crypto.randomUUID(), replyId = "", references = "";
let draftSnapshot = "";
function notify(message = "", error = false) { status.textContent = message; status.className = `mail-status${error ? " error" : " success"}`; }
async function api(path: string, options: RequestInit = {}) { const response = await adminFetch(`/mail${path}`, options); if (!response.ok) throw new Error(await responseMessage(response)); return response; }
const params = () => new URLSearchParams({ folder, uid_validity: validity });
function date(value: string) { const d = new Date(value); return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat("en", {dateStyle:"medium",timeStyle:"short"}).format(d); }
function busyList(busy: boolean) { el("mail-list").setAttribute("aria-busy", String(busy)); el<HTMLButtonElement>("mail-refresh").disabled = busy; }
function resetReader() { readVersion++; current = null; el("mail-detail").hidden = true; el("mail-empty").hidden = false; }
function renderMessages() {
  const list = el("mail-list"); list.replaceChildren();
  if (!messages.length) { const p=document.createElement("p"); p.style.padding="18px"; p.textContent=query ? "No messages match this search." : "No messages in this folder."; list.append(p); }
  messages.forEach((message) => { const button=document.createElement("button"); button.type="button"; button.className=`mail-row${message.seen ? "" : " unread"}`; button.setAttribute("aria-current", String(current?.uid===message.uid));
    const sender=document.createElement("strong"), subject=document.createElement("span"), stamp=document.createElement("small"); sender.textContent=message.sender || "Unknown sender"; subject.textContent=message.subject; stamp.textContent=date(message.date); button.append(sender,subject,stamp); button.addEventListener("click",()=>openMessage(message.uid).catch(showError)); list.append(button); });
  el("mail-page").textContent=total ? `${(page-1)*20+1}–${Math.min(page*20,total)} of ${total}` : "0 messages";
  el<HTMLButtonElement>("mail-previous").disabled=page<=1; el<HTMLButtonElement>("mail-next").disabled=page*20>=total;
}
function showError(error: unknown) { notify(error instanceof Error ? error.message : "The mailbox request failed.",true); }
async function loadMessages() {
  const version=++listVersion; resetReader(); busyList(true); notify("Loading messages…");
  try { const p=new URLSearchParams({folder,q:query,page:String(page)}); const response=await api(`/messages?${p}`); const data=await response.json(); if (version!==listVersion) return;
    messages=data.items; total=data.total; validity=data.uid_validity; renderMessages(); notify();
  } catch(error) { if(version===listVersion) { messages=[]; total=0; renderMessages(); showError(error); } }
  finally { if(version===listVersion) busyList(false); }
}
async function mutate(uid: string, change: object) { return api(`/messages/${uid}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({folder,uid_validity:validity,...change})}); }
async function openMessage(uid: string) {
  const version=++readVersion, selectedFolder=folder; notify("Opening message…");
  const response=await api(`/messages/${uid}?${params()}`); const message:Message=await response.json(); if(version!==readVersion || selectedFolder!==folder) return;
  current=message; el("mail-empty").hidden=true; el("mail-detail").hidden=false;
  el("mail-subject").textContent=message.subject; el("mail-from").textContent=message.sender; el("mail-to").textContent=message.to; el("mail-cc").textContent=message.cc || "—"; el("mail-date").textContent=date(message.date); el("mail-body").textContent=message.body || "This message has no readable text body.";
  const attachments=el("mail-attachments"); attachments.replaceChildren();
  const attachmentParams=params(); (message.attachments||[]).forEach((attachment)=>{ const button=document.createElement("button"); button.type="button"; button.textContent=`Download ${attachment.name} (${Math.max(1,Math.round(attachment.size/1024))} KB)`; button.addEventListener("click",async()=>{button.disabled=true;try{const response=await api(`/messages/${uid}/attachments/${attachment.index}?${attachmentParams}`); const blob=await response.blob(); const url=URL.createObjectURL(blob); const a=document.createElement("a"); a.href=url; a.download=attachment.name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(error){showError(error);}finally{button.disabled=false;}});attachments.append(button);});
  renderMessages(); notify(); el("mail-subject").focus();
  if (!message.seen) { try { await mutate(uid,{seen:true}); if(version!==readVersion)return; message.seen=true; messages=messages.map(m=>m.uid===uid?{...m,seen:true}:m); renderMessages(); } catch(error) { showError(error); } }
}
function renderFolders() {
  const nav=el("mail-folder-list"), move=el<HTMLSelectElement>("mail-move-folder"); nav.replaceChildren(); move.replaceChildren(new Option("Move to…",""));
  folders.forEach(item=>{const button=document.createElement("button");button.type="button";button.textContent=item.name; if(item.id===folder)button.setAttribute("aria-current","page");button.addEventListener("click",()=>{folder=item.id;page=1;query="";el<HTMLInputElement>("mail-query").value="";el("mail-folder-heading").textContent=item.name;renderFolders();loadMessages().catch(showError);});nav.append(button);if(item.id!==folder)move.add(new Option(item.name,item.id));});
}
function editorState() { return JSON.stringify([el<HTMLInputElement>("mail-compose-to").value,el<HTMLInputElement>("mail-compose-cc").value,el<HTMLInputElement>("mail-compose-subject").value,el<HTMLTextAreaElement>("mail-compose-body").value,Array.from(el<HTMLInputElement>("mail-compose-files").files||[]).map(f=>f.name)]); }
function closeEditor() { if(sending)return; if(editorState()!==draftSnapshot && !confirm("Discard this unsent email?"))return; editor.close(); }
function compose(mode: "new"|"reply"|"forward") {
  form.reset(); requestId=crypto.randomUUID(); replyId="";references="";el("mail-compose-status").textContent="";el<HTMLButtonElement>("mail-send").disabled=false;
  el("mail-editor-title").textContent=mode==="reply"?"Reply":mode==="forward"?"Forward email":"New email";
  if(current && mode!=="new") {el<HTMLInputElement>("mail-compose-subject").value=(mode==="reply"?/^re:/i:/^fwd:/i).test(current.subject)?current.subject:`${mode==="reply"?"Re":"Fwd"}: ${current.subject}`;
    el<HTMLTextAreaElement>("mail-compose-body").value=`\n\n${mode==="reply"?"On":"Forwarded message —"} ${date(current.date)}, ${current.sender}:\n${(current.body||"").split("\n").map(line=>"> "+line).join("\n")}`;
    if(mode==="reply"){el<HTMLInputElement>("mail-compose-to").value=current.reply_to;replyId=current.message_id.replace(/[\r\n]/g,"").slice(0,1000);references=`${current.references} ${replyId}`.replace(/[\r\n]/g,"").trim().slice(-2000);}
  }
  if(mode==="forward" && current?.attachments?.length)el("mail-compose-status").textContent="Original attachments are not included. Download and add any files you want to forward.";
  draftSnapshot=editorState();editor.showModal();el<HTMLInputElement>(mode==="reply"?"mail-compose-body":"mail-compose-to").focus();
}
async function encodeFile(file:File) {return new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("An attachment could not be read."));reader.readAsDataURL(file);});}
form.addEventListener("submit",async(event)=>{event.preventDefault();if(sending||!form.reportValidity())return;const files=Array.from(el<HTMLInputElement>("mail-compose-files").files||[]);const output=el("mail-compose-status");output.className="";if(files.length>10 || files.reduce((n,f)=>n+f.size,0)>8*1024*1024){output.textContent="Use up to 10 attachments totaling 8 MB or less.";output.className="error";return;}
  sending=true;const send=el<HTMLButtonElement>("mail-send");send.disabled=true;send.textContent="Sending…";output.textContent="Sending your email…";
  try { const attachments=await Promise.all(files.map(async file=>({name:file.name,content:await encodeFile(file)}))); const response=await api("/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:requestId,to:el<HTMLInputElement>("mail-compose-to").value,cc:el<HTMLInputElement>("mail-compose-cc").value,subject:el<HTMLInputElement>("mail-compose-subject").value,body:el<HTMLTextAreaElement>("mail-compose-body").value,in_reply_to:replyId,references,attachments})});const data=await response.json();
    if(!data.sent){output.textContent=data.warning || "Sending could not be confirmed. Check Spacemail before trying again.";output.className="error";send.disabled=true;return;}
    editor.close();form.reset();notify(data.warning || "Email sent.");
  }catch(error){output.textContent=error instanceof Error?error.message:"Sending failed.";output.className="error";send.disabled=false;}finally{sending=false;send.textContent="Send email";}
});
el("mail-compose").addEventListener("click",()=>compose("new"));el("mail-reply").addEventListener("click",()=>compose("reply"));el("mail-forward").addEventListener("click",()=>compose("forward"));el("mail-editor-close").addEventListener("click",closeEditor);editor.addEventListener("cancel",event=>{event.preventDefault();closeEditor();});
window.addEventListener("beforeunload",event=>{if(editor.open && (sending||editorState()!==draftSnapshot)){event.preventDefault();}});
el("mail-search").addEventListener("submit",event=>{event.preventDefault();query=el<HTMLInputElement>("mail-query").value.trim();page=1;loadMessages().catch(showError);});
el("mail-refresh").addEventListener("click",()=>loadMessages().catch(showError));el("mail-previous").addEventListener("click",()=>{if(page>1){page--;loadMessages().catch(showError);}});el("mail-next").addEventListener("click",()=>{if(page*20<total){page++;loadMessages().catch(showError);}});
el("mail-unread").addEventListener("click",async()=>{if(!current)return;const uid=current.uid;try{await mutate(uid,{seen:false});messages=messages.map(m=>m.uid===uid?{...m,seen:false}:m);renderMessages();notify("Marked as unread.");}catch(error){showError(error);}});
el("mail-move").addEventListener("click",async()=>{if(!current)return;const destination=el<HTMLSelectElement>("mail-move-folder").value;if(!destination){notify("Choose a destination folder first.",true);return;}const button=el<HTMLButtonElement>("mail-move");button.disabled=true;try{await mutate(current.uid,{destination});await loadMessages();notify("Message moved.");}catch(error){showError(error);}finally{button.disabled=false;}});
el("mail-check").addEventListener("click",async()=>{const button=el<HTMLButtonElement>("mail-check");button.disabled=true;notify("Checking incoming and outgoing mail…");try{await api("/connection",{method:"POST"});notify("Incoming and outgoing connections verified. No email was sent.");}catch(error){showError(error);}finally{button.disabled=false;}});
async function initialize() {const response=await requireAdmin("/mail/status");if(!response)return;const config=await response.json();el("mail-address").textContent=config.address;const fromNote=document.querySelector(".mail-from-note");if(fromNote)fromNote.textContent=`From ${config.address}`;if(!config.configured){el("mail-setup").hidden=false;return;}notify("Connecting to Spacemail…");el("mail-check").hidden=false;const result=await(await api("/folders")).json();folders=result.items;folder=folders.find(f=>f.id.toUpperCase()==="INBOX")?.id || folders[0]?.id || "INBOX";renderFolders();el("mail-folder-heading").textContent=folders.find(f=>f.id===folder)?.name || "Inbox";el("mail-workspace").hidden=false;await loadMessages();}
initialize().catch(showError);
