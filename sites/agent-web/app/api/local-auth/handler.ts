import {getAuthMode} from '../../auth';
import {loadLocalConfig,LoginLimiter} from '../../lib/local-auth';
import {handleLocalAuth} from '../../lib/local-auth-http';
const limiter=new LoginLimiter();
export async function handle(request:Request,action:'login'|'logout'){
 try{
  const mode=getAuthMode();let config=null;try{if(mode==='local')config=loadLocalConfig();}catch{}
  return await handleLocalAuth(request,action,{mode,config,origin:process.env.AGENT_LOCAL_ORIGIN||'',limiter,now:()=>Math.floor(Date.now()/1000)});
 }catch{return Response.json({error:'service_not_configured'},{status:503,headers:{'cache-control':'no-store'}});}
}
