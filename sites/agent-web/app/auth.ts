import {headers} from 'next/headers';
import {getChatGPTUser,chatGPTSignInPath,chatGPTSignOutPath} from './chatgpt-auth';
import type {ChatGPTUser} from './chatgpt-auth';
import {loadLocalConfig,validLocalOrigin} from './lib/local-auth';
import {safeLocalReturn} from './lib/local-auth-http';
export function getAuthMode():'sites'|'local'{
 const mode=process.env.AGENT_AUTH_MODE||'sites';if(mode!=='sites'&&mode!=='local')throw new Error('auth_configuration');return mode;
}
export async function getAppUser():Promise<ChatGPTUser|null>{
 if(getAuthMode()==='sites')return getChatGPTUser();
 const config=loadLocalConfig(),origin=process.env.AGENT_LOCAL_ORIGIN||'',h=await headers();
 if(!validLocalOrigin(origin)||h.get('host')!==new URL(origin).host)return null;
 return {userId:config.owner_id,displayName:'Local',email:'',fullName:null};
}
export function appSignInPath(returnTo:string){return getAuthMode()==='sites'?chatGPTSignInPath(returnTo):`/local-login?return_to=${encodeURIComponent(safeLocalReturn(returnTo))}`;}
export function appSignOutPath(){return getAuthMode()==='sites'?chatGPTSignOutPath('/'):'/api/local-auth/logout';}
