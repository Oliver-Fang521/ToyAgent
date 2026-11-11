export function reopenedRun<T extends {status:string}>(run:T|null|undefined):T|null {
  return run&&run.status!=="completed"?run:null;
}

export function completedMessage<E>(run:{status:string;provider?:string|null;model?:string|null;reasoning_effort?:string|null;capability_version?:string|null;result?:{answer:string;evidence:E[];memory?:MemoryReport|null}}) {
  return run.status === "completed" && run.result ?
    {role:"assistant" as const,content:run.result.answer,evidence:run.result.evidence,...(run.result.memory?{memory:run.result.memory}:{}),...(run.provider!==undefined?{provider:run.provider,model:run.model??null}:{}),...(run.reasoning_effort!==undefined?{reasoning_effort:run.reasoning_effort}:{}),...(run.capability_version!==undefined?{capability_version:run.capability_version}:{})} : null;
}

export async function loadConversationMessages<M>(fetchPage:(offset:number)=>Promise<{messages:M[];next_offset:number|null}>) {
  const messages:M[] = [];
  let offset:number|null = 0;
  while (offset !== null) {
    const page = await fetchPage(offset);
    messages.push(...page.messages);
    if (page.next_offset !== null && page.next_offset <= offset) throw new Error("invalid_history_page");
    offset = page.next_offset;
  }
  return messages;
}
import type { MemoryReport } from "./memory.ts";
