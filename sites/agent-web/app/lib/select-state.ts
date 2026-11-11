export type SelectState={open:boolean;active:string};
export type SelectEvent="ArrowDown"|"ArrowUp"|"Home"|"End"|"Enter"|" "|"Escape";
export function selectTransition(state:SelectState,key:SelectEvent,options:readonly {value:string;disabled?:boolean}[]):SelectState{
 const enabled=options.filter(o=>!o.disabled).map(o=>o.value);
 if(key==="Escape")return {...state,open:false};
 if(!enabled.length)return {...state,open:false};
 const index=enabled.indexOf(state.active);
 if(!state.open)return {open:true,active:index<0?enabled[0]:state.active};
 if(key==="Enter"||key===" ")return {...state,open:false};
 const next=key==="Home"?0:key==="End"?enabled.length-1:(Math.max(0,index)+(key==="ArrowUp"?-1:1)+enabled.length)%enabled.length;
 return {open:true,active:enabled[next]};
}
