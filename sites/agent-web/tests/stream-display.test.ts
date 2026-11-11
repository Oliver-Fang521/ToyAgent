import test from 'node:test';
import assert from 'node:assert/strict';
import {nearBottom, advanceStream} from '../app/lib/stream-display.ts';

test('follow resumes only when the reader is near the bottom',()=>{
 assert.equal(nearBottom(600,400,1000),true);
 assert.equal(nearBottom(500,400,1000),false);
 assert.equal(nearBottom(0,500,400),true);
});

test('stream display reveals real text in small frames and preserves emoji',()=>{
 const target='Hello 🌎 '+ 'x'.repeat(120);
 const first=advanceStream('',target);
 assert.ok(first.length>0 && first.length<target.length);
 assert.ok(target.startsWith(first));
 assert.ok(!/[\uD800-\uDBFF]$/.test(first));
 let current=first;
 for(let i=0;i<100&&current!==target;i++)current=advanceStream(current,target);
 assert.equal(current,target);
});

test('stream reset immediately drops text from the old draft',()=>{
 assert.equal(advanceStream('old draft','new'), 'new');
 assert.equal(advanceStream('old',''), '');
});
