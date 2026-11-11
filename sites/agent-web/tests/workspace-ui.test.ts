import test from "node:test";
import assert from "node:assert/strict";
import { parseLocale, translateUI } from "../app/lib/ui-language.ts";
import { filterModels } from "../app/lib/model-picker.ts";

test("language preferences accept only supported locales and preserve unknown UI values",()=>{
 assert.equal(parseLocale("en"),"en");
 for(const value of [null,"fr","<script>","EN"])assert.equal(parseLocale(value),"zh");
 assert.equal(translateUI("新建对话","en"),"New chat");
 assert.equal(translateUI("新建对话","zh"),"新建对话");
 assert.equal(translateUI("opaque model identifier","en"),"opaque model identifier");
});
test("model search intersects provider filtering without inventing catalog entries",()=>{
 const models=[{provider:"openai",model:"o",display_name:"GPT model"},{provider:"deepseek",model:"d",display_name:"DeepSeek Flash"}];
 assert.deepEqual(filterModels(models,"deepseek"," FLASH "),[models[1]]);
 assert.deepEqual(filterModels(models,"openai","flash"),[]);
 assert.deepEqual(filterModels(models,"all","absent"),[]);
 assert.deepEqual(filterModels(models,"all",""),models);
});
