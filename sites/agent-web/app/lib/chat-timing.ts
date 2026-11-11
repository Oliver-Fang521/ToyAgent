// Browser-local timings for newly submitted turns, in the browser's monotonic clock.
const pending = new Map<string, number>();
const measureName = "toyagent.first-answer-frame";

export function recordChatSubmit(runId:string, started:number) {
 if (pending.size >= 32) pending.delete(pending.keys().next().value!);
 pending.set(runId, started);
}

export function recordFirstAnswerFrame(runId:string) {
 const started = pending.get(runId);
 if (started === undefined || document.visibilityState !== "visible") return;
 pending.delete(runId);
 requestAnimationFrame(()=>{
  if (document.visibilityState !== "visible") return;
  if (performance.getEntriesByName(measureName).length >= 100) performance.clearMeasures(measureName);
  performance.measure(measureName, {start:started, end:performance.now(), detail:{runId}});
 });
}
