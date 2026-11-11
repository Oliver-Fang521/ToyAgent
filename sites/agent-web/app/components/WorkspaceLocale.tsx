"use client";
import {createContext,useContext,useEffect,useState} from "react";
import {parseLocale,translateUI} from "../lib/ui-language";
import type {Locale} from "../lib/ui-language";
const LocaleContext=createContext({locale:"zh" as Locale,setLocale:(_locale:Locale)=>{},t:(value:string)=>value});
export function WorkspaceLocale({children}:{children:React.ReactNode}){
 const [locale,update]=useState<Locale>("zh");
 useEffect(()=>{try{update(parseLocale(localStorage.getItem("toyagent-ui-language")))}catch{}
  function sync(e:StorageEvent){if(e.key==="toyagent-ui-language")update(parseLocale(e.newValue));}window.addEventListener("storage",sync);return()=>window.removeEventListener("storage",sync);},[]);
 useEffect(()=>{document.documentElement.lang=locale==="zh"?"zh-CN":"en";},[locale]);
 function setLocale(next:Locale){update(next);try{localStorage.setItem("toyagent-ui-language",next)}catch{}}
 return <LocaleContext.Provider value={{locale,setLocale,t:value=>translateUI(value,locale)}}>{children}</LocaleContext.Provider>;
}
export function useWorkspaceLocale(){return useContext(LocaleContext);}
