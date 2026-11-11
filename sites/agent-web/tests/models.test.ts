import test from "node:test";
import assert from "node:assert/strict";
import { matchingHandle, requiresNewConversation } from "../app/lib/models.ts";
import { completedMessage } from "../app/lib/history.ts";
test("provider changes isolate history and handles",()=>{
 const openai={provider:"openai" as const,model:"gpt-6.1-sol"};
 const deepseek={provider:"deepseek" as const,model:"deepseek-flash"};
 assert.equal(requiresNewConversation("openai",deepseek),true);
 assert.equal(requiresNewConversation("deepseek",deepseek),false);
 assert.equal(requiresNewConversation(null,openai),true);
 assert.equal(matchingHandle({provider:"openai",handle:"private",expiresAt:100},deepseek,50),null);
 assert.equal(matchingHandle({provider:"deepseek",handle:"private",expiresAt:100},deepseek,101),null);
});
test("completed message retains immutable selection",()=>{
 assert.deepEqual(completedMessage({status:"completed",provider:"deepseek",model:"deepseek-flash",result:{answer:"a",evidence:[]}}),{role:"assistant",content:"a",evidence:[],provider:"deepseek",model:"deepseek-flash"});
});

test("picker respects capabilities and preserves explicit reasoning in history",async()=>{
 const {modelSelection}=await import("../app/lib/models.ts");
 const profile={provider:"openai" as const,model:"gpt-6.1-sol",allowed_reasoning_efforts:["low","medium","high","xhigh","max"],default_reasoning_effort:"low"};
 assert.deepEqual(modelSelection(profile,"high"),{provider:"openai",model:"gpt-6.1-sol",reasoning_effort:"high"});
 assert.throws(()=>modelSelection(profile,"none"));
 assert.deepEqual(modelSelection({...profile,model:"gpt-4.1",allowed_reasoning_efforts:[],default_reasoning_effort:null}),{provider:"openai",model:"gpt-4.1"});
 const message=completedMessage({status:"completed",provider:"openai",model:"gpt-6.1-sol",reasoning_effort:"max",capability_version:"v1",result:{answer:"a",evidence:[]}});
 assert.equal(message?.reasoning_effort,"max");
 assert.equal(completedMessage({status:"completed",provider:"openai",model:"old",result:{answer:"a",evidence:[]}})?.reasoning_effort,undefined);
});

test("new sessions default to GPT-6.1 Terra medium",async()=>{
 const {initialSelection}=await import("../app/lib/models.ts");
 assert.deepEqual(initialSelection,{provider:"openai",model:"gpt-6.1-terra",reasoning_effort:"medium"});
});
