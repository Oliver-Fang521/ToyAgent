export const phaseLabels = {prepare:"准备",routing:"理解问题",plan:"规划",research:"研究/工具",correction:"修正",verify:"验证",submit:"答案提交"} as const;
export type Phase = keyof typeof phaseLabels;
export type RunFailure = {stage:Phase;code:string;component?:string;tool?:string;provider?:string;tool_category?:string;reason?:string;diagnostic?:{exception_type?:string;http_status?:number;upstream_code?:string;request_id?:string;phase?:string;validation_code?:string;response_status?:string;incomplete_reason?:string;retry_category?:string;attempt_count?:number;model?:string;reasoning_effort?:string;max_output_tokens?:number;output_tokens?:number;reasoning_tokens?:number}};
type RunState = {id:string;status:string;error?:string;progress:{event:string;tool?:string}[];failure?:RunFailure|null};
const eventPhases:Record<string,Phase>={routing_started:"routing",plan_started:"plan",act_started:"research",verify_started:"verify",correction_started:"correction",submit_started:"submit",evidence_repair_started:"verify"};
export function currentStage(events:{event:string}[]):Phase {
 let stage:Phase="prepare";for(const item of events)stage=eventPhases[item.event]||stage;return stage;
}
export function completionView(events:{event:string}[]) {
 if(events.some(item=>item.event==="clarification_completed"))return {label:"等待你补充信息",research:false};
 if(events.some(item=>item.event==="chat_completed"))return {label:"回复完成",research:false};
 return {label:"研究完成",research:true};
}
export function stageVisited(events:{event:string}[],stage:Phase) {
 return stage==="prepare"||events.some(item=>eventPhases[item.event.replace(/_completed$/,"_started")]===stage);
}
export function failureView(run:RunState|null){
 if(!run||!["failed","interrupted"].includes(run.status))return null;
 const stage=run.failure?.stage&&run.failure.stage in phaseLabels?run.failure.stage:currentStage(run.progress||[]);
 const code=run.failure?.code||run.error||"execution_failed",diagnostic=run.failure?.diagnostic;
 let message="任务执行失败。",suggestion="请提供任务编号以便排查；不要反复提交相同请求。";
 if(diagnostic?.upstream_code==="insufficient_quota"){message="模型服务报告 API 额度不足。";suggestion="请检查 API 账号余额与用量限制；会员订阅不等于 API 额度。";}
 else if(diagnostic?.http_status===429){message="模型服务限制了本次请求。";suggestion="请稍后重试，并检查 API 速率与用量限制。";}
 else if(code==="provider_authentication"){message="模型服务拒绝了凭据或权限。";suggestion="请检查 Key 与接收服务域名是否匹配。";}
 else if(code==="provider_model_unavailable"){message="模型服务拒绝了模型或请求参数。";suggestion="请核对账号的模型权限及接口支持情况。";}
 else if(code==="model_unavailable"){message="所选模型已不可用。";suggestion="请重新选择模型后再发送。";}
 else if(code==="capability_unavailable"){message="当前模型或工具不支持这项操作。";suggestion="请选择支持的模型；DeepSeek 暂不支持托管 Python 或音频，扫描 PDF 需先 OCR。";}
 else if(code==="timeout"){message="本阶段超过时间限制。";suggestion="请缩小问题范围后重试。";}
 else if(code.startsWith("credential_")){message="凭据缺失、过期或已撤销。";suggestion="请重新配置自己的 API Key。";}
 else if(code==="critic_rejection"){message=run.failure?.reason==="insufficient_evidence"?"证据不足，无法验证答案的可靠性。":run.failure?.reason==="revise"?"答案修正后仍未通过验证。":"验证未通过，具体拒绝原因未记录。";suggestion="请补充可信来源、相关附件或明确问题范围。";}
 else if(code==="malformed_response"&&diagnostic?.incomplete_reason==="max_output_tokens"){message="模型输出达到上限，答案未完整生成。";suggestion="已尝试恢复但仍未完成；可缩小问题范围后重试。";}
 else if(code==="malformed_response"||code==="no_answer"){message="模型输出缺失或不符合所需格式。";suggestion="模型可能未完成结构化输出；请提供任务编号排查接口兼容性。";}
 else if(code==="tool_budget_exhausted"){message="工具调用次数已达到预算上限。";suggestion="请缩小问题范围或分成多次任务。";}
 else if(code==="loop_detected"){message="检测到重复调用，已停止任务。";suggestion="请补充限制条件或调整问题，避免重复执行。";}
 else if(code==="attachment_error"){message="附件处理失败。";suggestion="请检查文件是否存在、格式是否受支持，并重新上传。";}
 else if(code==="service_restarted"){message="服务重启中断了任务。";suggestion="确认服务恢复后重试；已发送的请求可能仍计费。";}
 else if(code==="retry_exhausted"&&diagnostic?.retry_category){message=diagnostic.retry_category==="APITimeoutError"?"模型服务响应超时，重试后仍未成功。":diagnostic.retry_category==="APIConnectionError"?"无法连接模型服务，重试后仍未成功。":diagnostic.retry_category==="RateLimitError"?"模型服务限制了请求，重试后仍未成功。":"模型服务暂时异常，重试后仍未成功。";suggestion="请稍后重试；如持续发生，请提供任务编号。";}
 else if(code==="provider_unavailable")message=diagnostic?.exception_type==="APIConnectionError"?"无法连接模型服务。":diagnostic?.exception_type==="APITimeoutError"?"模型服务响应超时。":diagnostic?.http_status?"模型服务未能完成请求。":"模型调用未完成，具体原因未记录。";
 const tool=run.failure?.tool;
 const component=run.failure?.component|| (code.startsWith("provider_")?"model":"execution");
 if(component==="model_client"){message="本地模型调用代码发生异常。";suggestion="请提供任务编号和异常类型排查；反复换 Key 不一定能解决。";}
 if(component==="tool"&&tool){message=`工具 ${tool} 调用失败。`;suggestion="请检查输入文件、网络或对应工具服务；查看诊断信息定位。";}
 if(component==="tool"&&tool&&run.failure?.tool_category==="repeated_call"){message=`工具 ${tool} 重复调用，已停止任务。`;suggestion="请调整问题或报告任务编号，以便检查工具调用循环。";}
 if(component==="tool"&&tool&&run.failure?.tool_category==="tool_budget_exhausted"){message=`工具 ${tool} 的调用次数已达到预算上限。`;suggestion="请缩小问题范围或分成多次任务。";}
 if(component==="tool"&&run.failure?.tool_category==="balance"){message="搜索服务余额不足。";suggestion="请检查对应服务商的 API 余额。";}
 if(component==="tool"&&["authentication","forbidden"].includes(run.failure?.tool_category||"")){message="搜索服务拒绝了 Key 或权限。";suggestion="请检查对应服务商的 Key 与访问权限。";}
 if(component==="tool"&&run.failure?.tool_category==="invalid_input"){message="搜索词不符合要求。";suggestion="搜索词需为 1–128 个字符。";}
 return {stage,label:phaseLabels[stage],message,suggestion,code,diagnostic,taskId:run.id,component,tool,provider:run.failure?.provider};
}
