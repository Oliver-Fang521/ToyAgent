"use client";
import {useEffect,useId,useRef,useState} from "react";
import {Check,ChevronDown,Search,X} from "lucide-react";
import type {ModelCatalog,ModelProvider,Selection} from "../lib/models";
import {modelSelection} from "../lib/models";
import {filterModels} from "../lib/model-picker";
import AnimatedSelect from "./AnimatedSelect";
import {useWorkspaceLocale} from "./WorkspaceLocale";

export default function ModelSelector({catalog,selection,disabled,onChange}:{catalog:ModelCatalog|null;selection:Selection;disabled:boolean;onChange:(selection:Selection)=>void;onRefresh:(provider:ModelProvider)=>Promise<void>}){
 const {t,locale}=useWorkspaceLocale(),id=useId(),dialog=useRef<HTMLDialogElement>(null),trigger=useRef<HTMLButtonElement>(null),search=useRef<HTMLInputElement>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const [open,setOpen]=useState(false),[closing,setClosing]=useState(false),[query,setQuery]=useState(""),[provider,setProvider]=useState("all");
 const profile=catalog?.models.find(m=>m.provider===selection.provider&&m.model===selection.model),effort=selection.reasoning_effort??profile?.default_reasoning_effort;
 const models=filterModels(catalog?.models||[],provider,query,false);
 function position(){const d=dialog.current,r=trigger.current?.getBoundingClientRect();if(!d||!r)return;const width=Math.min(390,window.innerWidth-24),height=Math.min(d.scrollHeight,window.innerHeight-24);d.style.width=width+"px";d.style.left=Math.max(12,Math.min(r.right-width,window.innerWidth-width-12))+"px";d.style.top=Math.max(12,Math.min(r.top-height-8,window.innerHeight-height-12))+"px";}
 function close(){if(closing)return;setClosing(true);timer.current=setTimeout(()=>{setOpen(false);setClosing(false);trigger.current?.focus()},matchMedia("(prefers-reduced-motion: reduce)").matches?0:120)}
 useEffect(()=>{if(!open)return;dialog.current?.showModal();position();search.current?.focus();window.addEventListener("resize",position);return()=>{window.removeEventListener("resize",position);dialog.current?.close()}},[open]);
 useEffect(()=>{if(open)position()},[query,provider,profile,open,locale]);
 useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current)},[]);
 useEffect(()=>{if(disabled){if(timer.current)clearTimeout(timer.current);setOpen(false);setClosing(false)}},[disabled]);
 return <div className="model-selector"><button ref={trigger} type="button" className="model-trigger" disabled={disabled||!catalog} aria-haspopup="dialog" aria-expanded={open} onClick={()=>setOpen(true)} aria-label={`${t("选择模型")}: ${profile?.display_name||selection.model}`}><span>{profile?.display_name||selection.model}</span>{effort&&<small>{effort==="none"?t("关闭"):effort}</small>}<ChevronDown size={13}/></button>
 <dialog ref={dialog} className={`model-picker ${closing?"closing":""}`} aria-labelledby={`${id}-title`} onCancel={e=>{e.preventDefault();close()}} onClick={e=>{if(e.target!==e.currentTarget)return;const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close()}}>
  <div className="picker-heading"><h2 id={`${id}-title`}>{t("选择模型")}</h2><button type="button" onClick={close} aria-label={t("关闭")}><X size={15}/></button></div>
  <label className="model-search-wrap"><Search size={15}/><input ref={search} type="search" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")e.preventDefault();if(e.key==="ArrowDown"){e.preventDefault();dialog.current?.querySelector<HTMLButtonElement>(".model-option:not(:disabled)")?.focus()}}} aria-label={t("搜索模型或服务商…")} placeholder={t("搜索模型或服务商…")}/></label>
  <div className="provider-tabs" role="tablist" aria-label={t("模型服务商")}>{["all","openai","deepseek"].map((value,index)=><button type="button" key={value} role="tab" id={`${id}-${value}`} aria-selected={provider===value} aria-controls={`${id}-results`} tabIndex={provider===value?0:-1} onClick={()=>setProvider(value)} onKeyDown={e=>{if(e.key==="ArrowLeft"||e.key==="ArrowRight"){e.preventDefault();const next=(index+(e.key==="ArrowRight"?1:2))%3;setProvider(["all","openai","deepseek"][next]);e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus()}}}>{value==="all"?t("全部"):value==="openai"?"OpenAI":"DeepSeek"}</button>)}</div>
  <div className="model-results" id={`${id}-results`} role="tabpanel" aria-labelledby={`${id}-${provider}`} onKeyDown={e=>{if(e.key!=="ArrowDown"&&e.key!=="ArrowUp")return;const buttons=Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>(".model-option:not(:disabled)")),index=buttons.indexOf(document.activeElement as HTMLButtonElement);e.preventDefault();buttons[(index+(e.key==="ArrowDown"?1:buttons.length-1))%buttons.length]?.focus()}}>
   {!models.length&&<p className="picker-empty">{t("没有匹配的模型")}</p>}{Array.from(new Set(models.map(m=>`${m.provider}/${m.family}`))).map(group=><section key={group}><h3 className="model-family">{group.replace("openai/","OpenAI · ").replace("deepseek/","")}</h3>{models.filter(m=>`${m.provider}/${m.family}`===group).map(m=><button className="model-option" type="button" key={`${m.provider}/${m.model}`} aria-pressed={selection.provider===m.provider&&selection.model===m.model} disabled={m.adapter_status==="unsupported"||m.availability==="unavailable"} onClick={()=>onChange(modelSelection(m))}><span className={`provider-avatar ${m.provider==="deepseek"?"ds":""}`} aria-hidden="true">{m.provider==="openai"?"O":"D"}</span><span><strong>{m.display_name}</strong><small>{m.disabled_reason?t(m.disabled_reason):m.availability==="unavailable"?t("账户未显示此模型"):m.model}</small></span>{selection.provider===m.provider&&selection.model===m.model&&<Check size={15} className="model-check"/>}</button>)}</section>)}
  </div>
  {!!profile?.allowed_reasoning_efforts?.length&&<div className="reasoning-section"><span>{t("推理配置")}</span><AnimatedSelect value={effort||profile.default_reasoning_effort||""} options={profile.allowed_reasoning_efforts.map(value=>({value,label:value==="none"?t("关闭"):value}))} onChange={value=>onChange(modelSelection(profile,value))} label={t("推理配置")}/></div>}
  <footer className="picker-footer"><span>{models.length} {locale==="en"?"models":"个模型"}</span><button type="button" onClick={close}>{t("完成")}</button></footer>
 </dialog></div>;
}
