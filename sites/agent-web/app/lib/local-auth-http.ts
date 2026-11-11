import {issueSession,verifySession,verifyPassword,validateLocalRequest} from './local-auth.ts';
import type {LocalAuthConfig,LoginLimiter} from './local-auth.ts';
export const localCookie='toyagent_local_session';
export function resolveLocalIdentity(mode:string,cookie:string|null,config:LocalAuthConfig|null,sites:{userId:string}|null,now:number){
 if(mode==='sites')return sites;
 if(mode!=='local'||!config||!cookie)return null;
 return verifySession(cookie,config,now);
}
export function safeLocalReturn(value:string):string{
 try{if(!value.startsWith('/')||value.startsWith('//')||/[\\\u0000-\u001f]/.test(value))return '/';
 const url=new URL(value,'http://app.local');
 if(url.origin!=='http://app.local'||url.pathname.startsWith('/api/')||url.pathname.startsWith('/local-login'))return '/';
 return url.pathname+url.search+url.hash;}catch{return '/';}
}
type Settings={mode:string;config:LocalAuthConfig|null;origin:string;limiter:LoginLimiter;now:()=>number};
function error(code:string,status:number,extra:Record<string,string>={}){
 return Response.json({error:code},{status,headers:{'cache-control':'no-store',...extra}});
}
async function boundedBody(request:Request):Promise<string|null>{
 const reader=request.body?.getReader();if(!reader)return '';
 const chunks:Uint8Array[]=[];let total=0;
 try{while(true){const {value,done}=await reader.read();if(done)break;total+=value.length;
  if(total>4096){await reader.cancel();return null;}chunks.push(value);}
  return Buffer.concat(chunks).toString('utf8');
 }finally{reader.releaseLock();}
}
export async function handleLocalAuth(request:Request,action:'login'|'logout',s:Settings):Promise<Response>{
 if(s.mode!=='local')return error('not_found',404);
 if(request.method!=='POST')return error('method_not_allowed',405);
 if(!validateLocalRequest(request,s.origin))return error('invalid_origin',403);
 if(!s.config)return error('service_not_configured',503);
 if(Number(request.headers.get('content-length')||0)>4096)return error('request_too_large',413);
 if(action==='logout')return new Response(null,{status:303,headers:{location:'/local-login','cache-control':'no-store',
  'set-cookie':`${localCookie}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0`}});
 if(request.headers.get('content-type')?.split(';')[0]!=='application/x-www-form-urlencoded')return error('unsupported_content_type',415);
 const retry=s.limiter.allow(s.now()*1000);if(retry)return error('try_later',429,{'retry-after':String(retry)});
 const body=await boundedBody(request);if(body===null)return error('request_too_large',413);
 const form=new URLSearchParams(body),password=form.get('password')||'';
 const reservationRetry=s.limiter.allow(s.now()*1000);
 if(reservationRetry)return error('try_later',429,{'retry-after':String(reservationRetry)});
 s.limiter.failed(s.now()*1000); // reserve a slot before async hashing; concurrent attempts stay bounded
 if(!await verifyPassword(password,s.config))return error('invalid_password',401);
 s.limiter.clear();
 return new Response(null,{status:303,headers:{location:safeLocalReturn(form.get('return_to')||'/'),'cache-control':'no-store',
  'set-cookie':`${localCookie}=${issueSession(s.config,s.now())}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200`}});
}
