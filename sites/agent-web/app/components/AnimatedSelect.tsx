"use client";
import {useEffect,useId,useRef,useState} from "react";
import {Check,ChevronDown} from "lucide-react";
import {selectTransition} from "../lib/select-state";
import type {SelectEvent} from "../lib/select-state";

export type SelectOption<T extends string>={value:T;label:string;group?:string;disabled?:boolean};
export default function AnimatedSelect<T extends string>({value,options,onChange,label,disabled=false,display}:{value:T;options:readonly SelectOption<T>[];onChange:(value:T)=>void;label:string;disabled?:boolean;display?:React.ReactNode}){
 const id=useId(),trigger=useRef<HTMLButtonElement>(null),menu=useRef<HTMLDivElement>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
 const [state,setState]=useState({open:false,active:value as string}),[closing,setClosing]=useState(false);
 function close(restore=true){setClosing(true);if(restore)trigger.current?.focus();if(timer.current)clearTimeout(timer.current);timer.current=setTimeout(()=>{menu.current?.hidePopover();setState(s=>({...s,open:false}));setClosing(false)},matchMedia("(prefers-reduced-motion: reduce)").matches?0:120)}
 function position(){const r=trigger.current?.getBoundingClientRect(),m=menu.current;if(!r||!m)return;const width=Math.min(Math.max(r.width,190),window.innerWidth-24);const height=Math.min(m.scrollHeight,280,window.innerHeight-24);m.style.width=width+"px";m.style.maxHeight=height+"px";m.style.left=Math.max(12,Math.min(r.left,window.innerWidth-width-12))+"px";m.style.top=(r.bottom+height+8<=window.innerHeight?r.bottom+6:Math.max(12,r.top-height-6))+"px";}
 useEffect(()=>{if(!state.open)return;const m=menu.current;m?.showPopover();position();m?.querySelector<HTMLElement>(`[data-option-index="${options.findIndex(o=>o.value===state.active)}"]`)?.focus();function outside(e:PointerEvent){if(!menu.current?.contains(e.target as Node)&&!trigger.current?.contains(e.target as Node))close(false)}document.addEventListener("pointerdown",outside);window.addEventListener("resize",position);return()=>{document.removeEventListener("pointerdown",outside);window.removeEventListener("resize",position);m?.hidePopover()}},[state.open]);
 useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current)},[]);
 useEffect(()=>{if(disabled&&state.open){menu.current?.hidePopover();setState(s=>({...s,open:false}));setClosing(false)}},[disabled]);
 function keydown(e:React.KeyboardEvent){const keys=["ArrowDown","ArrowUp","Home","End","Enter"," ","Escape"];if(!keys.includes(e.key)||e.key==="Escape"&&!state.open)return;e.preventDefault();e.stopPropagation();if(state.open&&(e.key==="Escape"||e.key==="Enter"||e.key===" ")){if(e.key!=="Escape"){const selected=options.find(o=>o.value===state.active&&!o.disabled);if(selected)onChange(selected.value)}close();return;}const next=selectTransition(state,e.key as SelectEvent,options);setState(next);requestAnimationFrame(()=>menu.current?.querySelector<HTMLElement>(`[data-option-index="${options.findIndex(o=>o.value===next.active)}"]`)?.focus())}
 return <div className="animated-select" onKeyDown={keydown}>
  <button ref={trigger} type="button" className="select-trigger" aria-label={label} aria-haspopup="listbox" aria-expanded={state.open&&!closing} aria-controls={id} disabled={disabled} onClick={()=>state.open?close():setState(selectTransition({open:false,active:value},"Enter",options))}><span>{display??options.find(o=>o.value===value)?.label??label}</span><ChevronDown size={14}/></button>
  <div ref={menu} id={id} popover="manual" role="listbox" aria-label={label} className={`select-options ${closing?"closing":""}`} onKeyDown={e=>{if(e.key==="Tab")close(false)}}>
   {options.map((option,index)=><div key={option.value}>{option.group&&option.group!==options[index-1]?.group&&<div className="select-group" role="presentation">{option.group}</div>}<button type="button" data-option-index={index} role="option" aria-selected={option.value===value} disabled={option.disabled} tabIndex={state.active===option.value?0:-1} onFocus={()=>setState(s=>({...s,active:option.value}))} onClick={()=>{onChange(option.value);close()}}>{option.label}{option.value===value&&<Check size={14}/>}</button></div>)}
  </div>
 </div>;
}
