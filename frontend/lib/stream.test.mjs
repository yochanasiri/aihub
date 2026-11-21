import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const source = await readFile(new URL('./stream.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { consumeEvents } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
function stream(value,step=1){const data=new TextEncoder().encode(value);return new ReadableStream({start(controller){for(let i=0;i<data.length;i+=step)controller.enqueue(data.slice(i,i+step));controller.close();}});}
test('SSE handles split Unicode, CRLF, comments and frames',async()=>{const seen=[];await consumeEvents(stream(': keepalive\r\n\r\nevent: delta\r\ndata: {"text":"Indore • परिसर"}\r\n\r\nevent: done\ndata: {"status":"answered"}\n\n'),(name,data)=>seen.push([name,data]));assert.deepEqual(seen,[['delta',{text:'Indore • परिसर'}],['done',{status:'answered'}]]);});
test('SSE rejects truncated data instead of treating partial answers as done',async()=>{await assert.rejects(consumeEvents(stream('event: done\ndata: {"ok":tru'),()=>{}),/Incomplete stream/);});
test('SSE cancels its reader when a protocol error is raised',async()=>{let cancelled=false;const body=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('event: error\ndata: {"discard_partial":true}\n\n'));},cancel(){cancelled=true;}});await assert.rejects(consumeEvents(body,()=>{throw new Error('discard');}),/discard/);assert.equal(cancelled,true);});
