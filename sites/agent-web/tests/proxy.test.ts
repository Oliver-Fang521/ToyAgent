import test from "node:test";
test("memory routes require identity and origin and forward only bounded pagination", async()=>{
 const settings={base:"https://backend.example",token:"secret"};
 assert.equal((await forwardAgentRequest(new Request("https://site.example/api/agent/memories"),["memories"],null,settings)).status,401);
 let seen="";
 const result=await forwardAgentRequest(new Request("https://site.example/api/agent/memories?offset=50&owner=bob"),["memories"],{userId:"alice"},settings,async url=>{seen=String(url);return Response.json({items:[]});});
 assert.equal(result.status,200);assert.equal(seen,"https://backend.example/v1/memories?offset=50");
 assert.equal((await forwardAgentRequest(new Request("https://site.example/api/agent/memories/clear",{method:"POST",headers:{origin:"https://evil.example"}}),["memories","clear"],{userId:"alice"},settings)).status,403);
});
test("anonymous catalog read is the only identity exception",async()=>{
 const upstream:typeof fetch=async(_url,options)=>{
  assert.equal(new Headers(options?.headers).get("authorization"),"Bearer server-token");
  assert.equal(new Headers(options?.headers).get("x-agent-user"),null);
  return Response.json({models:[]});
 };
 assert.equal((await forwardAgentRequest(new Request("https://site.example/api/agent/model-catalog"),["model-catalog"],null,{base:"https://backend.example",token:"server-token"},upstream)).status,200);
 assert.equal((await forwardAgentRequest(new Request("https://site.example/api/agent/model-catalog",{method:"POST"}),["model-catalog"],null,{base:"https://backend.example",token:"server-token"},upstream)).status,401);
});
import assert from "node:assert/strict";
import { forwardAgentRequest, safeSourceUrl } from "../app/lib/proxy.ts";

test("run event streams require identity and forward SSE without buffering",async()=>{
 const path=["runs","00000000-0000-4000-8000-000000000001","events"];
 const req=new Request("https://site.example/api/agent/runs/00000000-0000-4000-8000-000000000001/events");
 const settings={base:"https://backend.example",token:"secret"};
 assert.equal((await forwardAgentRequest(req,path,null,settings)).status,401);
 const result=await forwardAgentRequest(req,path,{userId:"alice"},settings,async(_url,options)=>{
  assert.equal(new Headers(options?.headers).get("x-agent-user"),"alice");
  return new Response("data: {\"status\":\"running\"}\n\n",{headers:{"content-type":"text/event-stream"}});
 });
 assert.match(result.headers.get("content-type")||"",/^text\/event-stream/);
 assert.equal(await result.text(),"data: {\"status\":\"running\"}\n\n");
});

test("anonymous execution and traversal are rejected", async () => {
  const req = new Request("https://site.example/api/agent/conversations");
  assert.equal((await forwardAgentRequest(req, ["conversations"], null, {})).status, 401);
  assert.equal((await forwardAgentRequest(req, ["..", "health"], {userId:"alice"}, {})).status, 404);
});
test("proxy replaces browser identity and authorization", async () => {
  let seen: Headers | null = null;
  const req = new Request("https://site.example/api/agent/conversations", {
    headers:{"x-agent-user":"bob", authorization:"Bearer attacker"}});
  const result = await forwardAgentRequest(req, ["conversations"], {userId:"alice"},
    {base:"https://backend.example",token:"service-secret"}, async (_url, options) => {
      seen = new Headers(options?.headers); return Response.json([]);
    });
  assert.equal(result.status, 200);
  assert.equal(seen!.get("x-agent-user"), "alice");
  assert.equal(seen!.get("authorization"), "Bearer service-secret");
});
test("cross-origin paid requests and unsafe sources are blocked", async () => {
  const req = new Request("https://site.example/api/agent/conversations", {method:"POST",headers:{origin:"https://evil.example"}});
  assert.equal((await forwardAgentRequest(req,["conversations"],{userId:"alice"},{base:"https://backend.example",token:"secret"})).status,403);
  assert.equal(safeSourceUrl("javascript:alert(1)"), null);
  assert.equal(safeSourceUrl("https://example.org"), "https://example.org/");
});
test("conversation pagination forwards only a validated offset", async () => {
  let seen = "";
  const req = new Request("https://site.example/api/agent/conversations/12345678-1234-1234-1234-123456789abc?offset=100&token=evil");
  await forwardAgentRequest(req,["conversations","12345678-1234-1234-1234-123456789abc"],{userId:"alice"},
    {base:"https://backend.example",token:"secret"},async url => { seen=String(url); return Response.json([]); });
  assert.equal(seen,"https://backend.example/v1/conversations/12345678-1234-1234-1234-123456789abc?offset=100");
});
test("credential routes are protected and handles forwarded only to run starts", async () => {
  for (const path of [["credentials"],["credentials","delete"],["credentials","migrate-model"],["credential-sessions"],
    ["conversations","12345678-1234-1234-1234-123456789abc","runs"]]) {
    let headers = new Headers();
    const result = await forwardAgentRequest(new Request("https://site.example/api/agent/"+path.join("/"), {
      method:"POST",headers:{origin:"https://site.example","x-agent-credential-handle":"fake-handle"}}),path,{userId:"alice"},
      {base:"https://backend.example",token:"secret"},async (_url, options) => {headers=new Headers(options?.headers); return Response.json({});});
    assert.equal(result.status,200);
    assert.equal(headers.get("x-agent-credential-handle"),path.at(-1)==="runs" ? "fake-handle":null);
  }
  const hostile = new Request("https://site.example/api/agent/credentials",{method:"POST",headers:{origin:"https://evil.example"}});
  assert.equal((await forwardAgentRequest(hostile,["credentials"],{userId:"alice"},{base:"https://backend.example",token:"secret"})).status,403);
});
test("Worker-compatible manual redirects never forward credentials to another destination",async()=>{
  let calls=0;
  let redirect:RequestRedirect|undefined;
  const response=await forwardAgentRequest(new Request("https://site.example/api/agent/credentials"),["credentials"],{userId:"alice"},
    {base:"https://backend.example",token:"secret"},async(_url,options)=>{
      calls++;redirect=options?.redirect;
      return new Response(null,{status:302,headers:{location:"https://evil.example"}});
    });
  assert.equal(redirect,"manual");assert.equal(calls,1);assert.equal(response.status,503);assert.equal(response.headers.get("location"),null);
});
