import test from "node:test";
import assert from "node:assert/strict";
import { failureView, currentStage, completionView, stageVisited } from "../app/lib/failures.ts";
test("completed research does not claim a correction that never ran",()=>{
 const events=[{event:"routing_started"},{event:"plan_started"},{event:"act_started"},{event:"verify_started"},{event:"submit_completed"}];
 assert.equal(stageVisited(events,"correction"),false);
 assert.equal(stageVisited(events,"verify"),true);
 assert.equal(stageVisited(events,"submit"),true);
});
test("direct answers and clarification do not claim research verification",()=>{
 assert.deepEqual(completionView([{event:"clarification_completed"}]), {label:"等待你补充信息",research:false});
 assert.deepEqual(completionView([{event:"chat_completed"}]), {label:"回复完成",research:false});
 assert.deepEqual(completionView([{event:"submit_completed"}]), {label:"研究完成",research:true});
 assert.equal(currentStage([{event:"routing_started"}]),"routing");
});
test("validation rejection explains the safe reason",()=>{
 const view=failureView({id:"reason",status:"failed",progress:[],failure:{stage:"verify",code:"critic_rejection",reason:"insufficient_evidence"}});
 assert.match(view!.message,/证据不足/);
});
test("legacy failure reports phase without inventing a cause",()=>{
 const view=failureView({id:"task-1",status:"failed",error:"provider_unavailable",progress:[{event:"plan_started"}]});
 assert.equal(view?.stage,"plan");assert.match(view!.message,/具体原因未记录/);
});
test("latest stage wins after correction, not highest visited stage",()=>{
 assert.equal(currentStage([{event:"verify_started"},{event:"act_started"}]),"research");
});
test("quota failure gives actionable message without rendering raw exception",()=>{
 const view=failureView({id:"task-2",status:"failed",progress:[],failure:{stage:"verify",code:"provider_unavailable",diagnostic:{http_status:429,upstream_code:"insufficient_quota"}}});
 assert.equal(view?.stage,"verify");assert.match(view!.message,/额度/);assert.match(view!.suggestion,/API/);
 assert.equal(failureView({id:"task-3",status:"cancelled",progress:[]}),null);
});
test("concrete tool failure is distinct from model failure",()=>{
 const view=failureView({id:"task-4",status:"failed",progress:[],failure:{stage:"research",code:"retry_exhausted",component:"tool",tool:"web_search"}});
 assert.match(view!.message,/web_search/);assert.equal(view!.component,"tool");
});
test("DeepSeek balance failure identifies search billing not model quota",()=>{
 const view=failureView({id:"task-search",status:"failed",progress:[],failure:{stage:"research",code:"retry_exhausted",component:"tool",tool:"web_search",provider:"deepseek",tool_category:"balance"}});
 assert.equal(view?.provider,"deepseek");assert.match(view!.message,/搜索服务余额/);assert.match(view!.suggestion,/API 余额/);
});
test('repeated tool reports the tool and the actual cause',()=>{
 const view=failureView({id:'x',status:'failed',progress:[],failure:{stage:'verify',code:'loop_detected',component:'tool',tool:'read_webpage',tool_category:'repeated_call'}});
 assert.match(view!.message,/read_webpage/);assert.match(view!.message,/重复/);
});
test('local client failures are not mislabeled as a remote outage',()=>{
 const view=failureView({id:'x',status:'failed',progress:[],failure:{stage:'verify',code:'provider_client_error',component:'model_client',diagnostic:{exception_type:'ParserFailure'}}});
 assert.match(view!.message,/本地/);assert.equal(view!.diagnostic?.exception_type,'ParserFailure');
});
test('provider retry gives cause and attempt count',()=>{
 const view=failureView({id:'retry',status:'failed',progress:[],failure:{stage:'verify',code:'retry_exhausted',component:'model',diagnostic:{retry_category:'APITimeoutError',attempt_count:3}}});
 assert.match(view!.message,/超时/);
 assert.equal(view!.diagnostic?.attempt_count,3);
});
test('output truncation explains the missing answer',()=>{
 const view=failureView({id:'x',status:'failed',progress:[],failure:{stage:'research',code:'malformed_response',diagnostic:{incomplete_reason:'max_output_tokens'}}});
 assert.match(view!.message,/输出达到上限/);assert.match(view!.suggestion,/恢复/);
});
