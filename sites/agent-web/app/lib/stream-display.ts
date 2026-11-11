/** The user is following when they are within 24 pixels of the end. */
export function nearBottom(scrollTop:number, clientHeight:number, scrollHeight:number):boolean {
 return scrollHeight - clientHeight - scrollTop <= 24;
}

/** Reveal only text already received; catch up over about six frames. */
export function advanceStream(current:string, target:string):string {
 if(!target.startsWith(current)) return target;
 const remaining=Array.from(target.slice(current.length));
 if(!remaining.length)return current;
 const count=Math.min(24,Math.max(1,Math.ceil(remaining.length/6)));
 return current+remaining.slice(0,count).join('');
}
