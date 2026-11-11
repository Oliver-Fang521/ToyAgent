import test from "node:test";
import assert from "node:assert/strict";
import {applyRunEvent} from "../app/lib/run-events.ts";

test("individual events update output and progress without losing run state",()=>{
 const run={id:"1",status:"running",progress:[] as {event:string;time:number;tool?:string}[],partial_answer:"Hi"};
 const a=applyRunEvent(run,"answer.delta",{delta:" there"});
 assert.equal(a.partial_answer,"Hi there");
 const b=applyRunEvent(a,"tool.started",{event:"tool_call_started",tool:"web_search",time:1});
 assert.equal(b.progress[0].tool,"web_search");
 const c=applyRunEvent(b,"answer.reset",{text:""});
 assert.equal(c.partial_answer,"");
 assert.equal(applyRunEvent(c,"run.completed",{...c,status:"completed"}).status,"completed");
});

test("a reconnect snapshot replaces partial state and invalid events are rejected",()=>{
 const run={id:"1",status:"running",progress:[] as {event:string;time:number}[],partial_answer:"stale"};
 const resumed=applyRunEvent(run,"run.snapshot",{id:"1",status:"running",progress:[],partial_answer:"fresh"});
 assert.equal(resumed.partial_answer,"fresh");
 assert.throws(()=>applyRunEvent(run,"run.snapshot",{id:"other",status:"running",progress:[]}));
 assert.throws(()=>applyRunEvent(run,"answer.delta",{delta:12}));
});

test('only research candidates are labelled unverified', async()=>{
 const {partialAnswerLabel}=await import('../app/lib/run-events.ts');
 assert.equal(partialAnswerLabel([{event:'routing_started',time:1}]),'生成中');
 assert.equal(partialAnswerLabel([{event:'act_started',time:2}]),'草稿 · 尚未验证');
 assert.equal(partialAnswerLabel([{event:'act_started',time:2},{event:'verify_started',time:3}]),'草稿 · 尚未验证');
});

test('verification can replace a draft with the final answer',()=>{
 const run={id:'1',status:'running',progress:[] as {event:string;time:number}[],partial_answer:'uncorrected'};
 const reset=applyRunEvent(run,'answer.reset',{text:'corrected draft'});
 const complete=applyRunEvent(reset,'run.completed',{id:'1',status:'completed',progress:[],result:{answer:'verified answer'}});
 assert.equal(complete.status,'completed');
 assert.equal((complete as unknown as {result:{answer:string}}).result.answer,'verified answer');
});
