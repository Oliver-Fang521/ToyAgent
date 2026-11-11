import test from "node:test";
import assert from "node:assert/strict";
import {saveSigninAttachment, takeSigninAttachment} from "../app/lib/signin-attachment.ts";

test("attachment is restored once and only for its draft and account",async()=>{
 const records=new Map<string,unknown>();
 const store={put:async(k:string,v:unknown)=>{records.set(k,v)},take:async(k:string)=>{const v=records.get(k);records.delete(k);return v}};
 const file=new File(["data"],"sample.pdf",{type:"application/pdf"});
 await saveSigninAttachment(store,"id",file,"alice",100);
 const restored=await takeSigninAttachment(store,"id","alice",200);
 assert.equal(restored?.name,"sample.pdf");assert.equal(await restored?.text(),"data");
 assert.equal(await takeSigninAttachment(store,"id","alice",200),null);
 await saveSigninAttachment(store,"other",file,"alice",100);
 assert.equal(await takeSigninAttachment(store,"other","bob",200),null);
 await saveSigninAttachment(store,"old",file,null,100);
 assert.equal(await takeSigninAttachment(store,"old","bob",900101),null);
 await saveSigninAttachment(store,"guest",file,null,100);
 assert.equal((await takeSigninAttachment(store,"guest","bob",200))?.name,"sample.pdf");
});
