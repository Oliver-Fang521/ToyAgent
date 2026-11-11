export function normalizeMathDelimiters(text:string){
 return text.split(/(```[\s\S]*?```|`[^`\n]*`)/g).map((part,index)=>index%2?part:part.replace(/\\\[([\s\S]*?)\\\]/g,(_,body)=>`\n$$\n${body}\n$$\n`).replace(/\\\(([^\n]*?)\\\)/g,(_,body)=>`$${body}$`)).join("");
}

// Keep incomplete Markdown blocks mutable. Closed paragraphs can render independently.
export function splitStreamingMarkdown(text:string){
 let boundary=0, offset=0, fence="", math="";
 for(const line of text.match(/[^\n]*\n|[^\n]+$/g)||[]){
  const trimmed=line.trim();
  const marker=trimmed.match(/^(`{3,}|~{3,})/);
  if(marker&&!math){
   if(!fence)fence=marker[1];
   else if(marker[1][0]===fence[0]&&marker[1].length>=fence.length)fence="";
  }else if(!fence){
   if(math&&trimmed.includes(math))math="";
   else if(!math&&trimmed.startsWith("$$")&&trimmed.indexOf("$$",2)<0)math="$$";
   else if(!math&&trimmed.startsWith("\\[")&&!trimmed.includes("\\]"))math="\\]";
  }
  offset+=line.length;
  if(!trimmed&&!fence&&!math&&line.endsWith("\n"))boundary=offset;
 }
 return {stable:text.slice(0,boundary),tail:text.slice(boundary),literalTail:!!math};
}
