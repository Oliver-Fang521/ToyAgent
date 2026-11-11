import type {MemoryView} from "./memory.ts";
const facets={profile:["preferred_language","response_style","education_background","professional_background","learning_subjects","skill_experience"],project:["goal","stack","constraint","status"],decision:["choice"]};
export const memoryTypes=[
 ["profile:preferred_language","语言偏好","背景与偏好"],["profile:response_style","回答风格","背景与偏好"],["profile:education_background","教育背景","背景与偏好"],["profile:professional_background","职业背景","背景与偏好"],["profile:learning_subjects","学习方向","背景与偏好"],["profile:skill_experience","技能经验","背景与偏好"],
 ["project:goal","项目目标","项目与目标"],["project:stack","技术栈","项目与目标"],["project:constraint","项目约束","项目与目标"],["project:status","当前进展","项目与目标"],["decision:choice","已作出的决定","经历与决定"],
] as const;
export function memoryTopicPayload(category:string,topic:string,entity:string){if(!Object.hasOwn(facets,category)||!topic.trim()||topic.trim().length>64||entity.trim().length>128||category!=="profile"&&!entity.trim())throw new Error("memory_invalid");return {category:category as MemoryView["category"],facet:topic.trim(),entity:category==="profile"?"self":entity.trim()};}
export function memoryTypePayload(type:string,entity:string){const index=type.indexOf(":");return memoryTopicPayload(type.slice(0,index),type.slice(index+1),entity);}
export function memoryDeleteBody(revision:number,permanent:boolean){return {revision,permanent};}
export type MemoryEditorState={content:string;conflict:boolean};
export function memoryMutationAction(active:boolean,confirmed:boolean){return active&&!confirmed?"confirm":"submit";}
export function memoryEditorTransition(state:MemoryEditorState,event:{type:"reset"}|{type:"rebase"}|{type:"content";content:string}|{type:"failure";status?:number}):MemoryEditorState{return event.type==="reset"?{content:"",conflict:false}:event.type==="rebase"?{...state,conflict:false}:event.type==="content"?{...state,content:event.content}:{...state,conflict:event.status===409};}
export function rebaseMemoryDraft<T extends {id:string;revision:number;content:string}>(draft:MemoryEditorState,items:T[],id:string){const record=items.find(item=>item.id===id);if(!record)throw new Error("memory_not_found");return {draft:memoryEditorTransition(draft,{type:"rebase"}),record};}
export function searchMemories<T extends {content:string}>(items:T[],query:string):T[]{const term=query.trim().toLocaleLowerCase();return term?items.filter(item=>item.content.toLocaleLowerCase().includes(term)):items;}
