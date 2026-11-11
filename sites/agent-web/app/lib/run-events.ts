type Progress = {event:string;time:number;tool?:string;provider?:string};
type StreamRun = {id:string;status:string;progress:Progress[];partial_answer?:string};

export const runEventNames=["run.snapshot","run.queued","run.running","run.completed","run.failed","run.cancelled","run.interrupted","answer.delta","answer.reset","stage.updated","tool.started","tool.completed","tool.failed","verification.started","verification.completed","verification.rejected"] as const;

export function applyRunEvent<T extends StreamRun>(run:T,kind:string,data:unknown):T {
 if(!data || typeof data!=="object")throw new Error("Invalid run event");
 const value=data as Record<string,unknown>;
 if(kind.startsWith("run.")) {
  if(value.id!==run.id||typeof value.status!=="string"||!Array.isArray(value.progress))throw new Error("Invalid run snapshot");
  return value as T;
 }
 if(kind==="answer.delta"){
  if(typeof value.delta!=="string")throw new Error("Invalid answer delta");
  return {...run,partial_answer:(run.partial_answer||"")+value.delta};
 }
 if(kind==="answer.reset"){
  if(typeof value.text!=="string")throw new Error("Invalid answer reset");
  return {...run,partial_answer:value.text};
 }
 if(kind==="stage.updated"||kind.startsWith("tool.")||kind.startsWith("verification.")){
  if(typeof value.event!=="string"||typeof value.time!=="number")throw new Error("Invalid progress event");
  return {...run,progress:[...run.progress,value as Progress].slice(-100)};
 }
 throw new Error("Unknown run event");
}

export function partialAnswerLabel(progress:Progress[]):string {
 return progress.some(item=>item.event==="act_started"||item.event==="correction_act_started") ? "草稿 · 尚未验证" : "生成中";
}
