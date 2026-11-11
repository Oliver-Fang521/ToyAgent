"use client";
import {useLayoutEffect,useRef,useState} from "react";
import {advanceStream} from "./stream-display";

export function useStreamDisplay(target:string, runId:string|undefined):string {
 const [displayed,setDisplayed]=useState("");
 const current=useRef("");
 const latest=useRef(target);
 const currentRun=useRef(runId);
 const frame=useRef<number | null>(null);
 useLayoutEffect(()=>{
  latest.current=target;
  if(currentRun.current!==runId){currentRun.current=runId;current.current="";setDisplayed("");}
  if(!target.startsWith(current.current) || window.matchMedia("(prefers-reduced-motion: reduce)").matches){
   if(frame.current!==null)cancelAnimationFrame(frame.current);
   frame.current=null;current.current=target;setDisplayed(target);return;
  }
  if(frame.current!==null || target===current.current)return;
  const tick=()=>{
   frame.current=null;
   const next=advanceStream(current.current,latest.current);
   if(next!==current.current){current.current=next;setDisplayed(next);}
   if(next!==latest.current)frame.current=requestAnimationFrame(tick);
  };
  frame.current=requestAnimationFrame(tick);
 },[target,runId]);
 useLayoutEffect(()=>()=>{if(frame.current!==null)cancelAnimationFrame(frame.current);},[]);
 return displayed;
}
