import test from 'node:test';import assert from 'node:assert/strict';
import {handleLocalAuth,resolveLocalIdentity,safeLocalReturn} from '../app/lib/local-auth-http.ts';
import {LoginLimiter,parseLocalConfig,issueSession} from '../app/lib/local-auth.ts';
const config=parseLocalConfig({version:1,owner_id:'01234567-89ab-4def-8123-456789abcdef',service_token:'s'.repeat(43),session_key:Buffer.alloc(32,1).toString('base64'),password:{algorithm:'scrypt',salt:'AAECAwQFBgcICQoLDA0ODw==',hash:'qG0qPpHc44W5ow6BmhdyCTZbJJr3rG1dRr7DbHF8U0E=',n:16384,r:8,p:1}});
const settings=()=>({mode:'local',config,origin:'http://localhost:3000',limiter:new LoginLimiter(),now:()=>1000});
const request=(body='password=synthetic-local-password&return_to=%2F',extra:Record<string,string>={})=>new Request('http://localhost:3000/api/local-auth/login',{method:'POST',headers:{host:'localhost:3000',origin:'http://localhost:3000','content-type':'application/x-www-form-urlencoded',...extra},body});
test('login sets bounded private cookie and safe redirect; logout clears it',async()=>{
 const s=settings(),response=await handleLocalAuth(request(), 'login',s);
 assert.equal(response.status,303);assert.equal(response.headers.get('location'),'/');
 const cookie=response.headers.get('set-cookie')!;
 for(const flag of ['HttpOnly','SameSite=Strict','Path=/','Max-Age=43200'])assert.ok(cookie.includes(flag));
 assert.ok(!cookie.includes('Domain=')&&!cookie.includes('Secure'));
 assert.equal(response.headers.get('cache-control'),'no-store');
 const out=await handleLocalAuth(request(''),'logout',s);assert.ok(out.headers.get('set-cookie')!.includes('Max-Age=0'));
});
test('local auth rejects configuration, method, origin, size and type violations',async()=>{
 assert.equal((await handleLocalAuth(request(),'login',{...settings(),config:null})).status,503);
 assert.equal((await handleLocalAuth(request(),'login',{...settings(),mode:'sites'})).status,404);
 assert.equal((await handleLocalAuth(new Request('http://localhost:3000'),'logout',settings())).status,405);
 assert.equal((await handleLocalAuth(request('',{origin:'http://evil.example'}),'logout',settings())).status,403);
 assert.equal((await handleLocalAuth(request('x'.repeat(4097)),'login',settings())).status,413);
 assert.equal((await handleLocalAuth(request('',{'content-type':'multipart/form-data'}),'login',settings())).status,415);
 assert.equal(safeLocalReturn('//evil.example'),'/');assert.equal(safeLocalReturn('/\\evil.example'),'/');
 assert.equal(safeLocalReturn('/api/local-auth/login'),'/');
});
test('wrong password returns generic failure and sixth request is rate limited',async()=>{
 const s=settings();
 for(let i=0;i<5;i++)assert.equal((await handleLocalAuth(request('password=wrong-password'),'login',s)).status,401);
 const response=await handleLocalAuth(request(),'login',s);
 assert.equal(response.status,429);assert.equal(response.headers.get('retry-after'),'900');
});
test('authentication modes never borrow other identity credentials',()=>{
 const cookie=issueSession(config,1000),spoof={userId:'attacker'};
 assert.equal(resolveLocalIdentity('local',null,config,spoof,1001),null);
 assert.deepEqual(resolveLocalIdentity('local',cookie,config,spoof,1001),{userId:config.owner_id});
 assert.equal(resolveLocalIdentity('sites',cookie,config,null,1001),null);
 assert.equal(resolveLocalIdentity('unknown',cookie,config,spoof,1001),null);
});
test('concurrent login admission never hashes more than five attempts',async()=>{
 const s=settings();
 const responses=await Promise.all(Array.from({length:20},()=>handleLocalAuth(request('password=wrong-password'),'login',s)));
 assert.equal(responses.filter(r=>r.status===401).length,5);
 assert.equal(responses.filter(r=>r.status===429).length,15);
});
