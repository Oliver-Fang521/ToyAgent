import test from "node:test";
import assert from "node:assert/strict";
import { loadConversationMessages, completedMessage, reopenedRun } from "../app/lib/history.ts";

test("terminal retry retains answer and evidence", () => {
  assert.deepEqual(completedMessage({status:"completed",result:{answer:"answer",evidence:[{summary:"source"}]}}),
    {role:"assistant",content:"answer",evidence:[{summary:"source"}]});
  assert.equal(completedMessage({status:"failed"}), null);
});
test("display loads all pages without bounding model context", async () => {
  const offsets:number[] = [];
  const messages = await loadConversationMessages(async offset => {
    offsets.push(offset);
    return {messages:[{role:"user",content:String(offset)}],next_offset:offset === 0 ? 100 : null};
  });
  assert.deepEqual(offsets,[0,100]);
  assert.equal(messages.length,2);
});
test("reopening queued/running history restores its cancelable run, never resubmits",()=>{
 for(const status of ["queued","running","failed","cancelled","interrupted"]){const run={id:"original",status};assert.equal(reopenedRun(run),run);}
 assert.equal(reopenedRun({id:"done",status:"completed"}),null);
 assert.equal(reopenedRun(null),null);
});
