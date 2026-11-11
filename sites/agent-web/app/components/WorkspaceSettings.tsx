"use client";
import {useEffect,useRef} from "react";
import {X} from "lucide-react";
import LanguageMenu from "./LanguageMenu";
import {useWorkspaceLocale} from "./WorkspaceLocale";
export default function WorkspaceSettings({open,onClose,onKeys,onMemory,signedIn}:{open:boolean;onClose:()=>void;onKeys:()=>void;onMemory:()=>void;signedIn:boolean}){
 const dialog=useRef<HTMLDialogElement>(null);const {t}=useWorkspaceLocale();
 useEffect(()=>{if(open)dialog.current?.showModal();else dialog.current?.close();return()=>dialog.current?.close()},[open]);
 return <dialog ref={dialog} className="workspace-settings key-settings" aria-labelledby="workspace-settings-title" onCancel={e=>{e.preventDefault();onClose()}}><div className="key-settings-heading"><h2 id="workspace-settings-title">{t("设置")}</h2><button type="button" onClick={onClose} aria-label={t("关闭")}><X size={18}/></button></div>
 <div className="workspace-setting-row"><strong>{t("界面语言")}</strong><LanguageMenu/></div>
 <div className="workspace-setting-row"><strong>{t("模型服务商")}</strong><button onClick={()=>{onClose();onKeys()}}>{t(signedIn?"配置":"登录")}</button></div>
 <div className="workspace-setting-row"><strong>{t("我的记忆")}</strong><button onClick={()=>{onClose();onMemory()}}>{t(signedIn?"管理":"登录")}</button></div></dialog>;
}
