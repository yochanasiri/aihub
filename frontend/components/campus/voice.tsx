'use client';

import { useEffect, useRef, useState } from 'react';
import { Mic, Square, Volume2 } from 'lucide-react';

async function requireOK(response:Response) {
  if(response.ok)return response;
  const data=await response.json().catch(()=>null);
  throw new Error(typeof data?.detail==='string'?data.detail:'Voice service unavailable.');
}

type Recording = {recorder?:MediaRecorder;stream?:MediaStream;timer?:number;abort:AbortController};

export function VoiceInput({enabled,disabled,onText,onError}:{enabled:boolean;disabled:boolean;onText:(text:string)=>void;onError:(error:string)=>void}) {
  const [stage,setStage]=useState<'idle'|'opening'|'recording'|'transcribing'>('idle');
  const current=useRef<Recording|null>(null);
  function release() {
    const job=current.current;
    if(!job)return;
    current.current=null;
    job.abort.abort();
    window.clearTimeout(job.timer);
    if(job.recorder?.state==='recording')job.recorder.stop();
    job.stream?.getTracks().forEach(track=>track.stop());
  }
  useEffect(()=>()=>release(),[]);
  async function start() {
    if(current.current)return;
    if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){
      onError('Microphone recording requires a supported browser on HTTPS or localhost.');return;
    }
    const type=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
    if(!type){onError('This browser does not provide a supported audio format.');return;}
    const job:Recording={abort:new AbortController()};
    current.current=job;setStage('opening');onError('');
    try {
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      if(job.abort.signal.aborted){stream.getTracks().forEach(track=>track.stop());return;}
      job.stream=stream;
      const recorder=new MediaRecorder(stream,{mimeType:type,audioBitsPerSecond:64000});
      job.recorder=recorder;
      const chunks:Blob[]=[];
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      recorder.onerror=()=>{release();setStage('idle');onError('Recording failed.');};
      recorder.onstop=async()=>{
        window.clearTimeout(job.timer);
        stream.getTracks().forEach(track=>track.stop());
        if(job.abort.signal.aborted)return;
        setStage('transcribing');
        try {
          const kind=type.split(';')[0];
          const response=await requireOK(await fetch('/api/campus/voice/transcribe',{method:'POST',signal:job.abort.signal,
            headers:{'Content-Type':kind},body:new Blob(chunks,{type:kind})}));
          const data=await response.json();
          if(!job.abort.signal.aborted)onText(data.text);
        } catch(e) {
          if(!job.abort.signal.aborted)onError(e instanceof Error?e.message:'Transcription failed.');
        } finally {
          if(current.current===job){release();setStage('idle');}
        }
      };
      recorder.start();
      setStage('recording');
      job.timer=window.setTimeout(()=>{if(recorder.state==='recording')recorder.stop();},28000);
    } catch {
      if(!job.abort.signal.aborted){release();setStage('idle');onError('Could not access the microphone. Check your browser permissions.');}
    }
  }
  const label=stage==='recording'?'Stop and transcribe recording':stage==='opening'?'Opening microphone…':stage==='transcribing'?'Transcribing…':enabled?'Record a question':'Local voice input is disabled';
  return <button type="button" className="mic-button" aria-label={label} title={label}
    disabled={!enabled||stage==='opening'||stage==='transcribing'||(disabled&&stage==='idle')}
    onClick={()=>stage==='recording'?current.current?.recorder?.stop():void start()}>
    {stage==='recording'?<Square size={20}/>:<Mic size={20}/>}
  </button>;
}

export function Listen({id,enabled}:{id:string;enabled:boolean}) {
  const [url,setUrl]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const request=useRef<AbortController|null>(null);
  useEffect(()=>()=>request.current?.abort(),[]);
  useEffect(()=>()=>{if(url)URL.revokeObjectURL(url);},[url]);
  async function load() {
    const abort=new AbortController();request.current=abort;
    setBusy(true);setError('');
    try{
      const response=await requireOK(await fetch('/api/campus/voice/speak/'+id,{method:'POST',
        headers:{'Content-Type':'application/json'},body:'{}',signal:abort.signal}));
      const blob=await response.blob();
      if(!abort.signal.aborted)setUrl(URL.createObjectURL(blob));
    }catch(e){if(!abort.signal.aborted)setError(e instanceof Error?e.message:'Playback unavailable.');}
    finally{if(!abort.signal.aborted)setBusy(false);}
  }
  return <div>
    {url?<audio controls autoPlay src={url} aria-label="Spoken answer"/>:
      <button type="button" disabled={!enabled||busy} onClick={()=>void load()} title={enabled?'Listen to answer':'Local playback is disabled'}><Volume2 size={15}/><span>{busy?'Preparing audio…':'Listen'}</span></button>}
    {error&&<small role="alert">{error}</small>}
  </div>;
}
