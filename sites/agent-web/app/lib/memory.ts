import type { ModelProvider } from "./models.ts";
export type MemoryMode="normal"|"temporary";
export type MemorySettingsState={enabled:boolean;revision:number;generation:number;capture_after:number;suppressed_facets?:{category:"profile"|"project"|"decision";facet:string}[]};
export type MemoryReport={status:"ready"|"pending"|"updated"|"unchanged"|"paused"|"temporary"|"unavailable"|"skipped";used_ids:string[];saved_ids:string[];skipped_count:number};
export type MemoryView={id:string;category:"profile"|"project"|"decision";facet:string;topic?:string;entity:string;content:string;revision:number;origin_provider:ModelProvider;allowed_providers:ModelProvider[];created_at:number;updated_at:number;last_confirmed_at:number;expires_at:number|null;edited_by_user:boolean};
export type MemoryJob={id:string;state:string;provider:ModelProvider;retry_count:number;failure_code:string|null;kind?:"summary"};
export type MemoryListSnapshot={settings:MemorySettingsState;items:MemoryView[];overviews?:Partial<Record<ModelProvider,string|null>>;suppressed_facets?:{category:string;facet:string}[];maintenance?:{pending:number;failed:number;uncertain:number;input_tokens:number;output_tokens:number;jobs:MemoryJob[]};barriers?:{id:string;revision:number;topic:string;entity:string;category:string}[]};
export function maintenanceLabel(state:string){return ({pending:"等待整理",leased:"正在整理",provider_started:"正在整理",proposed:"正在保存",completed:"已保存",unchanged:"无新增记忆",failed:"整理失败",uncertain:"调用结果待确认",blocked:"整理已停止",needs_clarification:"需要澄清"} as Record<string,string>)[state]||"整理已停止";}
export function canRetryMemory(job:{state:string;retry_count:number},confirmed:boolean){return confirmed&&job.retry_count===0&&(job.state==="failed"||job.state==="uncertain");}
export function maintenanceReason(code:string){
 if(code==="memory_limit")return "本次整理超出处理预算，请分批重试。";
 if(code==="memory_timeout")return "模型调用超时";
 if(code.startsWith("credential_")||code==="provider_authentication")return "请检查模型 Key";
 return "整理未完成，请检查设置或重试";
}
export function loadMemorySnapshot(){return memoryApi<MemoryListSnapshot>("memories/snapshot");}
export function createMemorySnapshotLoader(fetchSnapshot:()=>Promise<MemoryListSnapshot>=loadMemorySnapshot){
 let epoch=0;
 return {invalidate(){epoch++},async load(){const current=++epoch;try{const value=await fetchSnapshot();return current===epoch?value:null}catch(error){if(current!==epoch)return null;throw error}}};
}
const errors:Record<string,string>={memory_conflict:"内容已在别处修改。你的草稿已保留，请重新载入最新版本。",memory_invalid:"内容或字段不支持。请勿保存密钥、密码或敏感身份信息。",memory_limit:"本次整理超出处理预算，请分批重试。",memory_busy:"记忆正在更新，请稍后重试。",memory_not_found:"这条记忆已不存在，请刷新列表。",memory_unavailable:"记忆服务暂不可用，正常对话仍可继续。",sign_in_required:"请先登录以管理个人记忆。"};
export type MemoryPage={items:MemoryView[];total:number;next_cursor:string|null;coverage:string};
export function loadMemoryPage(query="",cursor:string|null=null,upstream:typeof fetch=fetch){const params=new URLSearchParams({q:query,limit:"50"});if(cursor)params.set("cursor",cursor);return memoryApi<MemoryPage>(`memories/search?${params}`,undefined,upstream);}
export function createMemoryPageLoader(fetchPage:(query:string,cursor:string|null)=>Promise<MemoryPage>=loadMemoryPage){let epoch=0;return {invalidate(){epoch++},async load(query:string,cursor:string|null=null){const current=++epoch;try{const page=await fetchPage(query,cursor);return current===epoch?page:null}catch(error){if(current!==epoch)return null;throw error}}};}
export async function memoryApi<T=unknown>(path:string,body?:unknown,upstream:typeof fetch=fetch):Promise<T>{
 const response=await upstream(`/api/agent/${path}`,{method:body===undefined?"GET":"POST",cache:"no-store",...(body===undefined?{}:{headers:{"content-type":"application/json"},body:JSON.stringify(body)})});
 const data=await response.json().catch(()=>({}));
 const code=typeof data==="object"&&data!==null&&"error" in data&&typeof data.error==="string"?data.error:"memory_unavailable";
 if(!response.ok)throw Object.assign(new Error(errors[code]||"记忆操作未能确认，请刷新状态后重试。"),{status:response.status,code});
 return data as T;
}
export function memoryLabel(settings:MemorySettingsState|null,mode:MemoryMode){return mode==="temporary"?"临时对话":settings===null?"长期记忆 · 正在读取状态":settings.enabled?"长期记忆已开启":"长期记忆已暂停";}
export function memoryReportLabel(report:MemoryReport){
 if(report.status==="temporary")return "临时模式 · 未读取或保存长期记忆";
 if(report.status==="paused")return "长期记忆已暂停";
 if(report.status==="unavailable")return "记忆整理暂不可用 · 本次未保存";
 return `使用 ${report.used_ids.length} 项记忆 · ${report.saved_ids.length?`记住了 ${report.saved_ids.length} 项`:"本次未更新记忆"}${report.skipped_count?` · 跳过 ${report.skipped_count} 项`:""}`;
}
export function editorFailure(error:unknown,content:string){const failure=error as {status?:number;message?:string};return {content,reload:failure.status===409,message:failure.message||"记忆操作未确认，请重试。"};}
type Attempt={question:string;file:unknown;selection:string;memoryMode:MemoryMode};
export function sameAttempt(first:Attempt|null,second:Attempt){return !!first&&first.question===second.question&&first.file===second.file&&first.selection===second.selection&&first.memoryMode===second.memoryMode;}
