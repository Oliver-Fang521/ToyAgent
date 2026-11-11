"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, ArrowUpRight, BookOpen, Check, FileText,
  GitBranch, LoaderCircle, Menu, Paperclip, Plus, Search, Square, X } from "lucide-react";
import { safeSourceUrl } from "../lib/proxy";
import { completedMessage, loadConversationMessages, reopenedRun } from "../lib/history";
import ApiKeySettings from "./ApiKeySettings";
import { claimNewDraft, emptyDraft, writeDraft, type ConversationDrafts } from "../lib/conversation-drafts";
import {nearBottom} from "../lib/stream-display";
import {useStreamDisplay} from "../lib/use-stream-display";
import AnswerContent from "./AnswerContent";
import {recordChatSubmit} from "../lib/chat-timing";
import {applyRunEvent,runEventNames,partialAnswerLabel} from "../lib/run-events";
import MemorySettings from "./MemorySettings";
import { memoryApi, sameAttempt } from "../lib/memory";
import type { MemoryMode, MemorySettingsState, MemoryReport } from "../lib/memory";
import ModelSelector from "./ModelSelector";
import WorkspaceSettings from "./WorkspaceSettings";
import {useWorkspaceLocale} from "./WorkspaceLocale";
import { initialSelection, matchingHandle, requiresNewConversation, modelSelection } from "../lib/models";
import type { ModelCatalog, ModelProvider, Selection, TemporaryHandle } from "../lib/models";
import { nextGate, saveDraft, consumeDraft, clearDraft, recoveryAction } from "../lib/onboarding";
import {browserAttachmentStore,saveSigninAttachment,takeSigninAttachment} from "../lib/signin-attachment";
import { credentialApi } from "../lib/credentials";
import type { CredentialState } from "../lib/credentials";
import { currentStage, failureView, phaseLabels, completionView, stageVisited } from "../lib/failures";
import type { RunFailure, Phase } from "../lib/failures";

type Evidence = { evidence_id: string; kind: string; summary: string; source_url?: string };
type Message = { role: "user" | "assistant"; content: string; evidence?: Evidence[];provider?:string|null;model?:string|null;reasoning_effort?:string|null;capability_version?:string|null;memory?:MemoryReport|null };
type Conversation = { id: string; created: number;provider:ModelProvider|null;title?:string };
type Run = { id: string; conversation: string; status: string; question: string; error?: string;provider:ModelProvider|null;model:string|null;
  partial_answer?:string;
  reasoning_effort?:string|null;capability_version?:string|null;
  failure?:RunFailure|null;
  progress: { event: string; time: number;tool?:string;provider?:string;detail?:string }[]; result?: { answer: string; evidence: Evidence[];memory?:MemoryReport|null;tool_usage?:{search_requests:number;fetch_requests:number} } };
const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
const stages = Object.entries(phaseLabels);
const prompts = [
  { icon: Search, label: "深入研究", text: "What are the main differences between sodium-ion and lithium-ion batteries?",zh:"钠离子电池和锂离子电池的主要区别是什么？请提供可靠来源。" },
  { icon: FileText, label: "分析文件", text: "Help me analyze the file I attach and identify the most important findings.",zh:"帮我分析上传的文件，梳理最重要的发现。" },
  { icon: GitBranch, label: "解释代码", text: "Explain the code I provide, starting with the intuition and then its implementation.",zh:"请解释我提供的代码，先讲思路，再讲具体实现。" },
];
const errors: Record<string,string> = {
  active_run_limit:"You already have an active request. Finish or cancel it first.",
  service_busy:"The research queue is full. Please try again shortly.",
  service_unreachable:"The research service is temporarily unreachable. Please try again.",
  service_not_configured:"The research service is not configured yet.",
  unsupported_file:"This file type isn’t supported. Try a PDF, text, CSV, XLSX, image or audio file.",
  file_too_large:"Files must be smaller than 32 MB.",
  timeout:"This research reached its time limit. Try a more focused question.",
  critic_rejection:"The agent couldn’t verify an answer with enough evidence. Try refining your question.",
  sign_in_required:"Please sign in to start a research request.",
};

export default function AgentChat({ signedIn, ownerId, displayName, signInPath, signOutPath, localAuth=false }:
  { signedIn:boolean; ownerId:string|null; displayName:string; signInPath:string; signOutPath:string;localAuth?:boolean }) {
  const {locale,setLocale,t}=useWorkspaceLocale();
  const [workspaceSettingsOpen,setWorkspaceSettingsOpen]=useState(false);
  const [sidebarCollapsed,setSidebarCollapsed]=useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [cid, setCid] = useState<string|null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [drafts, setDrafts] = useState<ConversationDrafts>(() => new Map());
  const { question, file } = drafts.get(cid) ?? emptyDraft;
  function setQuestion(question: string) {
    setDrafts(previous => writeDraft(previous, cid, {...(previous.get(cid) ?? emptyDraft), question}));
  }
  function setFile(file: File | null) {
    setDrafts(previous => writeDraft(previous, cid, {...(previous.get(cid) ?? emptyDraft), file}));
  }
  const [run, setRun] = useState<Run|null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [temporary,setTemporary] = useState<TemporaryHandle|null>(null);
  const [credentialState,setCredentialState] = useState<CredentialState|null>(null);
  const [selection,setSelection] = useState<Selection>(initialSelection);
  const [catalog,setCatalog] = useState<ModelCatalog|null>(null);
  const [conversationProvider,setConversationProvider] = useState<ModelProvider|null>(null);
  const [settingsOpen,setSettingsOpen] = useState(false);
  const [notice,setNotice] = useState("");
  const [memoryOpen,setMemoryOpen] = useState(false);
  const [memoryMode,setMemoryMode] = useState<MemoryMode>("normal");
  const [memorySettings,setMemorySettings] = useState<MemorySettingsState|null>(null);
  const [memoryUnavailable,setMemoryUnavailable] = useState(false);
  const ownerGeneration=useRef(0);
  const signingIn=useRef(false);
  const recoveredDraft=useRef<{owner:string|null;draft:ReturnType<typeof consumeDraft>;attachment?:Promise<File|null>}|null>(null);
  const catalogGeneration=useRef(0);
  const credentialHandle=matchingHandle(temporary,selection);
  const pollGeneration = useRef(0);
  const navigationGeneration = useRef(0);
  const conversation = useRef<HTMLElement>(null);
  const followOutput = useRef(true);
  const displayedAnswer = useStreamDisplay(run?.partial_answer || "", run?.id);
  const attempt = useRef<{key:string;question:string;file:File|null;attachmentId:string|null;selection:string;memoryMode:MemoryMode}|null>(null);
  const activeKey = `toy-agent-active:${ownerId||"anonymous"}`;
  const busy = pending || Boolean(run && !terminal.has(run.status));
  const failure = failureView(run);
  const completion = completionView(run?.progress||[]);
  const latestTool=run?.progress?.filter(event=>event.event.startsWith("tool_call_")).at(-1);
  const researchActivity=run?.progress?.filter(event=>event.event==="research_activity").at(-1);
  const phase = failure?.stage || currentStage(run?.progress||[]);
  const stage = stages.findIndex(([key])=>key===phase);

  async function api<T>(path:string, options:RequestInit = {}): Promise<T> {
    const response = await fetch(`/api/agent/${path}`, { ...options, cache:"no-store" });
    const body = await response.json().catch(() => ({}));
    const failure = body as {error?:string;detail?:string};
    if (!response.ok) throw Object.assign(new Error(errors[failure.error || failure.detail || ""] || `Request failed (${response.status}). Please try again.`),{code:failure.error||failure.detail,status:response.status});
    return body as T;
  }
  async function refreshConversations() {
    const current=ownerGeneration.current;
    try { const value=await api<Conversation[]>("conversations");if(current===ownerGeneration.current)setConversations(value); } catch(e) { if(current===ownerGeneration.current)setError((e as Error).message); }
  }
  async function refreshMemory(){const current=ownerGeneration.current;try{const value=await memoryApi<MemorySettingsState>("memory/settings");if(current===ownerGeneration.current){setMemorySettings(value);setMemoryUnavailable(false);}}catch{if(current===ownerGeneration.current){setMemorySettings(null);setMemoryUnavailable(true);}}}
  async function memoryChanged(){void refreshMemory();attempt.current=null;if(run&&!terminal.has(run.status)){try{const latest=await api<Run>(`runs/${run.id}`);setRun(latest);if(terminal.has(latest.status))localStorage.removeItem(activeKey);}catch{setNotice("记忆已更新，请刷新任务状态。不会自动重试。");}}}
  async function refreshCredentials(){try{const state=await credentialApi("credentials") as CredentialState;
    setCredentialState(state);}catch{setCredentialState(null);}}
  function clearDiscovered(){catalogGeneration.current++;setCatalog(current=>current?{...current,discovery:undefined,models:current.models.map(model=>({...model,availability:undefined}))}:null)}
  function credentialsChanged(provider:ModelProvider,handle:string|null){clearDiscovered();setTemporary(handle?{provider,handle,expiresAt:Date.now()+1800000}:null);void refreshCredentials();}
  async function refreshModelCatalog(provider:ModelProvider){
    if(!signedIn){void beginSignIn();return}
    const handle=matchingHandle(temporary,{provider,model:""});
    if(!handle&&!credentialState?.statuses.some(s=>s.provider===provider&&s.configured)){setSettingsOpen(true);return}
    const current=ownerGeneration.current,epoch=catalogGeneration.current;
    try{const result=await api<{models:ModelCatalog["models"];discovery:{status:string;expires_at?:number|null}}>("model-catalog/refresh",{method:"POST",headers:{"content-type":"application/json",...(handle?{"x-agent-credential-handle":handle}:{})},body:JSON.stringify({provider})});
      if(current===ownerGeneration.current&&epoch===catalogGeneration.current)setCatalog(c=>c?{...c,models:c.models.filter(m=>m.provider!==provider).concat(result.models),discovery:{...c.discovery,[provider]:result.discovery}}:c);
    }catch(e){if(current!==ownerGeneration.current||epoch!==catalogGeneration.current)return;const failure=e as {status?:number;code?:string};if(failure.status===401)void beginSignIn();else if(failure.code?.startsWith("credential_"))setSettingsOpen(true);setCatalog(c=>c?{...c,discovery:{...c.discovery,[provider]:{status:"unavailable"}}}:c)}
  }
  useEffect(()=>{void api<ModelCatalog>("model-catalog").then(setCatalog).catch(()=>setError("模型清单暂不可用，请稍后刷新。"));},[]);
  useEffect(()=>{clearDiscovered()},[signedIn,ownerId]);
  useEffect(()=>{ownerGeneration.current++;const generation=ownerGeneration.current;pollGeneration.current++;attempt.current=null;setDrafts(new Map());setMessages([]);setConversations([]);setCid(null);setRun(null);setMemoryOpen(false);setMemorySettings(null);setMemoryUnavailable(false);setMemoryMode("normal");setTemporary(null);setSettingsOpen(false);setCredentialState(null);setError("");setNotice("");if(signedIn){void refreshMemory();void refreshCredentials();try{if(!recoveredDraft.current||recoveredDraft.current.owner!==ownerId){const draft=consumeDraft(sessionStorage,Date.now(),ownerId||undefined);recoveredDraft.current={owner:ownerId,draft,attachment:draft?.attachmentToken?takeSigninAttachment(browserAttachmentStore(),draft.attachmentToken,ownerId||""):undefined};}const {draft,attachment}=recoveredDraft.current;if(draft){setDrafts(previous=>writeDraft(previous,null,{question:draft.question,file:null}));setSelection(draft.selection);setMemoryMode(draft.memoryMode||"normal");if(attachment)void attachment.then(restored=>{if(generation!==ownerGeneration.current)return;if(restored)setDrafts(previous=>{const current=previous.get(null);return current?.file?previous:writeDraft(previous,null,{...(current??emptyDraft),file:restored});});else setError(locale==="en"?"Unable to restore attachment. Please select it again.":"附件恢复失败，请重新选择。");}).catch(()=>{if(generation===ownerGeneration.current)setError(locale==="en"?"Unable to restore attachment. Please select it again.":"附件恢复失败，请重新选择。");});}}catch{setNotice("浏览器不允许恢复草稿，请重新粘贴问题。");}}else recoveredDraft.current=null;return()=>{ownerGeneration.current++;};},[signedIn,ownerId]);
  useEffect(()=>{if(!temporary)return;const timer=setTimeout(()=>{setTemporary(null);clearDiscovered()},Math.max(0,temporary.expiresAt-Date.now()));return()=>clearTimeout(timer);},[temporary]);
  async function history(id:string) {
    return loadConversationMessages<Message>(offset => api<{messages:Message[];next_offset:number|null}>(`conversations/${id}?offset=${offset}`));
  }
  useEffect(() => {
    if (!signedIn) return;
    const generation=ownerGeneration.current;
    const navigation=navigationGeneration.current;
    void refreshConversations();
    const identifier = localStorage.getItem(activeKey);
    if (identifier) void (async () => {
      try {
        const restored = await api<Run>(`runs/${identifier}`);
        const savedMessages = await history(restored.conversation);
        if(generation!==ownerGeneration.current || navigation!==navigationGeneration.current)return;
        setCid(restored.conversation);
        setConversationProvider(restored.provider);
        if(restored.provider&&restored.model)setSelection({provider:restored.provider,model:restored.model,...(restored.reasoning_effort?{reasoning_effort:restored.reasoning_effort}:{})});
        setMessages(restored.status==="completed" ? savedMessages :
          [...savedMessages, {role:"user",content:restored.question}]);
        setRun(restored);
        if (terminal.has(restored.status)) localStorage.removeItem(activeKey);
      } catch { localStorage.removeItem(activeKey); }
    })();
  }, [signedIn,ownerId]);
  useLayoutEffect(() => { followOutput.current = true; }, [cid]);
  useLayoutEffect(() => {
    const area=conversation.current;
    if(area && followOutput.current) area.scrollTop=area.scrollHeight;
  }, [cid, messages, run?.status, run?.progress?.length, displayedAnswer, error]);
  useEffect(() => {
    if (!run || terminal.has(run.status)) return;
    const generation = ++pollGeneration.current;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    let source:EventSource|null = null;
    let streamRun:Run=run;
    function receive(next:Run) {
      if (stopped || generation !== pollGeneration.current) return;
      setRun(next);
      if (next.status === "completed" && next.result) {
        stopped=true;source?.close();localStorage.removeItem(activeKey);
        setMessages(current => [...current, completedMessage(next)!]);
        void refreshConversations();
      } else if (terminal.has(next.status)) {
        stopped=true;source?.close();localStorage.removeItem(activeKey);
        if(next.status!=="cancelled")setError("");
      }
    }
    async function poll() {
      try {
        const next:Run = await api<Run>(`runs/${run!.id}`);
        if (stopped || generation !== pollGeneration.current) return;
        receive(next);
        if (!stopped) timer = setTimeout(poll, 1000);
      } catch(e) {
        if (!stopped) { setError((e as Error).message);if(recoveryAction(e as {status?:number;code?:string})==="sign_in"){void beginSignIn();return;}timer = setTimeout(poll, 5000); }
      }
    }
    if(typeof EventSource!=="undefined"){
      source=new EventSource(`/api/agent/runs/${run.id}/events`);
      for(const name of runEventNames)source.addEventListener(name,(event:MessageEvent)=>{try{streamRun=applyRunEvent(streamRun,name,JSON.parse(event.data));receive(streamRun)}catch{source?.close();source=null;if(!stopped)timer=setTimeout(poll,0)}});
      source.onerror=()=>{source?.close();source=null;if(!stopped)timer=setTimeout(poll,0)};
    }else timer=setTimeout(poll,0);
    return () => { stopped = true; source?.close(); clearTimeout(timer); };
  }, [run?.id]);

  async function openConversation(id:string) {
    if (pending) return;
    const navigation=++navigationGeneration.current;
    attempt.current=null;
    setPending(true);
    pollGeneration.current++;
    setRun(null);
    setCid(id);
    setMessages([]);
    setError("");
    setMobileMenu(false);
    try {
      let details=await api<{latest_run?:Run|null;provider:ModelProvider|null}>(`conversations/${id}`);
      let data=await history(id);
      const updated=await api<{latest_run?:Run|null;provider:ModelProvider|null}>(`conversations/${id}`);
      if(details.latest_run?.id!==updated.latest_run?.id || details.latest_run?.status!==updated.latest_run?.status){
        details=updated;
        data=await history(id);
      }
      if(navigation!==navigationGeneration.current)return;
      const latest=details.latest_run;
      const active=reopenedRun(latest);
      if(latest&&!terminal.has(latest.status))localStorage.setItem(activeKey,latest.id);
      setConversationProvider(details.provider);attempt.current=null;
      if(details.provider){const profile=catalog?.models.find(m=>m.provider===details.provider&&m.model===latest?.model&&m.adapter_status==="supported")||catalog?.models.find(m=>m.provider===details.provider&&m.adapter_status==="supported");if(profile)setSelection(modelSelection(profile,latest?.model===profile.model&&latest.reasoning_effort?latest.reasoning_effort:profile.default_reasoning_effort));}
      else setNotice("这是旧版只读历史。发送新问题会创建新对话，不发送旧历史。");
      setMessages(active?[...data,{role:"user",content:active.question}]:data);
      setRun(active);
    } catch(e) { if(navigation===navigationGeneration.current)setError((e as Error).message); }
    finally { if(navigation===navigationGeneration.current)setPending(false); }
  }
  function newConversation() {
    if (pending) return;
    navigationGeneration.current++;pollGeneration.current++; attempt.current = null; setCid(null); setMessages([]); setRun(null); setError(""); setMobileMenu(false);
    setConversationProvider(null);
  }
  function chooseModel(next:Selection){if(busy)return;if(requiresNewConversation(conversationProvider,next)){pollGeneration.current++;setCid(null);setConversationProvider(null);setMessages([]);setRun(null);setNotice("");}attempt.current=null;setSelection(next);setError("");}
  async function beginSignIn(){
    if(signingIn.current)return;
    signingIn.current=true;
    try{
      let attachmentToken:string|undefined;
      if(file){attachmentToken=crypto.randomUUID();await saveSigninAttachment(browserAttachmentStore(),attachmentToken,file,ownerId);}
      try{if(question.trim()||attachmentToken)saveDraft(sessionStorage,{question,selection,ownerHint:ownerId,memoryMode,attachmentToken});else {try{clearDraft(sessionStorage);}catch{}}}
      catch(error){if(attachmentToken)await browserAttachmentStore().take(attachmentToken);throw error;}
      const link=document.createElement("a");link.href=signInPath;link.target="_top";link.click();
    }catch{signingIn.current=false;setError(locale==="en"?"Unable to save your draft. Please check browser storage and try again.":"无法保存草稿，请检查浏览器存储后重试。");}
  }
  async function submit() {
    if (busy || (!question.trim() && !file)) return;
    const gate=nextGate({signedIn,selection,credentialStatus:credentialHandle?[{provider:selection.provider,configured:true}]:(credentialState?.statuses||[]),busy});
    if(gate==="sign_in"){void beginSignIn();return;}
    const proposed={question:question.trim(),file,selection:JSON.stringify(selection),memoryMode};
    const replay=sameAttempt(attempt.current,proposed);
    if(gate==="key"&&!replay){setSettingsOpen(true);setNotice("请先配置当前服务商的 Key，问题会保留。保存后需再次点击发送。");return;}
    if(!catalog?.models.some(m=>m.provider===selection.provider&&m.model===selection.model)){setError("该模型已不可用，请重新选择。");return;}
    const submittedAt = performance.now();
    setPending(true); setError("");
    const text = question.trim() || (locale==="en"?"Please review the attachment, briefly describe it, and ask how you can help.":"请查看附件，简要介绍内容，并询问我需要什么帮助。");
    if (!sameAttempt(attempt.current,proposed))
      attempt.current = {key:crypto.randomUUID(), ...proposed, attachmentId:null};
    try {
      const identifier = (cid&&!requiresNewConversation(conversationProvider,selection)?cid:null) || (await api<{id:string}>("conversations",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({provider:selection.provider})})).id;
      if (cid === null) setDrafts(previous => claimNewDraft(previous, identifier));
      setCid(identifier);setConversationProvider(selection.provider);
      let attachmentId:null|string = attempt.current!.attachmentId;
      if (file && !attachmentId) {
        if (file.size > 32 * 1024 * 1024) throw new Error(errors.file_too_large);
        const form = new FormData(); form.append("file", file);
        attachmentId = (await api<{id:string}>(`conversations/${identifier}/uploads`,{method:"POST",body:form})).id;
        attempt.current!.attachmentId = attachmentId;
      }
      const next:Run = await api<Run>(`conversations/${identifier}/runs`, {method:"POST",
        headers:{"content-type":"application/json","idempotency-key":attempt.current!.key,...(credentialHandle?{"x-agent-credential-handle":credentialHandle}:{})},
        body:JSON.stringify({question:text, attachment_id:attachmentId,...selection,memory_mode:memoryMode})});
      recordChatSubmit(next.id, submittedAt);
      setMessages(current => [...current,{role:"user",content:text}]);
      if (next.status === "completed" && next.result) {
        setMessages(current => [...current,completedMessage(next)!]);
        localStorage.removeItem(activeKey);
      } else if (terminal.has(next.status)) {
        localStorage.removeItem(activeKey);
        if (next.status !== "cancelled") setError("");
      } else localStorage.setItem(activeKey, next.id);
      attempt.current = null; setRun(next); setDrafts(previous => writeDraft(previous, identifier, emptyDraft)); void refreshConversations();
    } catch(e) { const failure=e as Error&{code?:string;status?:number};setError(failure.message);if(failure.status===401){void beginSignIn();}else if(failure.code?.startsWith("credential_")||failure.code==="provider_authentication"){setTemporary(null);setSettingsOpen(true);}else if(failure.code==="conversation_provider_mismatch"){setCid(null);setConversationProvider(null);attempt.current=null;setNotice("当前历史属于另一服务商或旧版本。请再次发送以创建新对话。");} }
    finally { setPending(false); }
  }
  async function cancel() {
    if (!run) return;
    try { const cancelled = await api<Run>(`runs/${run.id}/cancel`, {method:"POST"}); pollGeneration.current++; localStorage.removeItem(activeKey); setRun(cancelled); }
    catch(e) { setError((e as Error).message); }
  }


  return <div className={`workspace ${sidebarCollapsed?"sidebar-collapsed":""} ${messages.length?"":"workspace-empty"}`}>
    {mobileMenu&&<button className="sidebar-scrim" aria-label={t("收起侧栏")} onClick={()=>setMobileMenu(false)}/>}
    <aside className={`sidebar ${mobileMenu ? "open" : ""}`}>
      <div className="brand-row"><a className="brand" href="/">ToyAgent</a><button className="sidebar-collapse" aria-label={t("收起侧栏")} onClick={()=>{setSidebarCollapsed(true);setMobileMenu(false)}}><Menu size={17}/></button></div>
      <button className="new-chat" onClick={newConversation} disabled={pending}><Plus size={17}/> {t("新建对话")} <span>↗</span></button>
      <button className="nav-current" onClick={()=>signedIn?setMemoryOpen(true):void beginSignIn()}><BookOpen size={17}/> {t("我的记忆")}</button>
      <div className="sidebar-caption history-caption">{t("最近对话")}</div>
      <nav className="history-list" aria-label={t("最近对话")}>
        {conversations.length ? conversations.map(item => <button key={item.id} className={cid===item.id?"selected":""}
          onClick={() => openConversation(item.id)} disabled={pending} title={item.title||t("新建对话")}><span className="conversation-title">{item.title||t("新建对话")}</span></button>)
          : null}
      </nav>
      <div className="sidebar-bottom"><button className="nav-current" onClick={()=>setWorkspaceSettingsOpen(true)}><Menu size={16}/>{t("设置")}</button>
        {!localAuth && (signedIn ? <div className="profile"><span className="avatar">{displayName[0]?.toUpperCase()}</span><span className="profile-name">{displayName}</span>{localAuth?<form method="post" action={signOutPath} onSubmit={()=>{try{clearDraft(sessionStorage);localStorage.removeItem(activeKey);}catch{}setTemporary(null);}}><button aria-label={t("退出登录")} type="submit">↗</button></form>:<a href={signOutPath} target="_top" onClick={()=>{try{clearDraft(sessionStorage);localStorage.removeItem(activeKey);}catch{}setTemporary(null);}} aria-label={t("退出登录")}>↗</a>}</div>
        : <button className="sidebar-signin" onClick={()=>void beginSignIn()}>{t("登录")}{!localAuth&&' · ChatGPT'} <ArrowUpRight size={15}/></button>)}</div>
    </aside>
    <main className="main-panel">
      <header className="topbar"><button className="mobile-toggle" onClick={() => {setSidebarCollapsed(false);setMobileMenu(!mobileMenu)}} aria-label={t("展开侧栏")}><Menu size={20}/></button>
        <span className="topbar-title">{t(cid?"对话":"新建对话")}</span>
        <div className="topbar-actions"><div className="language-segment" role="group" aria-label={t("界面语言")}><button aria-pressed={locale==="zh"} onClick={()=>setLocale("zh")}>中</button><button aria-pressed={locale==="en"} onClick={()=>setLocale("en")}>EN</button></div><button className="settings-trigger" onClick={()=>setWorkspaceSettingsOpen(true)}>{t("设置")}</button></div></header>
      <WorkspaceSettings open={workspaceSettingsOpen} signedIn={signedIn} onClose={()=>setWorkspaceSettingsOpen(false)} onKeys={()=>signedIn?setSettingsOpen(true):void beginSignIn()} onMemory={()=>signedIn?setMemoryOpen(true):void beginSignIn()}/>
      {signedIn&&<ApiKeySettings open={settingsOpen} provider={selection.provider} onClose={()=>setSettingsOpen(false)} onSignInRequired={()=>{setSettingsOpen(false);void beginSignIn();}} onCredentialChange={credentialsChanged}/>}
      {signedIn&&<MemorySettings key={ownerId} open={memoryOpen} provider={selection.provider} hasActiveRun={busy} onClose={()=>setMemoryOpen(false)} onChanged={()=>void memoryChanged()} onKeysRequired={()=>{setMemoryOpen(false);setSettingsOpen(true)}} onSignInRequired={()=>{setMemoryOpen(false);void beginSignIn();}}/>}
      <section ref={conversation} className={`conversation ${messages.length ? "has-messages" : ""}`}
        onWheel={event=>{if(event.deltaY<0)followOutput.current=false;}}
        onScroll={event=>{const area=event.currentTarget;followOutput.current=nearBottom(area.scrollTop,area.clientHeight,area.scrollHeight);}}>
        {!messages.length && <div className="welcome"><span className="greeting-mark" aria-hidden="true">t</span><h1>{t("今天，想一起探索什么？")}</h1></div>}
        <div className="messages">{messages.map((message,index) => <article key={index} className={`message ${message.role}`}>
          <div className="message-label">{message.role==="user"?(locale==="en"?"YOU":"你"):<><GitBranch size={15}/> ToyAgent <small>{message.model?`${message.provider} · ${message.model} · ${message.reasoning_effort??(message.capability_version?t("非推理模型"):t("档位未记录"))}`:locale==="en"?"Legacy · Model unknown":"旧记录 · 模型未知"}</small></>}</div>{message.role==="assistant"?<AnswerContent content={message.content} timingRunId={index===messages.length-1 && run?.status==="completed" ? run.id : undefined}/>:<p className="message-content">{message.content}</p>}
          {message.evidence?.length ? <div className="evidence-section"><div className="evidence-heading"><BookOpen size={14}/> {t("来源与证据")}</div>
            {message.evidence.map(evidence => <div className="source-card" key={evidence.evidence_id}><span className="source-number">{evidence.evidence_id.split("-").at(-1)}</span>
              <div><p>{evidence.summary}</p>{evidence.source_url && safeSourceUrl(evidence.source_url) ? <a href={safeSourceUrl(evidence.source_url)!} target="_blank" rel="noopener noreferrer">{new URL(evidence.source_url).hostname}<ArrowUpRight size={12}/></a> : <small>{evidence.kind} evidence</small>}</div></div>)}</div>:null}
        </article>)}</div>
        {run&&displayedAnswer&&!terminal.has(run.status)&&<article className="message assistant"><div className="message-label">ToyAgent · {t(partialAnswerLabel(run.progress))}</div><AnswerContent content={displayedAnswer} streaming timingRunId={run.id}/></article>}
        {run && <div className={`run-status ${failure?"run-failed":""}`} aria-live="polite"><div className="run-heading">{failure?<X size={15}/>:terminal.has(run.status)?<Check size={15}/>:<LoaderCircle size={15} className="spin"/>}
          {failure?`${t(failure.label)} · ${t("阶段失败")}`:run.status==="completed"?t(completion.label):t(run.status==="cancelled"?"请求已取消":run.status==="queued"?"正在等待任务名额":phaseLabels[phase])}
          {!terminal.has(run.status)&&<button onClick={cancel}><Square size={10}/> {t("停止")}</button>}</div>
          {completion.research&&terminal.has(run.status)&&<div className="stage-track">{stages.map(([key,label],index)=>{
            const visited=stageVisited(run.progress,key as Phase),skipped=run.status==="completed"&&!visited;
            return <span key={key} title={skipped?t("未执行"):undefined} className={failure&&index===stage?"failed-stage":visited?"reached":""}><i>{failure&&index===stage?<X size={10}/>:skipped?"—":visited&&(index<stage||run.status==="completed")?<Check size={10}/>:index+1}</i>{t(label)}</span>;
          })}</div>}
          {!terminal.has(run.status)&&latestTool?.event==="tool_call_started"&&latestTool.tool&&<p className="picker-status">{t("正在调用工具")} · {t(latestTool.tool==="web_search"?"网页搜索":latestTool.tool==="read_webpage"?"读取网页":latestTool.tool==="search_memory"?"搜索记忆":latestTool.tool==="read_memory"?"读取记忆":latestTool.tool)}{latestTool.provider?` · ${latestTool.provider==="deepseek"?"DeepSeek":latestTool.provider}`:""}</p>}
          {!terminal.has(run.status)&&latestTool?.event==="tool_call_started"&&researchActivity?.tool===latestTool.tool&&researchActivity?.detail&&<p className="picker-status">{latestTool.tool==="web_search"?(locale==="en"?"Searching: ":"正在搜索："):(locale==="en"?"Reading: ":"正在阅读：")}{researchActivity.detail}</p>}
          {failure&&<div className="failure-details" role="alert"><strong>{t(failure.label)} · {t("阶段失败")}：{locale==="en"&&failure.component==="tool"&&failure.tool&&failure.message.startsWith("工具 ")?(run?.failure?.tool_category==="repeated_call"?`Tool ${failure.tool} was called repeatedly; the task stopped.`:run?.failure?.tool_category==="tool_budget_exhausted"?`Tool ${failure.tool} reached its call limit.`:`Tool ${failure.tool} failed.`):t(failure.message)}</strong><p>{t(failure.suggestion)}</p>
            <p>{t("出错环节：")}{t(({model:"模型服务调用",model_client:"本地模型调用代码",model_output:"模型输出解析",attachment:"附件处理",tool:"工具调用",validation:"答案验证",credentials:"凭据检查",execution:"任务执行"} as Record<string,string>)[failure.component]||"任务执行")}{failure.tool?` · ${failure.tool}`:""}{failure.provider?` · ${failure.provider}`:""}</p>
            <details><summary>{t("诊断信息与任务编号")}</summary><p>{t("错误码：")}{failure.code}</p>
              {failure.diagnostic?.exception_type&&<p>{t("异常类型：")}{failure.diagnostic.exception_type}</p>}
              {failure.diagnostic?.http_status&&<p>HTTP：{failure.diagnostic.http_status}</p>}
              {failure.diagnostic?.retry_category&&<p>{locale==="en"?"Retry cause: ":"重试原因："}{failure.diagnostic.retry_category} · {locale==="en"?"Attempts: ":"尝试次数："}{failure.diagnostic.attempt_count}</p>}
              {failure.diagnostic?.upstream_code&&<p>{t("服务错误码：")}{failure.diagnostic.upstream_code}</p>}
              {failure.diagnostic?.request_id&&<p>{t("请求 ID：")}{failure.diagnostic.request_id}</p>}
              {failure.diagnostic?.validation_code&&<p>{locale==="en"?"Validation:":"校验原因："} {failure.diagnostic.validation_code} · {failure.diagnostic.phase}</p>}
              {failure.diagnostic?.response_status&&<p>{locale==="en"?"Model response:":"模型响应："} {failure.diagnostic.response_status}{failure.diagnostic.incomplete_reason!=="none"?` · ${failure.diagnostic.incomplete_reason}`:""}</p>}
              {failure.diagnostic?.model&&<p>{locale==="en"?"Model:":"模型："} {failure.diagnostic.model}{failure.diagnostic.reasoning_effort&&` · ${failure.diagnostic.reasoning_effort}`}</p>}
              {failure.diagnostic?.max_output_tokens!==undefined&&<p>{locale==="en"?"Output limit:":"输出上限："} {failure.diagnostic.max_output_tokens} tokens</p>}
              {failure.diagnostic?.output_tokens!==undefined&&<p>{locale==="en"?"Output usage:":"输出用量："} {failure.diagnostic.output_tokens} tokens{failure.diagnostic.reasoning_tokens!==undefined?` · ${locale==="en"?"reasoning":"推理"} ${failure.diagnostic.reasoning_tokens}`:""}</p>}
              <p>{t("任务编号：")}<code>{failure.taskId}</code></p><button onClick={()=>void navigator.clipboard.writeText(failure.taskId).catch(()=>setError("复制失败，请手动选择任务编号。"))}>{t("复制任务编号")}</button></details>
          </div>}</div>}
        {failure&&(failure.code.startsWith("credential_")||failure.code==="provider_authentication")&&<div className="failure-actions"><button onClick={()=>setSettingsOpen(true)}>{locale==="en"?"Check":"检查"} {run?.provider||selection.provider} API Key</button></div>}
      </section>
      <footer className="composer-area">
        {notice&&<p className="onboarding-notice" role="status">{t(notice)}<button onClick={()=>setNotice("")} aria-label={t("关闭提示")}>×</button></p>}
        {error&&<div className="error-banner" role="alert">{t(error)}<button onClick={()=>setError("")} aria-label={t("关闭错误")}><X size={14}/></button></div>}
        <form className="composer" onSubmit={event=>{event.preventDefault();void submit();}}>
          {file&&<div className="attachment-chip"><FileText size={13}/>{file.name}{selection.provider==="openai"&&file.name.toLowerCase().endsWith(".pdf")&&catalog?.models.some(m=>m.provider===selection.provider&&m.model===selection.model&&m.capabilities.image)&&<span>{locale==="en"?"Full PDF → OpenAI":"完整 PDF → OpenAI"}</span>}<button type="button" onClick={()=>setFile(null)} disabled={pending} aria-label={t("移除附件")}><X size={13}/></button></div>}
          <textarea aria-label={t("问题")} placeholder={t("你想聊些什么？")} value={question} maxLength={12000} rows={2}
            onChange={event=>setQuestion(event.target.value)} onKeyDown={event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();void submit();}}} disabled={busy}/>
          <div className="composer-actions"><label className="attach-button" title={t("附件（最大 32 MB）")}><Paperclip size={18}/><input type="file" aria-label={t("附件（最大 32 MB）")} disabled={busy}
            accept=".pdf,.docx,.txt,.csv,.xlsx,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a" onChange={event=>{const selected=event.currentTarget.files?.[0];if(selected)setFile(selected);event.currentTarget.value="";}}/></label>
            <label className="temporary-control" title={t("本次不读取、不保存长期记忆；普通聊天记录仍保存。模式不会删除已有记忆。")}><input type="checkbox" checked={memoryMode==="temporary"} disabled={busy} onChange={event=>{setMemoryMode(event.target.checked?"temporary":"normal");attempt.current=null;}}/>{t("临时对话")}</label>
            <ModelSelector catalog={catalog} selection={selection} disabled={busy} onChange={chooseModel} onRefresh={refreshModelCatalog}/>
            {signedIn?<button className="send-button" type="submit" disabled={busy||(!question.trim()&&!file)} aria-label={t("发送问题")}>{pending?<LoaderCircle size={18} className="spin"/>:<ArrowUp size={20}/>}</button>
              :<button className="signin-button" type="button" onClick={()=>void beginSignIn()}>{t("登录")} <ArrowUpRight size={15}/></button>}</div>
        </form>
        {!messages.length&&<div className="prompt-grid">{prompts.map(item=><button key={item.label} disabled={busy} onClick={()=>setQuestion(locale==="zh"?item.zh:item.text)}><item.icon size={15}/>{t(item.label)}</button>)}</div>}
        {memoryUnavailable&&<button className="memory-unavailable" onClick={()=>signedIn?setMemoryOpen(true):void beginSignIn()}>{t("记忆服务暂不可用")}</button>}
      </footer>
    </main>
  </div>;
}
