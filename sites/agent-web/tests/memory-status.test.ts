import test from 'node:test';
import assert from 'node:assert/strict';

test('open topics and deletion modes do not invent permanent semantics',async()=>{
 const {memoryTopicPayload,memoryDeleteBody}=await import('../app/lib/memory-editor.ts');
 assert.deepEqual(memoryTopicPayload('profile','称呼',''),{category:'profile',facet:'称呼',entity:'self'});
 assert.deepEqual(memoryTopicPayload('project','shipping:milestone','ToyAgent'),{category:'project',facet:'shipping:milestone',entity:'ToyAgent'});
 assert.throws(()=>memoryTopicPayload('profile',' ','self'));
 assert.deepEqual(memoryDeleteBody(3,false),{revision:3,permanent:false});
 assert.deepEqual(memoryDeleteBody(3,true),{revision:3,permanent:true});
});

test('management status is based on committed state and retry needs explicit consent',async()=>{
 const {maintenanceLabel,canRetryMemory}=await import('../app/lib/memory.ts');
 assert.notEqual(maintenanceLabel('pending'),maintenanceLabel('completed'));
 assert.notEqual(maintenanceLabel('failed'),maintenanceLabel('uncertain'));
 assert.equal(canRetryMemory({state:'failed',retry_count:0},false),false);
 assert.equal(canRetryMemory({state:'failed',retry_count:0},true),true);
 assert.equal(canRetryMemory({state:'uncertain',retry_count:1},true),false);
});

test('maintenance capacity failures remain actionable and never expose raw exceptions',async()=>{
 const {maintenanceReason}=await import('../app/lib/memory.ts');
 assert.doesNotMatch(maintenanceReason('memory_limit'),/200|删除/);
 assert.match(maintenanceReason('memory_limit'),/预算/);
 assert.equal(maintenanceReason('memory_timeout'),'模型调用超时');
 assert.equal(maintenanceReason('credential_expired'),'请检查模型 Key');
 assert.ok(!maintenanceReason('private-provider-error-secret').includes('secret'));
});

test('retry and restore proxy paths still require identity and same origin',async()=>{
 const {forwardAgentRequest}=await import('../app/lib/proxy.ts');
 const id='00000000-0000-4000-8000-000000000001';
 for(const path of [`memories/jobs/${id}/retry`,`memories/barriers/${id}/restore`]){
  let sent=0;
  const upstream=async()=>{sent++;return Response.json({ok:true})};
  const request=()=>new Request('https://app.test/api/agent/'+path,{method:'POST',headers:{origin:'https://app.test'},body:'{}'});
  assert.equal((await forwardAgentRequest(request(),path.split('/'),null,{base:'https://api.test',token:'test'},upstream)).status,401);
  assert.equal((await forwardAgentRequest(request(),path.split('/'),{userId:'alice'},{base:'https://api.test',token:'test'},upstream)).status,200);
  assert.equal(sent,1);
 }
});
