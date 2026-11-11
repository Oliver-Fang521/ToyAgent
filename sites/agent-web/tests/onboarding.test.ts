import test from "node:test";
import assert from "node:assert/strict";
import { nextGate, saveDraft, consumeDraft, clearDraft, recoveryAction } from "../app/lib/onboarding.ts";
const selection = {provider:"deepseek" as const,model:"deepseek-flash"};
function storage(){const values=new Map<string,string>();return {getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>{values.set(k,v);},removeItem:(k:string)=>{values.delete(k);},values};}
test("onboarding gates on matching credentials without sending",()=>{
  assert.equal(nextGate({signedIn:false,selection,credentialStatus:[],busy:false}),"sign_in");
  assert.equal(nextGate({signedIn:true,selection,credentialStatus:[{provider:"openai",configured:true}],busy:false}),"key");
  assert.equal(nextGate({signedIn:true,selection,credentialStatus:[{provider:"deepseek",configured:true}],busy:false}),"ready");
  assert.equal(nextGate({signedIn:true,selection,credentialStatus:[],busy:true}),"busy");
});
test("draft is bounded, single-consume and never contains keys or handles",()=>{
 const s=storage();
 saveDraft(s,{question:"translate Friday",selection,ownerHint:null,key:"do-not-save",handle:"do-not-save"} as never,100);
 assert.ok(!JSON.stringify([...s.values]).includes("do-not-save"));
 assert.equal(consumeDraft(s,200)?.question,"translate Friday");
 assert.equal(consumeDraft(s,201),null);
 saveDraft(s,{question:"old",selection,ownerHint:null},100);
 assert.equal(consumeDraft(s,900101),null);
});
test("owner change or unavailable storage cannot silently restore",()=>{
 const s=storage(); saveDraft(s,{question:"private",selection,ownerHint:"alice"},100);
 assert.equal(consumeDraft(s,200,"bob"),null);
 saveDraft(s,{question:"private",selection,ownerHint:null},100);clearDraft(s);assert.equal(consumeDraft(s,200),null);
 assert.throws(()=>saveDraft({setItem(){throw Error("disabled");}} as never,{question:"q",selection,ownerHint:null},100),/draft_storage_unavailable/);
});
test("expired authentication and credentials have distinct recovery actions",()=>{
 assert.equal(recoveryAction({status:401,code:"unauthorized"}),"sign_in");
 assert.equal(recoveryAction({status:422,code:"credential_expired"}),"key");
 assert.equal(recoveryAction({status:502,code:"provider_authentication"}),"key");
 assert.equal(recoveryAction({status:422,code:"capability_unavailable"}),"model");
});
test("draft carries a bounded per-tab attachment token",()=>{
 const s=storage();const attachmentToken="6c630a33-d51e-4d03-97ad-8d00443daa17";
 saveDraft(s,{question:"",selection,ownerHint:null,attachmentToken},100);
 assert.equal(consumeDraft(s,200,"alice")?.attachmentToken,attachmentToken);
});
