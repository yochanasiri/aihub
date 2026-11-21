/** Deliberately illustrative UI content, never an output of institute retrieval. */
export function demoAnswer(question: string) {
  const text = question.toLowerCase();
  const topic = /hostel|mess|accommodation/.test(text) ? 'Hostel & Mess Accommodation'
    : /placement|recruit|package|tpc/.test(text) ? 'Placements & Recruiters'
    : /health|sport|facilit/.test(text) ? 'Campus Facilities & Health'
    : /location|where|simrol/.test(text) ? 'Campus Location'
    : 'Programs & Admissions';
  const academic = topic === 'Programs & Admissions';
  const url = academic ? 'https://academic.iiti.ac.in/' : 'https://iiti.ac.in/';
  const sections: Record<string, string> = {
    'Programs & Admissions': '1. **Programs:** A live answer would list programs supported by retrieved institute documents.\n2. **Admissions:** It would show the published eligibility requirements, process, and dates.\n3. **Details:** Cutoffs and seat information would appear only when present in the retrieved sources.',
    'Hostel & Mess Accommodation': '1. **Accommodation:** A live answer would summarize the hostel facilities described in the source documents.\n2. **Mess services:** It would show published mess information and schedules.\n3. **Contact:** It would include the relevant office contact when available in official sources.',
    'Placements & Recruiters': '1. **Placement reports:** A live answer would cite the available official report and its year.\n2. **Recruiters:** It would list only companies named in that report.\n3. **Statistics:** Package figures would be shown only with a supporting official source.',
    'Campus Facilities & Health': '1. **Health services:** A live answer would show the services and contacts published by the Health Centre.\n2. **Sports facilities:** It would summarize facilities and schedules from retrieved pages.\n3. **Source checks:** Every factual detail would link back to the supporting document.',
    'Campus Location': '1. **Campus address:** A live answer would display the institute address from an official page.\n2. **Directions:** You can open the Campus Location page to see the map.\n3. **Sources:** The supporting official page would be linked below.',
  };
  const content = `**${topic}**\n\nThis is a sample response demonstrating how AIHUB displays an answer. It is not a live AI answer or retrieved campus information.\n\n${sections[topic]}\n\n[Example official portal link](${url})\n\nIn live mode, the assistant answers from retrieved documents or says it could not find sufficient official information.`;
  return { content, sources: [{id:'DEMO',url,title:academic?'Example link: IIT Indore Academic Portal':'Example link: IIT Indore Institute Portal'}] };
}

export async function streamDemo(content:string,signal:AbortSignal,onChunk:(chunk:string)=>void) {
  const pieces=content.match(/.{1,12}(?:\s|$)|.{1,12}/gs) || [content];
  for (const piece of pieces) {
    await new Promise<void>((resolve,reject)=>{
      if(signal.aborted){reject(new DOMException('Stopped','AbortError'));return;}
      const stop=()=>{clearTimeout(timer);reject(new DOMException('Stopped','AbortError'));};
      const timer=setTimeout(()=>{signal.removeEventListener('abort',stop);resolve();},45);
      signal.addEventListener('abort',stop,{once:true});
    });
    if(signal.aborted)throw new DOMException('Stopped','AbortError');
    onChunk(piece);
  }
}
