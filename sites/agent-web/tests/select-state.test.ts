import test from "node:test";
import assert from "node:assert/strict";

test("nested menu keyboard contract keeps selection within enabled options", async()=>{
 const {selectTransition}=await import("../app/lib/select-state.ts");
 const options=[{value:"a"},{value:"b",disabled:true},{value:"c"}];
 let state={open:false,active:"a"};
 state=selectTransition(state,"ArrowDown",options);
 assert.deepEqual(state,{open:true,active:"a"});
 state=selectTransition(state,"ArrowDown",options);
 assert.equal(state.active,"c");
 state=selectTransition(state,"ArrowDown",options);
 assert.equal(state.active,"a");
 assert.equal(selectTransition(state,"End",options).active,"c");
 assert.equal(selectTransition(state,"Escape",options).open,false);
 assert.deepEqual(selectTransition({open:false,active:"b"},"Enter",options),{open:true,active:"a"});
});
