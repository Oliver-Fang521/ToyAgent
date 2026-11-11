"use client";
import {memo,useLayoutEffect,useRef} from "react";
import {recordFirstAnswerFrame} from "../lib/chat-timing";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import {normalizeMathDelimiters,splitStreamingMarkdown} from "../lib/answer-format";

const Markdown = memo(function Markdown({content}:{content:string}){
 return <ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[rehypeKatex]} components={{img:()=>null,a:({href,children})=><a href={href} target="_blank" rel="noopener noreferrer">{children}</a>}}>{normalizeMathDelimiters(content)}</ReactMarkdown>;
});
export default function AnswerContent({content,streaming=false,timingRunId}:{content:string;streaming?:boolean;timingRunId?:string}){
 const root=useRef<HTMLDivElement>(null);
 useLayoutEffect(()=>{
  if(timingRunId && root.current?.textContent?.trim()) recordFirstAnswerFrame(timingRunId);
 },[content,timingRunId]);
 if(!streaming)return <div ref={root} className="message-content answer-content"><Markdown content={content}/></div>;
 const {stable,tail,literalTail}=splitStreamingMarkdown(content);
 return <div ref={root} className="message-content answer-content">{stable&&<Markdown content={stable}/>} {tail&&!literalTail&&<Markdown content={tail}/>}</div>;
}
