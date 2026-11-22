#!/usr/bin/env python3
"""Auditable, incremental crawler for PUBLIC pages on IIT Indore-owned domains.
No authentication, CAPTCHA evasion, or unrestricted external navigation.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, hashlib, json, logging, mimetypes, re, time, xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup, NavigableString, Tag

UA='AIHUB-IITIndore-PublicIndexer/1.0 (academic RAG; respectful crawl)'
SEEDS=[
 'https://www.iiti.ac.in/', 'https://www.iiti.ac.in/about',
 'https://www.iiti.ac.in/administration/director',
 'https://www.iiti.ac.in/uploads/reports/annual_report/22-23.pdf',
 'https://www.iiti.ac.in/uploads/reports/parliamentary_questions/2024-2025.pdf',
 'https://academic.iiti.ac.in/academic_cal.php',
 'https://cse.iiti.ac.in/faculty.php', 'https://cse.iiti.ac.in/research_area.php',
 'https://meche.iiti.ac.in/',
 'https://www.iiti.ac.in/academics', 'https://www.iiti.ac.in/academics/view_all',
 'https://www.iiti.ac.in/academics/courses/ug', 'https://www.iiti.ac.in/academics/forms_downloads',
 'https://www.iiti.ac.in/academics/scholarships', 'https://www.iiti.ac.in/admissions',
 'https://www.iiti.ac.in/admissions/all_programs','https://www.iiti.ac.in/admissions/pg_admissions',
 'https://www.iiti.ac.in/admissions/phd_admissions', 'https://www.iiti.ac.in/student_affairs',
 'https://www.iiti.ac.in/administration/reports','https://www.iiti.ac.in/administration/rules_and_policies',
 'https://www.iiti.ac.in/administration/statutes_and_acts','https://www.iiti.ac.in/administration/minutes_of_meeting',
 'https://academic.iiti.ac.in/','https://academic.iiti.ac.in/academic_program.php',
 'https://academic.iiti.ac.in/app/course_curriculum_list_pg',
 'https://placement.iiti.ac.in/', 'https://hostel.iiti.ac.in/',
 'https://studentaffairs.iiti.ac.in/','https://library.iiti.ac.in/',
 'https://rnd.iiti.ac.in/', 'https://ir.iiti.ac.in/',
]
EXCLUDED_PREFIX=('erpone.','intranet.','login.','sso.','mail.','outlook.')
EXCLUDED_PATH_PARTS=('/login','/logout','/sign-in','/signin','/admin/login','/wp-admin','/account','/auth/','/user/login')
SKIP_EXT={'.jpg','.jpeg','.png','.gif','.webp','.svg','.mp4','.mov','.mp3','.css','.js','.woff','.woff2','.ico','.zip','.rar','.7z','.exe'}
DOC_EXT={'.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.txt','.csv'}

def normalize(url):
    url=urldefrag(url.strip())[0]; p=urlsplit(url)
    if p.scheme.lower() not in ('https','http') or not p.hostname:return ''
    host=p.hostname.lower().rstrip('.')
    path=re.sub('/+','/',p.path or '/')
    # Remove only common tracking parameters; keep content-selecting query parameters.
    query=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not (k.lower().startswith('utm_') or k.lower() in ('fbclid','gclid'))])
    return urlunsplit((p.scheme.lower(),host+(f':{p.port}' if p.port else ''),path,query,''))

def allowed(url, enable_subdomains):
    p=urlsplit(url); host=p.hostname or ''
    if p.scheme not in ('http','https'):return False
    if enable_subdomains:
        if not (host=='iiti.ac.in' or host.endswith('.iiti.ac.in')):return False
    elif host not in ('www.iiti.ac.in','iiti.ac.in'):return False
    if any(host.startswith(k) for k in EXCLUDED_PREFIX):return False
    if any(k in p.path.lower() for k in EXCLUDED_PATH_PARTS):return False
    if Path(p.path).suffix.lower() in SKIP_EXT:return False
    return True

def content_md(node):
    lines=[]
    def add(x):
        if x:lines.append(x)
    def walk(n):
        if isinstance(n,NavigableString):return
        if not isinstance(n,Tag) or n.name in ('script','style','noscript','svg','form','button','iframe'):return
        tag=n.name
        if tag and re.fullmatch('h[1-6]',tag):add('#'*int(tag[1])+' '+n.get_text(' ',strip=True)+'\n');return
        if tag in ('table',):
            for tr in n.find_all('tr'):
                cells=[c.get_text(' ',strip=True).replace('|','\\|') for c in tr.find_all(['th','td'],recursive=False)]
                if cells:add(' | '.join(cells)+'\n')
            add('\n');return
        if tag=='li':add('- '+n.get_text(' ',strip=True)+'\n');return
        if tag in ('p','blockquote'):add(n.get_text(' ',strip=True)+'\n\n');return
        if tag=='br':add('\n');return
        for c in n.children:walk(c)
    walk(node)
    return re.sub(r'\n{3,}','\n\n',''.join(lines)).strip()+'\n'

def extract_pdf(path,max_pages):
    import fitz
    doc=fitz.open(str(path));out=[];page_info=[]
    total=min(len(doc),max_pages)
    for i in range(total):
        page=doc[i]
        # Sort blocks by physical location; preserve lines and bullet characters.
        blocks=page.get_text('blocks')
        blocks=sorted((b for b in blocks if len(b)>4 and isinstance(b[4],str)),key=lambda b:(round(b[1]/12),b[0]))
        lines='\n'.join(b[4].strip() for b in blocks if b[4].strip())
        out.append(f'\n\n## Page {i+1}\n\n{lines}')
        page_info.append({'page':i+1,'characters':len(lines),'needs_ocr':len(lines)<80})
    return ''.join(out).strip()+'\n',page_info,len(doc)

def save_json(path,obj):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)

def fetch_sitemaps(session,origin,can_visit,limit=12000):
    out=[];todo=collections.deque([origin+'/sitemap.xml']);seen=set()
    while todo and len(seen)<20:
        url=todo.popleft()
        if url in seen:continue
        seen.add(url)
        try:
            r=session.get(url,timeout=(10,25));r.raise_for_status()
            if len(r.content)>8_000_000:continue
            root=ET.fromstring(r.content)
            locs=[(e.text or '').strip() for e in root.iter() if e.tag.endswith('loc')]
            for loc in locs:
                if loc.lower().endswith('.xml') and urlsplit(loc).netloc==urlsplit(origin).netloc:todo.append(loc)
                elif can_visit(loc):out.append(loc)
                if len(out)>=limit:return out
        except Exception:continue
    return out

class Crawler:
    def __init__(self,args):
        self.a=args;self.root=Path(args.output).resolve();self.root.mkdir(parents=True,exist_ok=True)
        for folder in ('originals','markdown'):(self.root/folder).mkdir(exist_ok=True)
        self.manifest_file=self.root/'manifest.json'
        self.manifest=json.loads(self.manifest_file.read_text(encoding='utf8')) if self.manifest_file.exists() else {}
        self.session=requests.Session();self.session.headers.update({'User-Agent':UA,'Accept':'text/html,application/pdf,*/*'})
        self.queue=collections.deque();self.enqueued=set();self.seen=set();self.robots={};self.last_request={};self.discoveries={};self.stats=collections.Counter()
    def scope(self,u):return allowed(u,self.a.subdomains)
    def enqueue(self,u,depth,parent):
        u=normalize(u)
        if not u or not self.scope(u):return
        if u in self.enqueued:return
        self.enqueued.add(u);self.queue.append((u,depth,parent))
        self.discoveries[u]={'depth':depth,'discovered_from':parent}
    def robots_allowed(self,url):
        p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            parser=RobotFileParser();parser.set_url(origin+'/robots.txt')
            try:parser.read();self.robots[origin]=parser
            except Exception as e:logging.warning('robots.txt check failed for %s: %s',origin,e);self.robots[origin]=None
        rp=self.robots[origin]
        return bool(rp and rp.can_fetch(UA,url))
    def throttle(self,url):
        host=urlsplit(url).netloc;elapsed=time.monotonic()-self.last_request.get(host,0)
        if elapsed<self.a.delay:time.sleep(self.a.delay-elapsed)
        self.last_request[host]=time.monotonic()
    def links(self,soup,base):
        found=[]
        for node in soup.select('a[href],iframe[src],embed[src],object[data],source[src]'):
            href=node.get('href') or node.get('src') or node.get('data')
            if not href or href.startswith(('mailto:','tel:','javascript:','data:','#')):continue
            link=normalize(urljoin(base,href))
            if link and self.scope(link):found.append({'url':link,'label':node.get_text(' ',strip=True)[:150]})
        return found
    def run(self):
        for url in SEEDS:self.enqueue(url,0,'seed')
        for url in self.a.seed:self.enqueue(url,0,'user_seed')
        if self.a.sitemaps:
            for origin in ['https://www.iiti.ac.in','https://academic.iiti.ac.in']:
                for url in fetch_sitemaps(self.session,origin,self.scope):self.enqueue(url,0,'sitemap')
        while self.queue and len(self.seen)<self.a.max_urls:
            url,depth,parent=self.queue.popleft()
            if url in self.seen:continue
            self.seen.add(url)
            rec=dict(self.manifest.get(url,{}));rec.update({'url':url,'parent':parent,'last_checked_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
            if not self.robots_allowed(url):rec['status']='blocked_robots_or_unavailable';self.complete(url,rec);continue
            try:
                headers={}
                if rec.get('etag'):headers['If-None-Match']=rec['etag']
                if rec.get('last_modified'):headers['If-Modified-Since']=rec['last_modified']
                self.throttle(url)
                with self.session.get(url,headers=headers,timeout=(12,75),stream=True,allow_redirects=True) as r:
                    rec['http_status']=r.status_code
                    if r.status_code==304:
                        rec['status']='unchanged';self.reenqueue_saved_links(rec,depth,url);self.complete(url,rec);continue
                    if not self.scope(normalize(r.url)):raise ValueError('Redirect outside approved domain')
                    r.raise_for_status();mime=r.headers.get('Content-Type','').split(';')[0].lower()
                    maxbytes=self.a.max_file_mb*1024*1024;chunks=[];count=0
                    for part in r.iter_content(131072):
                        count+=len(part)
                        if count>maxbytes:raise ValueError('File exceeds configured size limit')
                        chunks.append(part)
                    raw=b''.join(chunks);digest=hashlib.sha256(raw).hexdigest()
                    rec.update({'final_url':normalize(r.url),'bytes':len(raw),'mime':mime,'etag':r.headers.get('ETag'),'last_modified':r.headers.get('Last-Modified')})
                    if digest==rec.get('sha256'):
                        rec['status']='unchanged_content';self.reenqueue_saved_links(rec,depth,url);self.complete(url,rec);continue
                    rec['sha256']=digest
                    token=hashlib.sha256(url.encode()).hexdigest()[:24]
                    ispdf=(raw[:4]==b'%PDF' or mime=='application/pdf' or urlsplit(url).path.lower().endswith('.pdf'))
                    if ispdf:
                        original=self.root/'originals'/f'{token}.pdf';original.write_bytes(raw)
                        rec['original_path']=str(original)
                        try:
                            txt,pages,total=extract_pdf(original,self.a.max_pdf_pages)
                            markdown=self.root/'markdown'/f'{token}.md';markdown.write_text(f'# {url}\n\nSource: {url}\n\n'+txt,encoding='utf8')
                            rec.update({'text_path':str(markdown),'page_count':total,'pages_extracted':len(pages),'ocr_pages':[x['page'] for x in pages if x['needs_ocr']], 'status':'pdf_extracted' if not any(x['needs_ocr'] for x in pages) and len(pages)==total else 'pdf_partial_review_needed'})
                        except Exception as e:rec.update({'status':'pdf_extraction_failed','error':str(e)[:500]})
                    elif mime in ('text/html','application/xhtml+xml') or raw[:25].lstrip().lower().startswith((b'<!doctype html',b'<html')):
                        original=self.root/'originals'/f'{token}.html';original.write_bytes(raw);rec['original_path']=str(original)
                        soup=BeautifulSoup(raw,'html.parser');found=self.links(soup,r.url)
                        rec['links']=found
                        main=soup.find('main') or soup.find('article') or soup.find(id='main-content') or soup.body or soup
                        # Do not modify the soup used for link extraction.
                        for bad in main.select('script,style,noscript,nav,footer,aside'):bad.decompose()
                        md=content_md(main)
                        markdown=self.root/'markdown'/f'{token}.md';markdown.write_text(f'# Source: {url}\n\n'+md,encoding='utf8')
                        rec.update({'text_path':str(markdown),'title':soup.title.get_text(' ',strip=True) if soup.title else '', 'chars':len(md),'status':'html_extracted' if len(md)>80 else 'html_partial_review_needed'})
                        self.reenqueue_saved_links(rec,depth,url)
                    else:
                        suffix=Path(urlsplit(url).path).suffix.lower() or mimetypes.guess_extension(mime) or '.bin'
                        original=self.root/'originals'/f'{token}{suffix[:8]}';original.write_bytes(raw)
                        rec.update({'original_path':str(original),'status':'unsupported_document_saved'})
            except Exception as e:
                rec.update({'status':'failed','error':f'{type(e).__name__}: {str(e)[:400]}'})
            self.complete(url,rec)
        report={'started_from':'https://www.iiti.ac.in/','run_completed_utc':dt.datetime.now(dt.timezone.utc).isoformat(), 'checked_this_run':len(self.seen),'discovered_this_run':len(self.enqueued),'remaining_queue':len(self.queue),'status_counts_this_run':dict(self.stats),'status_counts_manifest':dict(collections.Counter(r.get('status') for r in self.manifest.values())),'limits':{'max_urls':self.a.max_urls,'max_depth':self.a.max_depth,'max_pdf_pages':self.a.max_pdf_pages,'max_file_mb':self.a.max_file_mb},'caveat':'No completeness guarantee: JS-only pages, robots restrictions, CAPTCHAs, authenticated content, OCR-needed scans, and limits are reported, not bypassed.'}
        save_json(self.root/'report.json',report);save_json(self.root/'discovery.json',self.discoveries)
        print(json.dumps(report,indent=2));return report
    def reenqueue_saved_links(self,rec,depth,url):
        for link in rec.get('links',[]):
            link=link['url'] if isinstance(link,dict) else link
            # PDFs may be one link beyond depth limit; discover them anyway.
            ext=Path(urlsplit(link).path).suffix.lower()
            if depth<self.a.max_depth or ext in DOC_EXT:self.enqueue(link,depth+1,url)
    def complete(self,url,rec):
        self.manifest[url]=rec;self.stats[rec['status']]+=1
        save_json(self.manifest_file,self.manifest)
        print(f"{len(self.seen):5} {rec['status']:<30} {url}",flush=True)

def main():
    p=argparse.ArgumentParser(description='IIT Indore full-site and public-subdomain crawler')
    p.add_argument('--output',default='iiti_full_crawl');p.add_argument('--max-depth',type=int,default=5)
    p.add_argument('--max-urls',type=int,default=3000);p.add_argument('--max-pdf-pages',type=int,default=500)
    p.add_argument('--max-file-mb',type=int,default=100);p.add_argument('--delay',type=float,default=1.5)
    p.add_argument('--subdomains',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--sitemaps',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--seed',action='append',default=[],help='Additional public institute seed URL; may be repeated')
    args=p.parse_args();logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s');Crawler(args).run()
if __name__=='__main__':main()
