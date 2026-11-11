import {createHmac,randomBytes,scrypt,timingSafeEqual} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {dirname,join} from 'node:path';

export type LocalAuthConfig={version:1;owner_id:string;service_token:string;session_key:string;
 password:{algorithm:'scrypt';salt:string;hash:string;n:16384;r:8;p:1}};
const failure=()=>new Error('local_configuration');
function bytes(value:unknown,length:number):Buffer{
 if(typeof value!=='string')throw failure();
 const result=Buffer.from(value,'base64');
 if(result.length!==length||result.toString('base64')!==value)throw failure();
 return result;
}
export function parseLocalConfig(value:unknown):LocalAuthConfig{
 if(!value||typeof value!=='object')throw failure();
 const c=value as LocalAuthConfig,p=c.password;
 if(c.version!==1||! /^[a-f0-9-]{36}$/.test(c.owner_id)||typeof c.service_token!=='string'||c.service_token.length<32||
  !p||p.algorithm!=='scrypt'||p.n!==16384||p.r!==8||p.p!==1)throw failure();
 bytes(c.session_key,32);bytes(p.salt,16);bytes(p.hash,32);
 return c;
}
let cached:{path:string;config:LocalAuthConfig}|null=null;
export function loadLocalConfig(path=process.env.AGENT_LOCAL_CONFIG):LocalAuthConfig{
 if(!path)throw failure();
 if(cached?.path===path)return cached.config;
 try{
  if(readFileSync(join(dirname(path),'.complete'),'utf8')!=='1\n')throw failure();
  const content=readFileSync(path);if(content.length>8192)throw failure();
  const config=parseLocalConfig(JSON.parse(content.toString('utf8')));cached={path,config};return config;
 }catch{throw failure();}
}
export async function verifyPassword(password:string,c:LocalAuthConfig):Promise<boolean>{
 if(typeof password!=='string'||password.length<12||Buffer.byteLength(password)>1024)return false;
 const result=await new Promise<Buffer>((resolve,reject)=>scrypt(password,bytes(c.password.salt,16),32,
  {N:16384,r:8,p:1,maxmem:64*1024*1024},(error,key)=>error?reject(error):resolve(key)));
 return timingSafeEqual(result,bytes(c.password.hash,32));
}
function sign(body:string,c:LocalAuthConfig){return createHmac('sha256',bytes(c.session_key,32)).update(body).digest('base64url');}
export function issueSession(c:LocalAuthConfig,nowSeconds:number):string{
 const body=Buffer.from(JSON.stringify({v:1,sub:c.owner_id,iat:nowSeconds,exp:nowSeconds+43200,nonce:randomBytes(16).toString('hex')})).toString('base64url');
 return `${body}.${sign(body,c)}`;
}
export function verifySession(value:string,c:LocalAuthConfig,nowSeconds:number):{userId:string}|null{
 try{
  if(typeof value!=='string'||value.length>1024)return null;
  const parts=value.split('.');if(parts.length!==2)return null;
  const [body,signature]=parts,expected=sign(body,c);
  if(signature.length!==expected.length||!timingSafeEqual(Buffer.from(signature),Buffer.from(expected)))return null;
  const decoded=Buffer.from(body,'base64url');if(decoded.toString('base64url')!==body)return null;
  const p=JSON.parse(decoded.toString());
  if(p.v!==1||p.sub!==c.owner_id||!Number.isInteger(p.iat)||p.iat>nowSeconds||p.exp!==p.iat+43200||p.exp<=nowSeconds||! /^[a-f0-9]{32}$/.test(p.nonce))return null;
  return {userId:c.owner_id};
 }catch{return null;}
}
export function validLocalOrigin(origin:string):boolean{
 try{const u=new URL(origin);return u.protocol==='http:'&&['localhost','127.0.0.1'].includes(u.hostname)&&u.origin===origin&&!u.username&&!u.password;}catch{return false;}
}
export function validateLocalRequest(request:Request,configuredOrigin:string):boolean{
 if(!validLocalOrigin(configuredOrigin))return false;
 return request.headers.get('origin')===configuredOrigin&&request.headers.get('host')===new URL(configuredOrigin).host;
}
export class LoginLimiter{
 private failures:number[]=[];
 allow(nowMs:number):number{
  this.failures=this.failures.filter(t=>t>nowMs-900000);
  return this.failures.length>=5?Math.max(1,Math.ceil((this.failures[0]+900000-nowMs)/1000)):0;
 }
 failed(nowMs:number){if(this.failures.length<5)this.failures.push(nowMs);}
 clear(){this.failures=[];}
}
