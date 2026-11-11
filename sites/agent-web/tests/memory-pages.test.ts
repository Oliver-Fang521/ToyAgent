import test from "node:test";
import assert from "node:assert/strict";
import {loadMemoryPage} from "../app/lib/memory.ts";
import {forwardAgentRequest} from "../app/lib/proxy.ts";

test("management search passes full query and opaque continuation to server",async()=>{
 let request="";
 const fake:typeof fetch=async(url)=>{request=String(url);return Response.json({items:[],total:1000,next_cursor:"opaque-next",coverage:"truncated"});};
 const page=await loadMemoryPage("数学", "opaque-first", fake);
 const url=new URL(request,"http://localhost");
 assert.equal(url.searchParams.get("q"),"数学");
 assert.equal(url.searchParams.get("cursor"),"opaque-first");
 assert.equal(page.total,1000);assert.equal(page.next_cursor,"opaque-next");
});

test("memory search proxy forwards only bounded search arguments, not client identity",async()=>{
 let target="",owner="";
 const fake:typeof fetch=async(url,options)=>{target=String(url);owner=new Headers(options?.headers).get("x-agent-user")||"";return Response.json({items:[]});};
 const request=new Request("https://app.test/api/agent/memories/search?q=math&cursor=opaque&limit=20&owner=bob");
 const response=await forwardAgentRequest(request,["memories","search"],{userId:"alice"},{base:"https://backend.test",token:"test"},fake);
 assert.equal(response.status,200);assert.equal(owner,"alice");
 const url=new URL(target);assert.equal(url.searchParams.get("q"),"math");assert.equal(url.searchParams.get("cursor"),"opaque");assert.equal(url.searchParams.has("owner"),false);
});
