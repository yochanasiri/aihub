"""Export successfully extracted crawl text as JSONL for inspection or downstream indexing.
This does NOT write to Qdrant or replace existing AIHUB data.
"""
import argparse,json
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--crawl',default='iiti_full_crawl');p.add_argument('--output',default='iiti_rag_records.jsonl');a=p.parse_args()
 root=Path(a.crawl).resolve();records=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
 count=0;skipped=[]
 with open(a.output,'w',encoding='utf-8') as w:
  for url,record in sorted(records.items()):
   if record.get('status') not in ('html_extracted','pdf_extracted','unchanged','unchanged_content'):
    skipped.append({'url':url,'status':record.get('status')});continue
   p=Path(record.get('text_path',''))
   if not p.exists():skipped.append({'url':url,'status':'text_file_missing'});continue
   content=p.read_text(encoding='utf-8')
   if len(content)<80:skipped.append({'url':url,'status':'text_too_short'});continue
   item={'document_id':record.get('sha256'),'source_url':url,'final_url':record.get('final_url',url),'title':record.get('title',''),'text':content,'mime':record.get('mime'),'last_checked_utc':record.get('last_checked_utc'),'pages_extracted':record.get('pages_extracted'),'page_count':record.get('page_count')}
   w.write(json.dumps(item,ensure_ascii=False)+'\n');count+=1
 print(json.dumps({'exported':count,'skipped':len(skipped),'output':str(Path(a.output).resolve())},indent=2))
 (Path(a.output).with_suffix('.skipped.json')).write_text(json.dumps(skipped,indent=2),encoding='utf-8')
if __name__=='__main__':main()
