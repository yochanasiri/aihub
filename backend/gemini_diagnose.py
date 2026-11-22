"""Run inside backend container. Never prints the key or private chat history."""
import os
import socket
import httpx

BASE = 'https://generativelanguage.googleapis.com/v1beta'
key = os.getenv('GEMINI_API_KEY', '').strip()
model = os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite').removeprefix('models/')
print('Model:', model, '| Key configured:', bool(key), '| Search enabled:', os.getenv('GEMINI_GOOGLE_SEARCH'))
if not key:
    raise SystemExit('GEMINI_API_KEY is missing')
try:
    socket.getaddrinfo('generativelanguage.googleapis.com', 443)
    print('DNS: OK')
except OSError as exc:
    print('DNS failed:', type(exc).__name__)
    raise SystemExit(1)
headers = {'x-goog-api-key': key}
with httpx.Client(timeout=httpx.Timeout(150, connect=20)) as client:
    try:
        r = client.get(BASE + '/models/' + model, headers=headers)
        print('Model lookup HTTP:', r.status_code)
        if r.status_code != 200:
            print(r.text[:1000].replace(key, '[redacted]'))
            raise SystemExit(1)
        for name, question, tools in [
            ('Plain chat', 'Say hello in one sentence.', []),
            ('Google search', 'Use Google Search to find the latest news from the official IIT Indore website. Give one source.', [{'google_search': {}}]),
            ('URL context', 'Read https://www.iiti.ac.in/ and summarize the page briefly.', [{'url_context': {}}]),
        ]:
            body = {'contents':[{'parts':[{'text':question}]}], 'generationConfig':{'maxOutputTokens':8192}}
            if model == 'gemini-3.8-flash': body['generationConfig']['thinkingConfig'] = {'thinkingLevel':'LOW'}
            if tools: body['tools'] = tools
            r = client.post(BASE+'/models/'+model+':generateContent', headers=headers, json=body)
            print(name, 'HTTP:', r.status_code)
            if r.status_code == 200:
                candidate = (r.json().get('candidates') or [{}])[0]
                sources = (candidate.get('groundingMetadata') or {}).get('groundingChunks', [])
                url_meta = (candidate.get('urlContextMetadata') or {}).get('urlMetadata', [])
                print('Finish:', candidate.get('finishReason'), '| search sources:', len(sources), '| URL results:', len(url_meta))
            else:
                print(r.text[:1000].replace(key, '[redacted]'))
    except httpx.RequestError as exc:
        print('Transport failure:', type(exc).__name__, '| Check container network, DNS, TLS and proxy.')
        raise SystemExit(1)
