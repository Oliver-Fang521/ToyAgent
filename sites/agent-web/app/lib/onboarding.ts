import type { Selection } from "./models.ts";
import type { MemoryMode } from "./memory.ts";
type Storage = Pick<globalThis.Storage,"getItem"|"setItem"|"removeItem">;
type Draft = {question:string;selection:Selection;ownerHint:string|null;createdAt?:number;memoryMode?:MemoryMode;attachmentToken?:string};
const key="toy-agent-signin-draft";
export function recoveryAction(error:{status?:number;code?:string}){
 if(error.status===401||error.code==="sign_in_required")return "sign_in";
 if(error.code?.startsWith("credential_")||error.code==="invalid_credential"||error.code==="provider_authentication")return "key";
 if(error.code==="capability_unavailable"||error.code==="model_unavailable")return "model";
 return "none";
}
export function nextGate(input:{signedIn:boolean;selection:Selection;credentialStatus:{provider:string;configured:boolean}[];busy:boolean}){
 if(input.busy)return "busy";
 if(!input.signedIn)return "sign_in";
 return input.credentialStatus.some(s=>s.provider===input.selection.provider&&s.configured)?"ready":"key";
}
export function saveDraft(storage:Storage,draft:Draft,now=Date.now()){
 try{storage.setItem(key,JSON.stringify({question:draft.question.slice(0,12000),selection:{provider:draft.selection.provider,model:draft.selection.model,...(draft.selection.reasoning_effort?{reasoning_effort:draft.selection.reasoning_effort}:{})},ownerHint:draft.ownerHint,memoryMode:draft.memoryMode==="temporary"?"temporary":"normal",...(draft.attachmentToken?{attachmentToken:draft.attachmentToken}:{}),createdAt:now}));}
 catch{throw new Error("draft_storage_unavailable");}
}
export function clearDraft(storage:Storage){try{storage.removeItem(key);}catch{throw new Error("draft_storage_unavailable");}}
export function consumeDraft(storage:Storage,now=Date.now(),ownerHint?:string):Draft|null{
 try{
  const raw=storage.getItem(key);storage.removeItem(key);if(!raw)return null;
  const draft=JSON.parse(raw);
  if(typeof draft.question!=="string"||draft.question.length>12000||!Number.isFinite(draft.createdAt)||now<draft.createdAt||now-draft.createdAt>900000||!['openai','deepseek'].includes(draft.selection?.provider)||typeof draft.selection?.model!=="string"||draft.selection.model.length>128)return null;
  if(draft.ownerHint!==null&&draft.ownerHint!==ownerHint)return null;
  if(draft.selection.reasoning_effort!==undefined&&!['none','minimal','low','medium','high','xhigh','max'].includes(draft.selection.reasoning_effort))return null;
  if(draft.attachmentToken!==undefined&&(typeof draft.attachmentToken!=="string"||! /^[0-9a-f-]{36}$/.test(draft.attachmentToken)))return null;
  return {question:draft.question,selection:draft.selection,ownerHint:draft.ownerHint,createdAt:draft.createdAt,memoryMode:draft.memoryMode==="temporary"?"temporary":"normal",...(draft.attachmentToken?{attachmentToken:draft.attachmentToken}:{})};
 }catch{throw new Error("draft_storage_unavailable");}
}
