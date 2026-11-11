"use client";
import {useEffect,useRef,useState} from "react";
import {X} from "lucide-react";
import {useWorkspaceLocale} from "./WorkspaceLocale";
import {credentialApi,deleteCredential} from "../lib/credentials";
import type {CredentialState,Provider} from "../lib/credentials";
import type {ModelProvider} from "../lib/models";
import {recoveryAction} from "../lib/onboarding";

export default function ApiKeySettings({open,provider,onClose,onSignInRequired,onCredentialChange}:{open:boolean;provider:ModelProvider;onClose:()=>void;onSignInRequired:()=>void;onCredentialChange:(provider:ModelProvider,handle:string|null)=>void}){
 const {t}=useWorkspaceLocale(),dialog=useRef<HTMLDialogElement>(null);
 const [state,setState]=useState<CredentialState|null>(null),[editing,setEditing]=useState<Provider|null>(null),[key,setKey]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[migrationConsent,setMigrationConsent]=useState(false);
 async function refresh(){try{const data=await credentialApi("credentials") as CredentialState;if(!Array.isArray(data?.statuses))throw Error();setState(data)}catch(e){if(recoveryAction(e as {status?:number;code?:string})==="sign_in")onSignInRequired();else setError("密钥服务暂不可用，请稍后重试。")}}
 function close(){setKey("");setEditing(null);onClose()}
 useEffect(()=>{if(!open)return;setEditing(null);setKey("");setError("");setNotice("");setState(null);dialog.current?.showModal();void refresh();return()=>{dialog.current?.close()}},[open]);
 async function save(event:React.FormEvent){event.preventDefault();if(!editing||!key.trim()||busy)return;setBusy(true);setError("");try{await credentialApi("credentials",{provider:editing,key,consent:true});onCredentialChange(editing==="openai"||editing==="deepseek"?editing:provider,null);setKey("");setEditing(null);setNotice("已加密保存");await refresh()}catch{setError("保存未能确认，请刷新状态后重试。不会使用站长的 Key。")}finally{setBusy(false)}}
 async function remove(slot:Provider){setBusy(true);setError("");try{await deleteCredential(slot,credentialApi,()=>onCredentialChange(slot==="openai"||slot==="deepseek"?slot:provider,null));setEditing(null);setKey("");await refresh()}catch{setError("删除未能确认。需要立即撤销时，请在服务商处撤销 Key。")}finally{setBusy(false)}}
 async function migrate(){if(!migrationConsent||busy)return;setBusy(true);setError("");try{await credentialApi("credentials/migrate-model",{provider:"openai",consent:true});onCredentialChange("openai",null);setMigrationConsent(false);await refresh()}catch{setError("迁移失败或存在冲突，原配置已保留。请检查状态。")}finally{setBusy(false)}}
 if(!open)return null;
 const groups:[string,Provider[]][]=[["模型服务商",["openai","deepseek"]],["搜索服务",["serper","serpapi"]]];
 return <dialog ref={dialog} className="key-settings" aria-labelledby="key-settings-title" onCancel={e=>{e.preventDefault();close()}}>
  <div className="key-settings-heading"><h2 id="key-settings-title">{t("服务配置")}</h2><button type="button" onClick={close} aria-label={t("关闭密钥设置")}><X size={18}/></button></div>
  {groups.map(([label,slots])=><section key={label}><h3>{t(label)}</h3><div className="key-saved">{label==="搜索服务"&&(()=>{const item=state?.statuses.find(s=>s.provider==="deepseek");return <div><span>DeepSeek Search<small>{!state?t("正在读取…"):item?.configured?`•••• ${item.last_four}`:t("未配置")}</small></span><button type="button" disabled={busy||!state} onClick={()=>{setEditing("deepseek");setKey("");setNotice("");setError("")}}>{t(item?.configured?"修改":"配置")}</button></div>})()}{slots.map(slot=>{const item=state?.statuses.find(s=>s.provider===slot);return <div key={slot}><span>{slot==="openai"?"OpenAI":slot==="deepseek"?"DeepSeek":slot==="serper"?"Serper":"SerpAPI"}<small>{!state?t("正在读取…"):item?.configured?`•••• ${item.last_four}`:t("未配置")}</small></span><span><button type="button" disabled={busy||!state} onClick={()=>{setEditing(slot);setKey("");setNotice("");setError("")}}>{t(item?.configured?"修改":"配置")}</button>{item?.configured&&<button type="button" disabled={busy} onClick={()=>void remove(slot)}>{t("删除")}</button>}</span></div>})}</div></section>)}
  {editing&&<form onSubmit={save} autoComplete="off"><label>{editing} API Key<input type="password" value={key} onChange={e=>setKey(e.target.value)} maxLength={4096} required autoComplete="off" disabled={busy}/></label><button type="submit" className="key-save" disabled={busy||!key.trim()}>{t(busy?"处理中…":"保存")}</button><button type="button" onClick={()=>{setEditing(null);setKey("")}}>{t("取消")}</button></form>}
  {state?.legacy_available&&<section className="legacy-migration"><p>{t("旧模型 Key 不会自动归属。")}{state.legacy_migration_allowed?t("确认该 Key 来自 OpenAI 后才能迁移；不会发出模型请求。"):t("旧服务域名不明确，请重新填写正确服务商的 Key。")}</p>{state.legacy_migration_allowed&&<><label><input type="checkbox" checked={migrationConsent} onChange={e=>setMigrationConsent(e.target.checked)}/>{t("我确认旧 Key 属于 OpenAI，并同意迁移")}</label><button type="button" disabled={!migrationConsent||busy} onClick={()=>void migrate()}>{t("确认迁移旧 Key")}</button></>}</section>}
  {error&&<p role="alert" className="key-error">{t(error)}</p>}{notice&&<p role="status" className="key-notice">{t(notice)}</p>}
 </dialog>
}
