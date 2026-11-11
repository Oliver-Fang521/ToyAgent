export type ModelProvider = "openai"|"deepseek";
export type Selection = {provider:ModelProvider;model:string;reasoning_effort?:string};
export type ModelProfile = Selection & {display_name:string;endpoint:string;default_reasoning_effort:string|null;allowed_reasoning_efforts:string[];family:string;legacy:boolean;adapter_status:"supported"|"unsupported";disabled_reason:string|null;availability?:"visible"|"unavailable"|"unknown";evidence_url:string;capability_version:string;verification:string;capabilities:{image:boolean;audio:boolean;hosted_search:boolean;hosted_python:boolean}};
export type ModelCatalog = {providers:{id:ModelProvider;name:string;hostname:string}[];models:ModelProfile[];discovery?:Partial<Record<ModelProvider,{status:string;expires_at?:number|null}>>};
export type TemporaryHandle = {provider:ModelProvider;handle:string;expiresAt:number};
export const initialSelection:Selection={provider:"openai",model:"gpt-6.1-terra",reasoning_effort:"medium"};
export function requiresNewConversation(provider:string|null,selection:Selection){return provider!==selection.provider;}
export function matchingHandle(value:TemporaryHandle|null,selection:Selection,now=Date.now()){
 return value?.provider===selection.provider&&value.expiresAt>now?value.handle:null;
}
export function modelSelection(profile:Pick<ModelProfile,"provider"|"model"|"allowed_reasoning_efforts"|"default_reasoning_effort">,effort=profile.default_reasoning_effort):Selection{
 if(effort!==null&&!profile.allowed_reasoning_efforts.includes(effort))throw new Error("reasoning_unavailable");
 return {provider:profile.provider,model:profile.model,...(effort!==null?{reasoning_effort:effort}:{})};
}
