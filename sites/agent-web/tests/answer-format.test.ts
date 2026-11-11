import test from "node:test";
import assert from "node:assert/strict";
import {normalizeMathDelimiters,splitStreamingMarkdown} from "../app/lib/answer-format.ts";

test("math delimiters render while code remains literal",()=>{
 assert.equal(normalizeMathDelimiters("a \\(x^2\\) b"),"a $x^2$ b");
 assert.equal(normalizeMathDelimiters("\\[x+y\\]"),"\n$$\nx+y\n$$\n");
 assert.equal(normalizeMathDelimiters("`\\(x\\)`\n```tex\n\\[x\\]\n```"),"`\\(x\\)`\n```tex\n\\[x\\]\n```");
});

test("streaming markdown keeps unfinished blocks together",()=>{
 assert.deepEqual(splitStreamingMarkdown("Hello\n\nNext"),{stable:"Hello\n\n",tail:"Next",literalTail:false});
 assert.deepEqual(splitStreamingMarkdown("Hello\n\n$$\nx +\n\n y"),{stable:"Hello\n\n",tail:"$$\nx +\n\n y",literalTail:true});
 assert.deepEqual(splitStreamingMarkdown("Hello\n\n```js\nx\n\n y"),{stable:"Hello\n\n",tail:"```js\nx\n\n y",literalTail:false});
 assert.deepEqual(splitStreamingMarkdown("| A | B |\n|---|---|\n| 1 | 2 |\n"),{stable:"",tail:"| A | B |\n|---|---|\n| 1 | 2 |\n",literalTail:false});
});
