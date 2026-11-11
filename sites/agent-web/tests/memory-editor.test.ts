import test from "node:test";
import assert from "node:assert/strict";
test("memory editor conflict retains draft and owner reset clears content",async()=>{
 const {memoryEditorTransition,memoryTypePayload,searchMemories,rebaseMemoryDraft,memoryMutationAction}=await import("../app/lib/memory-editor.ts");
 const draft={content:"my unsaved draft",conflict:false};
 assert.deepEqual(memoryEditorTransition(draft,{type:"failure",status:409}),{content:"my unsaved draft",conflict:true});
 assert.deepEqual(memoryEditorTransition(draft,{type:"reset"}),{content:"",conflict:false});
 assert.deepEqual(rebaseMemoryDraft({content:"keep my draft",conflict:true},[{id:"one",revision:2,content:"remote edit"}],"one"),{draft:{content:"keep my draft",conflict:false},record:{id:"one",revision:2,content:"remote edit"}});
 assert.throws(()=>rebaseMemoryDraft(draft,[],"deleted"),/memory_not_found/);
 assert.equal(memoryMutationAction(true,false),"confirm");
 assert.equal(memoryMutationAction(true,true),"submit");
 assert.equal(memoryMutationAction(false,false),"submit");
 assert.deepEqual(memoryTypePayload("profile:learning_subjects","ignored"),{category:"profile",facet:"learning_subjects",entity:"self"});
 assert.deepEqual(memoryTypePayload("project:stack","ToyAgent"),{category:"project",facet:"stack",entity:"ToyAgent"});
 assert.deepEqual(memoryTypePayload("profile:invented","self"),{category:"profile",facet:"invented",entity:"self"});
 assert.throws(()=>memoryTypePayload("decision:choice",""));
 assert.equal(searchMemories([{content:"I study Python"},{content:"我学习数学"}],"PYTHON").length,1);
});

test("older snapshot cannot restore deleted memory after a mutation refresh",async()=>{
 const {createMemorySnapshotLoader}=await import("../app/lib/memory.ts");
 const resolve:((value:unknown)=>void)[]=[];
 const loader=createMemorySnapshotLoader(()=>new Promise(done=>resolve.push(done)) as never);
 const old=loader.load();loader.invalidate();const latest=loader.load();
 resolve[1]({settings:{generation:2},items:[]});
 assert.deepEqual(await latest,{settings:{generation:2},items:[]});
 resolve[0]({settings:{generation:1},items:[{content:"deleted body"}]});
 assert.equal(await old,null);
 const closed=loader.load();loader.invalidate();resolve[2]({settings:{generation:3},items:[]});
 assert.equal(await closed,null);
});
