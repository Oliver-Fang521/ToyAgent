import test from 'node:test';
import assert from 'node:assert/strict';
import {parseLocalConfig,verifyPassword,issueSession,verifySession,validateLocalRequest,LoginLimiter} from '../app/lib/local-auth.ts';

const fixture={version:1,owner_id:'01234567-89ab-4def-8123-456789abcdef',service_token:'s'.repeat(43),
 session_key:Buffer.alloc(32,1).toString('base64'),password:{algorithm:'scrypt',salt:Buffer.from(Array.from({length:16},(_,i)=>i)).toString('base64'),
 hash:'qG0qPpHc44W5ow6BmhdyCTZbJJr3rG1dRr7DbHF8U0E=',n:16384,r:8,p:1}};

test('Python scrypt records authenticate only matching bounded passwords',async()=>{
 const c=parseLocalConfig(fixture);
 assert.equal(await verifyPassword('synthetic-local-password',c),true);
 assert.equal(await verifyPassword('wrong-password',c),false);
 assert.equal(await verifyPassword('x'.repeat(1025),c),false);
 for(const change of [{version:2},{password:{...fixture.password,n:2**30}},{session_key:'bad'}])
  assert.throws(()=>parseLocalConfig({...fixture,...change}),/local_configuration/);
});
test('signed sessions reject tampering expiry future issue time and reset keys',()=>{
 const c=parseLocalConfig(fixture), token=issueSession(c,1000);
 assert.deepEqual(verifySession(token,c,1001),{userId:fixture.owner_id});
 assert.equal(verifySession(token,c,44200),null);
 assert.equal(verifySession(token,c,999),null);
 assert.equal(verifySession(token+'x',c,1001),null);
 assert.equal(verifySession(token.split('.')[0]+'.bad',c,1001),null);
 assert.equal(verifySession(token,{...c,session_key:Buffer.alloc(32,2).toString('base64')},1001),null);
 assert.notEqual(issueSession(c,1000),token);
 const body=Buffer.from(token.split('.')[0],'base64url').toString();
 assert.ok(!body.includes(c.service_token)&&!body.includes(c.password.hash));
});
test('local writes require exact configured loopback host and origin',()=>{
 const make=(origin:string|null,host='localhost:3000')=>new Request('http://localhost:3000/api/local-auth/login',{method:'POST',headers:{host,...(origin?{origin}:{})}});
 assert.equal(validateLocalRequest(make('http://localhost:3000'),'http://localhost:3000'),true);
 for(const origin of [null,'null','http://evil.example','http://localhost:3001','https://localhost:3000'])
  assert.equal(validateLocalRequest(make(origin),'http://localhost:3000'),false);
 assert.equal(validateLocalRequest(make('http://localhost:3000','evil.example'),'http://localhost:3000'),false);
 assert.equal(validateLocalRequest(make('http://localhost:3000'),'http://evil.example'),false);
});
test('login limiter blocks sixth attempt and recovers after bounded window',()=>{
 const limiter=new LoginLimiter();
 for(let i=0;i<5;i++){assert.equal(limiter.allow(1000),0);limiter.failed(1000);}
 assert.equal(limiter.allow(1000),900);
 assert.equal(limiter.allow(901000),0);
 limiter.failed(901000);limiter.clear();assert.equal(limiter.allow(901000),0);
});
