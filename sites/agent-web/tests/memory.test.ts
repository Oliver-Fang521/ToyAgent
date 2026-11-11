import test from "node:test";
import assert from "node:assert/strict";
import { memoryApi, memoryLabel, memoryReportLabel, editorFailure, sameAttempt } from "../app/lib/memory.ts";
import { saveDraft, consumeDraft } from "../app/lib/onboarding.ts";
import { completedMessage } from "../app/lib/history.ts";

test("default memory is visible but never falsely saved while loading",()=>{
 assert.match(memoryLabel(null,"normal"),/正在读取/);
 assert.equal(memoryLabel({enabled:false,revision:1,generation:1,capture_after:0},"normal"),"长期记忆已暂停");
 assert.equal(memoryLabel(null,"temporary"),"临时对话");
 assert.match(memoryReportLabel({status:"unavailable",used_ids:[],saved_ids:[],skipped_count:0}),/未保存/);
});
test("management forwards JSON revision and safe errors without submitting a question",async()=>{
 let seen="",payload:unknown;
 const fake:typeof fetch=async(url,options)=>{seen=String(url);payload=JSON.parse(String(options?.body));return Response.json({error:"memory_conflict"},{status:409});};
 await assert.rejects(memoryApi("memories/00000000-0000-0000-0000-000000000000/update",{content:"I study Rust",revision:2},fake),error=>{
  const failure=editorFailure(error,"unsaved text");
  assert.equal(failure.content,"unsaved text");assert.equal(failure.reload,true);return true;
 });
 assert.match(seen,/\/update$/);assert.deepEqual(payload,{content:"I study Rust",revision:2});
});
test("changed memory mode creates a new attempt, reconnect retains matching mode",()=>{
 assert.equal(sameAttempt({question:"q",file:null,selection:"openai",memoryMode:"normal"},{question:"q",file:null,selection:"openai",memoryMode:"temporary"}),false);
 assert.equal(sameAttempt({question:"q",file:null,selection:"openai",memoryMode:"temporary"},{question:"q",file:null,selection:"openai",memoryMode:"temporary"}),true);
});
test("temporary draft consumes once and excludes memory contents",()=>{
 const values=new Map<string,string>();const storage={getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>{values.set(k,v);},removeItem:(k:string)=>{values.delete(k);}};
 saveDraft(storage,{question:"q",selection:{provider:"openai",model:"gpt-6.1-sol"},ownerHint:null,memoryMode:"temporary",memories:"must-not-store"} as never,100);
 assert.ok(![...values.values()][0].includes("must-not-store"));
 assert.equal(consumeDraft(storage,200)?.memoryMode,"temporary");assert.equal(consumeDraft(storage,200),null);
});
test("safe memory report survives completed result reopening",()=>{
 const memory={status:"temporary" as const,used_ids:[],saved_ids:[],skipped_count:0};
 assert.deepEqual(completedMessage({status:"completed",result:{answer:"ok",evidence:[],memory}})?.memory,memory);
});
