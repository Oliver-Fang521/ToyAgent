"use client";
import {useEffect,useReducer,useRef,useState} from "react";
import {X} from "lucide-react";
import AnimatedSelect from "./AnimatedSelect";
import {useWorkspaceLocale} from "./WorkspaceLocale";
import {memoryApi,loadMemorySnapshot} from "../lib/memory";
import type {MemoryView} from "../lib/memory";
import type {ModelProvider} from "../lib/models";
import {memoryEditorTransition,memoryTypePayload,rebaseMemoryDraft,memoryMutationAction} from "../lib/memory-editor";

export default function MemoryEditor({record:initialRecord,onClose,onSaved,onSignInRequired,hasActiveRun,defaultProvider,suppressed}:{record:MemoryView|null;onClose:()=>void;onSaved:()=>void;onSignInRequired:()=>void;hasActiveRun:boolean;defaultProvider:ModelProvider;suppressed:readonly {category:string;facet:string}[]}){
 const [record,setRecord]=useState(initialRecord),[rebased,setRebased]=useState(false);
 const {t,locale}=useWorkspaceLocale(),dialog=useRef<HTMLDialogElement>(null),alive=useRef(true);
 const [draft,dispatch]=useReducer(memoryEditorTransition,{content:record?.content||"",conflict:false});
 const [type,setType]=useState(record?`${record.category}:${record.topic||record.facet}`:"profile:note"),[entity,setEntity]=useState(record?.entity||"");
 const [providers,setProviders]=useState<ModelProvider[]>(record?.allowed_providers||["openai","deepseek"]);
 const [expires,setExpires]=useState(record?.expires_at?new Date(record.expires_at*1000).toISOString().slice(0,16):""),[expiryPreset,setExpiryPreset]=useState(record?.expires_at?"custom":"never");
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 const [confirmStop,setConfirmStop]=useState(false);
 const origin=record?.origin_provider||defaultProvider,shared=providers.some(p=>p!==origin),category=type.slice(0,type.indexOf(":")),facet=type.slice(type.indexOf(":")+1);
 const restoring=!record&&suppressed.some(s=>s.category===category&&s.facet===facet);
 useEffect(()=>{alive.current=true;dialog.current?.showModal();return()=>{alive.current=false;dialog.current?.close()}},[]);
 function close(){dispatch({type:"reset"});onClose()}
 async function rebase(){if(!record||busy)return;setBusy(true);try{const snapshot=await loadMemorySnapshot();if(!alive.current)return;const latest=rebaseMemoryDraft(draft,snapshot.items,record.id);setRecord(latest.record);dispatch({type:"rebase"});setError("");setRebased(true)}catch(e){if(alive.current)setError((e as Error).message==="memory_not_found"?"这条记忆已不存在，请刷新列表。":(e as Error).message)}finally{if(alive.current)setBusy(false)}}
 async function save(e:React.SyntheticEvent,confirmed=false){e.preventDefault();if(busy)return;if(memoryMutationAction(hasActiveRun,confirmed)==="confirm"){setConfirmStop(true);return}
  setError("");setBusy(true);try{const payload=memoryTypePayload(type,entity);const expires_at=expires?Date.parse(expires+"Z")/1000:null;
   await memoryApi(record?`memories/${record.id}/update`:"memories",record?{revision:record.revision,content:draft.content,allowed_providers:providers,expires_at}:{...payload,content:draft.content,allowed_providers:providers});
   if(alive.current)onSaved();
  }catch(e){if(!alive.current)return;const failure=e as {status?:number;message?:string};if(failure.status===401){onSignInRequired();return}dispatch({type:"failure",status:failure.status});setError(failure.message||"记忆操作未确认，请重试。")}finally{if(alive.current)setBusy(false)}
 }
 return <dialog ref={dialog} className="key-settings memory-editor" aria-labelledby="memory-editor-title" onCancel={e=>{e.preventDefault();close()}}>
 <div className="key-settings-heading"><h2 id="memory-editor-title">{t(record?"编辑记忆":"新增记忆")}</h2><button type="button" onClick={close} aria-label={t("关闭")}><X size={18}/></button></div>
 <form onSubmit={event=>void save(event)} autoComplete="off">
  <label>{t("主题")}<input required maxLength={64} value={facet} disabled={busy||!!record} onChange={e=>setType(`${category}:${e.target.value}`)}/></label>
  <label>{t("记忆内容")}<textarea autoFocus required maxLength={500} value={draft.content} onChange={e=>dispatch({type:"content",content:e.target.value})} disabled={busy}/></label>
  {restoring&&<p className="memory-inline-notice">{t("保存后恢复此字段采集")}</p>}
  {error&&<p role="alert" className="key-error">{t(error)}</p>}
  {confirmStop&&<section className="memory-confirm" role="alert"><p>{t("此操作会停止当前任务。继续？")}</p><button type="button" disabled={busy||!draft.content.trim()} onClick={event=>void save(event,true)}>{t("确认并保存")}</button><button type="button" onClick={()=>setConfirmStop(false)}>{t("取消")}</button></section>}
  {draft.conflict&&<button type="button" disabled={busy} onClick={()=>void rebase()}>{t("载入最新版本 · 保留草稿")}</button>}
  {rebased&&record&&<div className="memory-inline-notice"><p>{t("最新版本如下，请核对后保存你的草稿。")}</p><p className="memory-content">{record.content}</p></div>}
  <details><summary>{t("高级设置")}</summary><div>
   <div><label>{t("分类")}</label><AnimatedSelect value={category} options={[{value:"profile",label:t("背景与偏好")},{value:"project",label:t("项目与目标")},{value:"decision",label:t("经历与决定")}]} onChange={value=>setType(`${value}:${facet}`)} label={t("分类")} disabled={busy||!!record}/></div>
   {category!=="profile"&&<label>{t("项目或决定名称")}<input maxLength={128} value={entity} disabled={busy||!!record} onChange={e=>setEntity(e.target.value)}/></label>}
   <div><label>{t("可用服务商")}</label><AnimatedSelect value={providers.length===2?"both":providers[0]} options={[{value:"openai",label:"OpenAI"},{value:"deepseek",label:"DeepSeek"},{value:"both",label:"OpenAI + DeepSeek"}]} onChange={value=>setProviders(value==="both"?["openai","deepseek"]:[value as ModelProvider])} label={t("可用服务商")} disabled={busy}/></div>
   {shared&&<p className="memory-inline-notice">{t("保存后，此内容也可发送给所选的其他服务商。")}</p>}
   {record&&<><div><label>{t("有效期")}</label><AnimatedSelect value={expiryPreset} options={[{value:"never",label:t("长期")},{value:"7",label:t("7 天")},{value:"30",label:t("30 天")},{value:"custom",label:t("自定义")}]} onChange={value=>{setExpiryPreset(value);setExpires(value==="never"?"":value==="custom"?expires:new Date(Date.now()+Number(value)*86400000).toISOString().slice(0,16))}} label={t("有效期")} disabled={busy}/></div>{expiryPreset==="custom"&&<label>{t("有效期（UTC，留空表示长期）")}<input type="datetime-local" value={expires} disabled={busy} onChange={e=>setExpires(e.target.value)}/></label>}
   <div className="memory-meta">{t("更新")} {new Date(record.updated_at*1000).toLocaleString(locale==="zh"?"zh-CN":"en-US")} · v{record.revision}<br/>{record.origin_provider}{record.edited_by_user?` · ${t("人工编辑优先")}`:""}</div></>}
  </div></details>
  <div className="memory-editor-actions"><button type="button" onClick={close}>{t("取消")}</button><button type="submit" className="memory-primary" disabled={busy||!draft.content.trim()||draft.conflict}>{t(busy?"处理中…":"保存")}</button></div>
 </form></dialog>;
}
