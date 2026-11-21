/** POST SSE parser: keeps UTF-8 and event frames intact across arbitrary network chunks. */
export async function consumeEvents(body: ReadableStream<Uint8Array>, onEvent:(name:string,data:Record<string,unknown>)=>void) {
  const reader=body.getReader(); const decoder=new TextDecoder(); let buffer='';
  function frame(raw:string) {
    let name='message'; const lines:string[]=[];
    for(const line of raw.split('\n')) {if(line.startsWith('event:'))name=line.slice(6).trim();else if(line.startsWith('data:'))lines.push(line.slice(5).trimStart());}
    if(lines.length)onEvent(name,JSON.parse(lines.join('\n')));
  }
  try { for(;;) {const {value,done}=await reader.read();buffer+=decoder.decode(value,{stream:!done});
    // Normalize CRLF after accumulation to handle a split CR/LF pair.
    let match:RegExpExecArray|null;while((match=/\r?\n\r?\n/.exec(buffer))) {frame(buffer.slice(0,match.index).replace(/\r\n/g,'\n'));buffer=buffer.slice(match.index+match[0].length);}
    if(buffer.length>2_000_000)throw new Error('Response exceeded size limit');
    if(done){if(buffer.trim()&&!buffer.startsWith(':'))throw new Error('Incomplete stream');break;}
  }} finally {await reader.cancel().catch(()=>{});reader.releaseLock();}
}
