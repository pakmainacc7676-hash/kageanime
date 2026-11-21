from flask import Flask, jsonify, request, send_from_directory
import requests, bs4, time, re
from urllib.parse import urljoin,urlparse,quote,parse_qs
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Thread, Event
import os, json, base64
import unicodedata, difflib
app=Flask(__name__,static_folder='.')
BASE='https://www.desidubanime.me/'
cache={}; index_cache={'until':0,'items':[]}; index_lock=Lock(); external_cache={'until':0,'items':[]}; external_lock=Lock()
archive_lock=Lock(); archive={'items':[], 'running':False, 'finished':0, 'total':0, 'updated':0, 'failed':0}
try:
 with open('toon_archive.json',encoding='utf-8') as f:
  saved=json.load(f);archive.update(items=saved.get('items',[]),updated=saved.get('updated',0),finished=saved.get('finished',0),total=saved.get('total',0),failed=saved.get('failed',0))
except (OSError,ValueError):pass
def soup(path):
 u=urljoin(BASE,path)
 if urlparse(u).hostname not in ('www.desidubanime.me','desidubanime.me'): raise ValueError('Invalid source')
 if u in cache and time.time()-cache[u][0]<180: return cache[u][1]
 r=requests.get(u,timeout=20,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False); r.raise_for_status()
 if r.is_redirect: raise ValueError('Source redirected')
 s=bs4.BeautifulSoup(r.text,'html.parser'); cache[u]=(time.time(),s); return s
def parse_cards(s):
 cards=[]; seen=set()
 for el in s.select('article.anime-card'):
  a=el.select_one('a[title][href*="/anime/"], a[title][href*="/watch/"]') or el.select_one('a[href*="/anime/"], a[href*="/watch/"]'); img=el.select_one('img')
  title=(a.get('title') or (img.get('alt','').replace(' poster','') if img else '')) if a else ''
  heading=el.select_one('h3 a'); names=[t.get_text(' ',strip=True) for t in heading.select('span') if t.get_text(' ',strip=True)] if heading else []
  if names: title=names[0]
  if not a or not title or a['href'] in seen: continue
  seen.add(a['href']); spans=[x.get_text(' ',strip=True) for x in el.select('span')]; badges=[x for x in spans if x in ['Hindi','Hindi DUB','Multi Audio','English','Japanese','Tamil','Telugu','Airing'] or x.endswith(' EP')]
  cards.append({'title':title,'url':a['href'],'image':img.get('src','') if img else '', 'badges':list(dict.fromkeys(badges))[:2], 'aliases':list(dict.fromkeys(names+[a.get('title','')]))})
 return cards
def total_pages(s):
 nums=[int(a.get_text(strip=True)) for a in s.select('.page-numbers') if a.get_text(strip=True).isdigit()]
 return max(nums,default=1)
def norm(text):
 text=unicodedata.normalize('NFKD',text).casefold()
 text=re.sub(r'[^a-z0-9]+',' ',text).strip()
 return text.replace('shippuuden','shippuden').replace('shipuuden','shippuden').replace('leviling','leveling').replace('levelling','leveling')
def all_catalog():
 with index_lock:
  if time.time()<index_cache['until']: return index_cache['items'], index_cache.get('complete',False)
  try: first=soup('anime/')
  except Exception:
   if index_cache['items']:
    index_cache['until']=time.time()+30
    return index_cache['items'],False
   raise
  n=total_pages(first); cards=parse_cards(first); complete=True
  def fetch(i):
   try: return parse_cards(soup('anime/page/%d/'%i))
   except Exception: return None
  with ThreadPoolExecutor(max_workers=4) as pool:
   for result in pool.map(fetch,range(2,n+1)):
    if result is None: complete=False
    else: cards.extend(result)
  if not complete and index_cache['items']:
   index_cache['until']=time.time()+30
   return index_cache['items'],False
  index_cache.update(until=time.time()+(300 if complete else 30),items=cards,complete=complete)
  return cards,complete

def external_results(q):
 result=[]; states={}
 for host in ['9anime.si','yomi.to']:
  try:
   path=('/search?q='+quote(q)) if host=='9anime.si' else '/browse'
   r=requests.get('https://'+host+path,timeout=12,headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status()
   tree=bs4.BeautifulSoup(r.text,'html.parser')
   elements=tree.select('a.anime-card-item') if host=='9anime.si' else tree.select('a.anime-card')
   for a in elements:
    img=a.select_one('img');title=(img.get('alt','').strip() if img else '')
    if not title or not a.get('href') or (host=='yomi.to' and norm(q) not in norm(title)): continue
    badges=[]
    if host=='9anime.si':
     if a.select_one('.ac-sub'): badges.append('Sub available')
     if a.select_one('.ac-dub'): badges.append('Dub available')
    result.append({'title':title,'image':urljoin('https://'+host,img.get('src','')),'url':urljoin('https://'+host,a['href']),'badges':badges,'aliases':[title],'source_name':host})
   states[host]='ok'
  except Exception: states[host]='unavailable'
 return result,states

def external_catalog():
 with external_lock:
  if time.time()<external_cache['until']: return external_cache['items'],external_cache['complete']
  def fetch9(page):
   r=requests.get('https://9anime.si/search',params={'page':page},timeout=15,headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status()
   tree=bs4.BeautifulSoup(r.text,'html.parser'); rows=[]
   for a in tree.select('a.anime-card-item'):
    img=a.select_one('img'); title=img.get('alt','').strip() if img else ''
    if not title or not a.get('href'):continue
    badges=[]
    if a.select_one('.ac-sub'):badges.append('Sub available')
    if a.select_one('.ac-dub'):badges.append('Dub available')
    rows.append({'title':title,'image':urljoin('https://9anime.si',img.get('src','')),'url':urljoin('https://9anime.si',a['href']),'badges':badges,'aliases':[title],'source_name':'9anime.si'})
   nums=[int(a.get_text(strip=True)) for a in tree.select('a[href*="page="]') if a.get_text(strip=True).isdigit()]
   return rows,max(nums,default=1)
  try:
   first,pages=fetch9(1); items=first; complete=True
   def safe_fetch(p):
    try:return fetch9(p)[0]
    except Exception:return None
   with ThreadPoolExecutor(max_workers=4) as pool:
    for rows in pool.map(safe_fetch,range(2,pages+1)):
     if rows is None:complete=False
     else:items.extend(rows)
  except Exception:items=[];complete=False
  if not complete:
   try:
    with open('external_snapshot.json',encoding='utf-8') as f: snapshot=json.load(f)
    found={x['url'] for x in items}
    items.extend(x for x in snapshot if x['url'] not in found)
   except (OSError,ValueError):pass
  try:
   yomi,_=external_results('');items.extend(x for x in yomi if x['source_name']=='yomi.to')
  except Exception:pass
  try: items.extend(toon_catalog())
  except Exception: complete=False
  external_cache.update(until=time.time()+(600 if complete else 30),items=items,complete=complete)
  return items,complete

def toon_catalog():
 r=requests.get('https://toonworld4all.me/anime-shows-list/',timeout=20,headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status()
 tree=bs4.BeautifulSoup(r.text,'html.parser');items=[];seen=set()
 for a in tree.select('article .entry-content li a[href]'):
  url=a.get('href','');host=urlparse(url).hostname
  if host not in ('toonworld4all.me','www.toonworld4all.me') or url in seen:continue
  raw=a.get_text(' ',strip=True)
  if not raw:continue
  seen.add(url)
  title=re.split(r'\s+(?:Multi Audio|Dual Audio|BluRay|Episodes|Complete Series|480p|720p|1080p|\[)',raw,flags=re.I)[0].strip(' -|:')
  if not title:continue
  m=re.search(r'\[([^]]+)\]',raw);langs=m.group(1) if m else ''
  items.append({'title':title,'image':'','url':url,'badges':[langs] if langs else [],'aliases':[title],'source_name':'ToonWorld4All'})
 return items

# Fast snapshot-first catalog: requests never wait for all third-party crawlers.
fast_catalog_lock=Lock()
fast_catalog_state={'desi':[],'external':[],'running':False,'last_attempt':0,'desi_complete':False,'external_complete':False}
try:
 with open('hindi_snapshot.json',encoding='utf-8') as f: fast_catalog_state['desi']=json.load(f)
except (OSError,ValueError):pass
try:
 with open('external_snapshot.json',encoding='utf-8') as f: fast_catalog_state['external']=json.load(f)
except (OSError,ValueError):pass

def snapshot_catalogs():
 def refresh():
  try:
   try:
    rows,complete=all_catalog()
    if rows:
     with fast_catalog_lock:fast_catalog_state.update(desi=rows,desi_complete=complete)
   except Exception:app.logger.warning('Desi catalog refresh failed; keeping snapshot')
   try:
    rows,complete=external_catalog()
    if rows:
     with fast_catalog_lock:fast_catalog_state.update(external=rows,external_complete=complete)
   except Exception:app.logger.warning('External catalog refresh failed; keeping snapshot')
  finally:
   with fast_catalog_lock:fast_catalog_state['running']=False
 with fast_catalog_lock:
  start=not fast_catalog_state['running'] and time.time()-fast_catalog_state['last_attempt']>=300
  if start:fast_catalog_state.update(running=True,last_attempt=time.time())
  # Copies protect the shared snapshot from merge_sources mutations.
  desi=[dict(x) for x in fast_catalog_state['desi']];external=[dict(x) for x in fast_catalog_state['external']]
  dc=fast_catalog_state['desi_complete'];ec=fast_catalog_state['external_complete']
 if start:Thread(target=refresh,daemon=True,name='fast-catalog-refresh').start()
 return desi,dc,external,ec

def start_toon_archive():
 with archive_lock:
  if archive['running'] or (archive['updated'] and time.time()-archive['updated']<21600): return
  archive['running']=True
 def crawl():
  entries={item['url']:item for item in archive['items']}; failures=0
  try:
   def page(p):
    url='https://toonworld4all.me/tag/anime/'+('page/%d/'%p if p>1 else '')
    r=requests.get(url,timeout=20,headers={'User-Agent':'Mozilla/5.0'});r.raise_for_status();tree=bs4.BeautifulSoup(r.text,'html.parser');rows=[]
    for el in tree.select('article'):
     a=el.select_one('h2.entry-title a');img=el.select_one('.herald-post-thumbnail img')
     if not a:continue
     raw=a.get_text(' ',strip=True);url=a.get('href','')
     if urlparse(url).hostname not in ('toonworld4all.me','www.toonworld4all.me'):continue
     title=re.split(r'\s+(?:Multi Audio|Dual Audio|BluRay|Episodes|Complete Series|480p|720p|1080p|\[)',raw,flags=re.I)[0].strip(' -|:')
     if not title:continue
     m=re.search(r'\[([^]]+)\]',raw);langs=m.group(1) if m else ''
     rows.append({'title':title,'image':img.get('src','') if img else '', 'url':url,'badges':[langs] if langs else [],'aliases':[title],'source_name':'ToonWorld4All'})
    nums=[int(x.get_text(strip=True)) for x in tree.select('.page-numbers') if x.get_text(strip=True).isdigit()]
    return rows,max(nums,default=1)
   first,total=page(1)
   for x in first:entries[x['url']]=x
   with archive_lock:archive.update(total=total,finished=1,items=list(entries.values()),failed=0)
   def safe(p):
    try:return page(p)[0]
    except Exception:return None
   with ThreadPoolExecutor(max_workers=4) as pool:
    for i,rows in enumerate(pool.map(safe,range(2,total+1)),2):
     if rows is None:failures+=1
     else:
      for x in rows:entries[x['url']]=x
     if i%5==0 or i==total:
      with archive_lock:archive.update(items=list(entries.values()),finished=i,failed=failures)
   with archive_lock:
    archive['updated']=time.time() if not failures else 0
    snapshot={k:archive[k] for k in ('items','updated','finished','total','failed')}
   with open('toon_archive.tmp','w',encoding='utf-8') as f:json.dump(snapshot,f,ensure_ascii=False)
   os.replace('toon_archive.tmp','toon_archive.json')
  except Exception:
   with archive_lock: archive['failed']+=1
  finally:
   with archive_lock:archive['running']=False
 Thread(target=crawl,daemon=True).start()

def merge_sources(items,others):
 lookup={}
 for x in items:
  x['sources']=[{'name':'DesiDubAnime','url':x['url'],'languages':'Check source'}]
  for title in [x['title']]+x.get('aliases',[]):
   if norm(title): lookup.setdefault(norm(title),x)
 for x in others:
  key=norm(x['title']); found=next((lookup[norm(name)] for name in [x['title']]+x.get('aliases',[]) if norm(name) in lookup),None)
  source={'name':x.get('source_name') or (urlparse(x.get('url','')).hostname or 'Source'),'url':x.get('url',''),'languages':', '.join(x.get('badges') or []) or 'Check source'}
  if found:
   if not any(y['name']==source['name'] for y in found['sources']): found['sources'].append(source)
  else:
   x['sources']=[source];items.append(x)
   for name in [x['title']]+x.get('aliases',[]):
    if norm(name):lookup.setdefault(norm(name),x)
 return items

@app.get('/health')
def health(): return jsonify(status='ok')
@app.get('/')
def index(): return send_from_directory('.', 'index.html')
@app.get('/black-torch.webp')
def hero(): return send_from_directory('.', 'black-torch.webp')
@app.get('/account.js')
def account_script(): return send_from_directory('.', 'account.js')
@app.get('/account.css')
def account_styles(): return send_from_directory('.', 'account.css')
@app.get('/api/auth-config')
def auth_config():
 url=os.environ.get('SUPABASE_URL','').strip().rstrip('/')
 key=os.environ.get('SUPABASE_PUBLISHABLE_KEY','').strip()
 valid=url.startswith('https://') and bool(key) and not key.startswith('sb_secret_')
 if valid and key.count('.')==2:
  try:
   part=key.split('.')[1];claims=json.loads(base64.urlsafe_b64decode(part+'='*((4-len(part)%4)%4)))
   if claims.get('role')=='service_role':valid=False
  except Exception:valid=False
 return jsonify(configured=valid,url=url if valid else '',key=key if valid else '')

anilist_cache={(1,''):(time.time(),([{'aliases': ['Attack on Titan', 'Shingeki no Kyojin', '進撃の巨人'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx16498-buvcRTBx4NSm.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=16498&ep=1&type=sub'}], 'title': 'Attack on Titan', 'url': 'https://animeflix.app/detail/?id=16498&ep=1&type=sub'}, {'aliases': ['Demon Slayer: Kimetsu no Yaiba', 'Kimetsu no Yaiba', '鬼滅の刃'], 'badges': ['26 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx101922-WBsBl0ClmgYL.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=101922&ep=1&type=sub'}], 'title': 'Demon Slayer: Kimetsu no Yaiba', 'url': 'https://animeflix.app/detail/?id=101922&ep=1&type=sub'}, {'aliases': ['JUJUTSU KAISEN', 'Jujutsu Kaisen', '呪術廻戦'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx113415-LHBAeoZDIsnF.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=113415&ep=1&type=sub'}], 'title': 'JUJUTSU KAISEN', 'url': 'https://animeflix.app/detail/?id=113415&ep=1&type=sub'}, {'aliases': ['Death Note', 'DEATH NOTE', 'DEATH NOTE'], 'badges': ['37 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1535-kUgkcrfOrkUM.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=1535&ep=1&type=sub'}], 'title': 'Death Note', 'url': 'https://animeflix.app/detail/?id=1535&ep=1&type=sub'}, {'aliases': ['My Hero Academia', 'Boku no Hero Academia', '僕のヒーローアカデミア'], 'badges': ['13 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21459-nYh85uj2Fuwr.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21459&ep=1&type=sub'}], 'title': 'My Hero Academia', 'url': 'https://animeflix.app/detail/?id=21459&ep=1&type=sub'}, {'aliases': ['Hunter x Hunter (2011)', 'HUNTER×HUNTER (2011)', 'HUNTER×HUNTER (2011)'], 'badges': ['148 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx11061-y5gsT1hoHuHw.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=11061&ep=1&type=sub'}], 'title': 'Hunter x Hunter (2011)', 'url': 'https://animeflix.app/detail/?id=11061&ep=1&type=sub'}, {'aliases': ['One-Punch Man', 'One Punch Man', 'ワンパンマン'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21087-B5DHjqZ3kW4b.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21087&ep=1&type=sub'}], 'title': 'One-Punch Man', 'url': 'https://animeflix.app/detail/?id=21087&ep=1&type=sub'}, {'aliases': ['ONE PIECE', 'ONE PIECE', 'ONE PIECE'], 'badges': [], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21-ELSYx3yMPcKM.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21&ep=1&type=sub'}], 'title': 'ONE PIECE', 'url': 'https://animeflix.app/detail/?id=21&ep=1&type=sub'}, {'aliases': ['Tokyo Ghoul', 'Tokyo Ghoul', '東京喰種 トーキョーグール'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/b20605-k665mVkSug8D.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20605&ep=1&type=sub'}], 'title': 'Tokyo Ghoul', 'url': 'https://animeflix.app/detail/?id=20605&ep=1&type=sub'}, {'aliases': ['Attack on Titan Season 2', 'Shingeki no Kyojin Season 2', '進撃の巨人 Season２'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20958-HuFJyr54Mmir.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20958&ep=1&type=sub'}], 'title': 'Attack on Titan Season 2', 'url': 'https://animeflix.app/detail/?id=20958&ep=1&type=sub'}, {'aliases': ['Fullmetal Alchemist: Brotherhood', 'Hagane no Renkinjutsushi: FULLMETAL ALCHEMIST', '鋼の錬金術師 FULLMETAL ALCHEMIST'], 'badges': ['64 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx5114-nSWCgQlmOMtj.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=5114&ep=1&type=sub'}], 'title': 'Fullmetal Alchemist: Brotherhood', 'url': 'https://animeflix.app/detail/?id=5114&ep=1&type=sub'}, {'aliases': ['Naruto', 'NARUTO', 'NARUTO -ナルト-'], 'badges': ['220 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20-dE6UHbFFg1A5.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20&ep=1&type=sub'}], 'title': 'Naruto', 'url': 'https://animeflix.app/detail/?id=20&ep=1&type=sub'}, {'aliases': ['Sword Art Online', 'Sword Art Online', 'ソードアート・オンライン'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx11757-SxYDUzdr9rh2.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=11757&ep=1&type=sub'}], 'title': 'Sword Art Online', 'url': 'https://animeflix.app/detail/?id=11757&ep=1&type=sub'}, {'aliases': ['A Silent Voice', 'Koe no Katachi', '聲の形'], 'badges': ['1 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20954-sYRfE5jQRtSB.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20954&ep=1&type=sub'}], 'title': 'A Silent Voice', 'url': 'https://animeflix.app/detail/?id=20954&ep=1&type=sub'}, {'aliases': ['Your Name.', 'Kimi no Na wa.', '君の名は。'], 'badges': ['1 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21519-SUo3ZQuCbYhJ.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21519&ep=1&type=sub'}], 'title': 'Your Name.', 'url': 'https://animeflix.app/detail/?id=21519&ep=1&type=sub'}, {'aliases': ['Attack on Titan Season 3', 'Shingeki no Kyojin Season 3', '進撃の巨人 Season３'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx99147-AiPDD8cwlCfi.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=99147&ep=1&type=sub'}], 'title': 'Attack on Titan Season 3', 'url': 'https://animeflix.app/detail/?id=99147&ep=1&type=sub'}, {'aliases': ['My Hero Academia Season 2', 'Boku no Hero Academia 2', '僕のヒーローアカデミア２'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21856-gutauxhWAwn6.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21856&ep=1&type=sub'}], 'title': 'My Hero Academia Season 2', 'url': 'https://animeflix.app/detail/?id=21856&ep=1&type=sub'}, {'aliases': ['Attack on Titan Final Season', 'Shingeki no Kyojin: The Final Season', '進撃の巨人 The Final Season'], 'badges': ['16 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx110277-sKUNXAsWMNFw.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=110277&ep=1&type=sub'}], 'title': 'Attack on Titan Final Season', 'url': 'https://animeflix.app/detail/?id=110277&ep=1&type=sub'}, {'aliases': ['Chainsaw Man', 'Chainsaw Man', 'チェンソーマン'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx127230-DdP4vAdssLoz.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=127230&ep=1&type=sub'}], 'title': 'Chainsaw Man', 'url': 'https://animeflix.app/detail/?id=127230&ep=1&type=sub'}, {'aliases': ['The Promised Neverland', 'Yakusoku no Neverland', '約束のネバーランド'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx101759-8UR7r9MNVpz2.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=101759&ep=1&type=sub'}], 'title': 'The Promised Neverland', 'url': 'https://animeflix.app/detail/?id=101759&ep=1&type=sub'}, {'aliases': ['Assassination Classroom', 'Ansatsu Kyoushitsu', '暗殺教室'], 'badges': ['22 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20755-dWrhs569YGUO.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20755&ep=1&type=sub'}], 'title': 'Assassination Classroom', 'url': 'https://animeflix.app/detail/?id=20755&ep=1&type=sub'}, {'aliases': ['Mob Psycho 100', 'Mob Psycho 100', 'モブサイコ100'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21507-6YUSbh2m0N1p.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21507&ep=1&type=sub'}], 'title': 'Mob Psycho 100', 'url': 'https://animeflix.app/detail/?id=21507&ep=1&type=sub'}, {'aliases': ['Re:ZERO -Starting Life in Another World-', 'Re:Zero kara Hajimeru Isekai Seikatsu', 'Re:ゼロから始める異世界生活'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21355-wRVUrGxpvIQQ.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21355&ep=1&type=sub'}], 'title': 'Re:ZERO -Starting Life in Another World-', 'url': 'https://animeflix.app/detail/?id=21355&ep=1&type=sub'}, {'aliases': ['Your lie in April', 'Shigatsu wa Kimi no Uso', '四月は君の嘘'], 'badges': ['22 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20665-TLgkL8T8IRFd.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20665&ep=1&type=sub'}], 'title': 'Your lie in April', 'url': 'https://animeflix.app/detail/?id=20665&ep=1&type=sub'}, {'aliases': ['Attack on Titan Season 3 Part 2', 'Shingeki no Kyojin Season 3 Part 2', '進撃の巨人 Season３ Part.2'], 'badges': ['10 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx104578-k61nx3LPjvgd.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=104578&ep=1&type=sub'}], 'title': 'Attack on Titan Season 3 Part 2', 'url': 'https://animeflix.app/detail/?id=104578&ep=1&type=sub'}, {'aliases': ['Naruto: Shippuden', 'NARUTO: Shippuuden', 'NARUTO -ナルト- 疾風伝'], 'badges': ['500 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1735-kGfVm0YqCPcu.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=1735&ep=1&type=sub'}], 'title': 'Naruto: Shippuden', 'url': 'https://animeflix.app/detail/?id=1735&ep=1&type=sub'}, {'aliases': ['ERASED', 'Boku dake ga Inai Machi', '僕だけがいない街'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21234-XmqW39aQ9o7O.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21234&ep=1&type=sub'}], 'title': 'ERASED', 'url': 'https://animeflix.app/detail/?id=21234&ep=1&type=sub'}, {'aliases': ['My Hero Academia Season 3', 'Boku no Hero Academia 3', '僕のヒーローアカデミア３'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx100166-jUCZYbzn2XLw.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=100166&ep=1&type=sub'}], 'title': 'My Hero Academia Season 3', 'url': 'https://animeflix.app/detail/?id=100166&ep=1&type=sub'}, {'aliases': ['Steins;Gate', 'Steins;Gate', 'シュタインズ・ゲート'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx9253-tIUXF2gfU8Sg.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=9253&ep=1&type=sub'}], 'title': 'Steins;Gate', 'url': 'https://animeflix.app/detail/?id=9253&ep=1&type=sub'}, {'aliases': ['Black Clover', 'Black Clover', 'ブラッククローバー'], 'badges': ['170 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx97940-fyh8o7gNbha0.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=97940&ep=1&type=sub'}], 'title': 'Black Clover', 'url': 'https://animeflix.app/detail/?id=97940&ep=1&type=sub'}, {'aliases': ['HAIKYU!!', 'Haikyuu!!', 'ハイキュー!!'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20464-ooZUyBe4ptp9.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20464&ep=1&type=sub'}], 'title': 'HAIKYU!!', 'url': 'https://animeflix.app/detail/?id=20464&ep=1&type=sub'}, {'aliases': ['Rascal Does Not Dream of Bunny Girl Senpai', 'Seishun Buta Yarou wa Bunny Girl Senpai no Yume wo Minai', '青春ブタ野郎はバニーガール先輩の夢を見ない'], 'badges': ['13 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx101291-wfEdgPqtfU0l.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=101291&ep=1&type=sub'}], 'title': 'Rascal Does Not Dream of Bunny Girl Senpai', 'url': 'https://animeflix.app/detail/?id=101291&ep=1&type=sub'}, {'aliases': ['SPY x FAMILY', 'SPY×FAMILY', 'SPY×FAMILY'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx140960-Kb6R5nYQfjmP.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=140960&ep=1&type=sub'}], 'title': 'SPY x FAMILY', 'url': 'https://animeflix.app/detail/?id=140960&ep=1&type=sub'}, {'aliases': ['Kaguya-sama: Love is War', 'Kaguya-sama wa Kokurasetai: Tensaitachi no Renai Zunousen', 'かぐや様は告らせたい～天才たちの恋愛頭脳戦～'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx101921-ufrjLzhSz7L1.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=101921&ep=1&type=sub'}], 'title': 'Kaguya-sama: Love is War', 'url': 'https://animeflix.app/detail/?id=101921&ep=1&type=sub'}, {'aliases': ['Dr. STONE', 'Dr. STONE', 'Dr.STONE'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx105333-GybuoSoOZfpH.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=105333&ep=1&type=sub'}], 'title': 'Dr. STONE', 'url': 'https://animeflix.app/detail/?id=105333&ep=1&type=sub'}, {'aliases': ['Vinland Saga', 'VINLAND SAGA', 'ヴィンランド・サガ'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx101348-2fhDFPCuMNiz.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=101348&ep=1&type=sub'}], 'title': 'Vinland Saga', 'url': 'https://animeflix.app/detail/?id=101348&ep=1&type=sub'}, {'aliases': ['Violet Evergarden', 'Violet Evergarden', 'ヴァイオレット・エヴァーガーデン'], 'badges': ['13 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21827-ubzq619ZA2E9.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21827&ep=1&type=sub'}], 'title': 'Violet Evergarden', 'url': 'https://animeflix.app/detail/?id=21827&ep=1&type=sub'}, {'aliases': ['Horimiya', 'Horimiya', 'ホリミヤ'], 'badges': ['13 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx124080-3i22mRVPBS0T.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=124080&ep=1&type=sub'}], 'title': 'Horimiya', 'url': 'https://animeflix.app/detail/?id=124080&ep=1&type=sub'}, {'aliases': ['Toradora!', 'Toradora!', 'とらドラ！'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx4224-PXVMBLNwy2aF.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=4224&ep=1&type=sub'}], 'title': 'Toradora!', 'url': 'https://animeflix.app/detail/?id=4224&ep=1&type=sub'}, {'aliases': ['No Game, No Life', 'No Game No Life', 'ノーゲーム・ノーライフ'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/b19815-sEOQ9yQaPKlk.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=19815&ep=1&type=sub'}], 'title': 'No Game, No Life', 'url': 'https://animeflix.app/detail/?id=19815&ep=1&type=sub'}, {'aliases': ['Akame ga Kill!', 'Akame ga Kill!', 'アカメが斬る！'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20613-HXHpec4bemk5.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20613&ep=1&type=sub'}], 'title': 'Akame ga Kill!', 'url': 'https://animeflix.app/detail/?id=20613&ep=1&type=sub'}, {'aliases': ['Noragami', 'Noragami', 'ノラガミ'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20447-EoQXeygHaVCK.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20447&ep=1&type=sub'}], 'title': 'Noragami', 'url': 'https://animeflix.app/detail/?id=20447&ep=1&type=sub'}, {'aliases': ['My Hero Academia Season 4', 'Boku no Hero Academia 4', '僕のヒーローアカデミア４'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx104276-SnEowMvesWIE.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=104276&ep=1&type=sub'}], 'title': 'My Hero Academia Season 4', 'url': 'https://animeflix.app/detail/?id=104276&ep=1&type=sub'}, {'aliases': ['The Seven Deadly Sins', 'Nanatsu no Taizai', '七つの大罪'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20789-Ma5ouSYPkru9.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20789&ep=1&type=sub'}], 'title': 'The Seven Deadly Sins', 'url': 'https://animeflix.app/detail/?id=20789&ep=1&type=sub'}, {'aliases': ['Bleach', 'BLEACH', 'BLEACH'], 'badges': ['366 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx269-d2GmRkJbMopq.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=269&ep=1&type=sub'}], 'title': 'Bleach', 'url': 'https://animeflix.app/detail/?id=269&ep=1&type=sub'}, {'aliases': ["KONOSUBA -God's blessing on this wonderful world!", 'Kono Subarashii Sekai ni Shukufuku wo!', 'この素晴らしい世界に祝福を！'], 'badges': ['10 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21202-mPOr80AEjUcZ.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21202&ep=1&type=sub'}], 'title': "KONOSUBA -God's blessing on this wonderful world!", 'url': 'https://animeflix.app/detail/?id=21202&ep=1&type=sub'}, {'aliases': ['Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train', 'Kimetsu no Yaiba: Mugen Ressha-hen', '鬼滅の刃 無限列車編'], 'badges': ['1 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx112151-1qlQwPB1RrJe.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=112151&ep=1&type=sub'}], 'title': 'Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train', 'url': 'https://animeflix.app/detail/?id=112151&ep=1&type=sub'}, {'aliases': ['Code Geass: Lelouch of the Rebellion', 'Code Geass: Hangyaku no Lelouch', 'コードギアス 反逆のルルーシュ'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1575-hsmWM2ydNm1m.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=1575&ep=1&type=sub'}], 'title': 'Code Geass: Lelouch of the Rebellion', 'url': 'https://animeflix.app/detail/?id=1575&ep=1&type=sub'}, {'aliases': ['DARLING in the FRANXX', 'Darling in the Franxx', 'ダーリン・イン・ザ・フランキス'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/nx99423-8MBxtwCeHf8B.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=99423&ep=1&type=sub'}], 'title': 'DARLING in the FRANXX', 'url': 'https://animeflix.app/detail/?id=99423&ep=1&type=sub'}, {'aliases': ['Parasyte -the maxim-', 'Kiseijuu: Sei no Kakuritsu', '寄生獣 セイの格率'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20623-dUARfggnNDOe.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20623&ep=1&type=sub'}], 'title': 'Parasyte -the maxim-', 'url': 'https://animeflix.app/detail/?id=20623&ep=1&type=sub'}],100))}
def animeflix_list(page=1,q=''):
 key=(page,q.casefold()); old=anilist_cache.get(key)
 if old and time.time()-old[0]<600:return old[1]
 query="""query ($page:Int,$search:String){Page(page:$page,perPage:50){pageInfo{total currentPage hasNextPage} media(type:ANIME,sort:POPULARITY_DESC,search:$search){id title{english romaji native} coverImage{large} episodes status}}}"""
 try:
  r=requests.post('https://graphql.anilist.co',json={'query':query,'variables':{'page':page,'search':q or None}},timeout=15);r.raise_for_status();body=r.json()
  if body.get('errors') or not body.get('data'):raise ValueError('AniList request failed')
 except Exception:
  if old:return old[1] # use the last successful result while the source is unavailable
  raise
 pg=body['data']['Page'];rows=[]
 for m in pg['media']:
  names=[v for v in (m.get('title') or {}).values() if v];id=m.get('id')
  if not id or not names:continue
  title=names[0]; url='https://animeflix.app/detail/?id='+str(id)+'&ep=1&type=sub'
  rows.append({'title':title,'url':url,'image':(m.get('coverImage') or {}).get('large') or '','badges':([str(m['episodes'])+' EP'] if m.get('episodes') else []),'aliases':names,'source_name':'AnimeFlix'})
 result=(rows,max(1,(min(pg['pageInfo'].get('total',0),5000)+49)//50));anilist_cache[key]=(time.time(),result);return result
@app.get('/api/animeflix')
def animeflix_endpoint():
 try:
  p=request.args.get('page','1');page=max(1,min(int(p),100)) if p.isdigit() else 1;q=request.args.get('q','').strip()[:100]
  rows,pages=animeflix_list(page,q)
  for x in rows:x['sources']=[{'name':'AnimeFlix','url':x['url'],'languages':'Check source'}]
  return jsonify(items=rows,pages=pages)
 except Exception:return jsonify(error='AnimeFlix catalog is temporarily unavailable.'),502
@app.get('/api/list')
def listing():
 try:
  q=request.args.get('q','').strip(); lang=request.args.get('lang','').lower(); p=request.args.get('page','1'); page=max(1,min(int(p),1000)) if p.isdigit() else 1
  if lang not in ['', 'hindi','english','japanese','tamil','telugu','bengali','malayalam','kannada']: lang=''
  if lang and not q:
   path='tag/'+lang+'/'+('page/'+str(page)+'/' if page>1 else '')
   tree=soup(path); cards=parse_cards(tree);pages=total_pages(tree);complete=True
  else:
   start_toon_archive()
   desi,desi_complete,others,other_complete=snapshot_catalogs()
   with archive_lock: archive_items=list(archive['items']); archive_state={k:archive[k] for k in ('running','finished','total','failed')}
   by_url={x['url']:dict(x) for x in others}
   for x in archive_items:by_url[x['url']]=dict(x)
   animeflix_pages=1
   try:
    extra,animeflix_pages=animeflix_list(1,q)
    for x in extra:by_url[x['url']]=dict(x)
   except Exception:pass
   all_items=merge_sources([dict(x) for x in desi],list(by_url.values()));complete=desi_complete and other_complete and not archive_state['running'] and not archive_state['failed']
   if q:
    matching=[x for x in all_items if any(norm(q) in norm(name) for name in [x['title']]+x.get('aliases',[]))]
    if not matching and len(norm(q))>4: matching=[x for x in all_items if any(difflib.SequenceMatcher(None,norm(q),norm(name)).ratio()>=.84 for name in [x['title']]+x.get('aliases',[]))]
   else: matching=all_items
   base_pages=max(1,(len(matching)+27)//28)
   if not q:
    pages=base_pages+max(0,animeflix_pages-1)
    if page<=base_pages:cards=matching[(page-1)*28:page*28]
    else:
     ext_page=page-base_pages+1
     try:ext,_=animeflix_list(ext_page)
     except Exception:
      app.logger.warning('Catalog page %s unavailable from AniList',ext_page)
      return jsonify(error='More anime are temporarily unavailable. Please retry.',retryable=True),503
     existing={norm(n) for item in all_items for n in [item['title']]+item.get('aliases',[]) if n}
     cards=[]
     for item in ext:
      names=[norm(n) for n in [item['title']]+item.get('aliases',[]) if n]
      if any(name in existing for name in names):continue
      existing.update(names);item=dict(item);item['sources']=[{'name':'AnimeFlix','url':item['url'],'languages':'Check source'}];cards.append(item)
   else:
    pages=base_pages;cards=matching[(page-1)*28:page*28]
  return jsonify(items=cards,source=BASE,pages=pages,complete=complete,archive=archive_state if not (lang and not q) else None)
 except Exception:
  app.logger.exception('Catalog listing failed')
  return jsonify(error='The anime catalog is temporarily unavailable. Please retry.',retryable=True),503
@app.get('/api/status')
def status():
 with archive_lock: return jsonify({k:archive[k] for k in ('running','finished','total','failed')})
episode_counts={}
@app.get('/api/episode-count')
def episode_count():
 urls=[request.args.get('url',''),request.args.get('alt','')]
 for url in urls:
  host=urlparse(url).hostname or ''
  if host not in ('www.desidubanime.me','desidubanime.me','9anime.si','www.9anime.si') or '/watch/' not in urlparse(url).path:continue
  try:
   if url in episode_counts and time.time()-episode_counts[url][0]<300: count=episode_counts[url][1]
   else:
    r=requests.get(url,timeout=15,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status();tree=bs4.BeautifulSoup(r.text,'html.parser')
    if '9anime.si' in host:
     count=len({a.get('data-ep') for a in tree.select('a.sidebar-ep-pill[data-ep]') if a.get('data-ep','').isdigit()})
    else:
     count=len({a.get('href') for a in tree.select('a[href*="/watch/"]') if re.match(r'^\d+\s',a.get_text(' ',strip=True))})
    episode_counts[url]=(time.time(),count)
   if count:return jsonify(count=count)
  except Exception:continue
 return jsonify(count=None)
# DesiDubAnime's Hindi-tagged catalog: a source label, not proof that every
# episode/player actually has a working Hindi audio track.
hindi_lock=Lock(); hindi_cache={'items':[], 'until':0}
try:
 with open('hindi_snapshot.json',encoding='utf-8') as f:hindi_cache['items']=json.load(f)
 hindi_cache['until']=time.time()+300
except (OSError,ValueError):pass
def hindi_catalog():
 with hindi_lock:
  if time.time()<hindi_cache['until'] and hindi_cache['items']:return hindi_cache['items']
  try:
   first=soup('tag/hindi/');total=total_pages(first)
   def fetch(i):return parse_cards(soup('tag/hindi/'+('page/%d/'%i if i>1 else '')))
   with ThreadPoolExecutor(max_workers=4) as pool:pages=list(pool.map(fetch,range(1,total+1)))
   items=[x for page in pages for x in page]
   if items:hindi_cache.update(items=items,until=time.time()+600)
  except Exception:
   hindi_cache['until']=time.time()+60
  return hindi_cache['items']
def hindi_match(item,rows):
 names={norm(v) for v in [item.get('title','')]+item.get('aliases',[]) if v}
 if 'hunter x hunter 2011' in names:names.add('hunter x hunter')
 candidates=[r for r in rows if names.intersection(norm(v) for v in [r.get('title','')]+r.get('aliases',[]) if v)]
 candidates.sort(key=lambda r:('/watch/' not in r.get('url',''), '0 EP' in r.get('badges',[])))
 return candidates[0] if candidates else None
def prefer_hindi(item,rows):
 match=hindi_match(item,rows)
 item=dict(item)
 if match:
  item['url']=match['url'];item['badges']=['Hindi listed']+[b for b in match['badges'] if b.endswith(' EP') and b!='0 EP'];item['hindi_listed']=True
 else:item['hindi_listed']=False
 return item
LEGEND_IDS=[223,813,21175,20,1735,21,269,101922,16498,113415,21459,127230,1535,5114,11061,21087,1575,97940]
feature_cache={'until':0,'items':[]}
@app.get('/api/featured')
def featured():
 try:
  if time.time()<feature_cache['until']:return jsonify(items=feature_cache['items'])
  query='query($ids:[Int]){Page(page:1,perPage:50){media(id_in:$ids,type:ANIME){id title{english romaji} coverImage{large} episodes}}}'
  r=requests.post('https://graphql.anilist.co',json={'query':query,'variables':{'ids':LEGEND_IDS}},timeout=20);r.raise_for_status();data={x['id']:x for x in r.json()['data']['Page']['media']};items=[]
  for i in LEGEND_IDS:
   x=data.get(i)
   if not x:continue
   title=x['title'].get('english') or x['title'].get('romaji');url='https://animeflix.app/detail/?id=%d&ep=1&type=sub'%i
   items.append({'title':title,'url':url,'image':(x.get('coverImage') or {}).get('large',''),'badges':[str(x['episodes'])+' EP'] if x.get('episodes') else []})
  items=[prefer_hindi(x,hindi_catalog()) for x in items]
  for item in items:
   if not item['hindi_listed']:item['badges']=['Hindi not confirmed']+item.get('badges',[])
  feature_cache.update(until=time.time()+600,items=items);return jsonify(items=items)
 except Exception:
  return jsonify(items=feature_cache['items'])
@app.get('/api/related')
def related():
 try:
  q=request.args.get('q','').strip()[:100]
  if not q:return jsonify(items=[])
  rows,_=animeflix_list(1,q)
  hindi=hindi_catalog();items=[prefer_hindi(x,hindi) for x in rows[:35]]
  known={x['url'] for x in items}
  # Include source-tagged Hindi seasons even when their English arc titles differ.
  for x in hindi:
   if norm(q) in norm(x['title']) and x['url'] not in known:
    y=dict(x);y['hindi_listed']=True;y['badges']=['Hindi listed']+[b for b in x.get('badges',[]) if b.endswith(' EP') and b!='0 EP'];items.append(y);known.add(x['url'])
  return jsonify(items=items)
 except Exception:return jsonify(items=[],error='Related titles are temporarily unavailable.')
@app.get('/api/cover')
def find_cover():
 try:
  q=request.args.get('q','').strip()[:100]
  if not q:return jsonify(image='')
  rows,_=animeflix_list(1,q)
  return jsonify(image=rows[0]['image'] if rows and difflib.SequenceMatcher(None,norm(q),norm(rows[0]['title'])).ratio()>=.72 else '')
 except Exception:return jsonify(image='')
@app.get('/watch')
def watch_page(): return send_from_directory('.', 'watch.html')
@app.get('/api/image')
def fetch_poster():
 try:
  url=request.args.get('url','');host=urlparse(url).hostname
  if host not in ('toonworld4all.me','www.toonworld4all.me'):return jsonify(image=''),400
  r=requests.get(url,timeout=12,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status()
  t=bs4.BeautifulSoup(r.text,'html.parser');im=t.select_one('article .entry-content img[src]') or t.select_one('meta[property="og:image"]')
  image=im.get('src',im.get('content','')) if im else ''
  return jsonify(image=image if image.startswith('https://') else '')
 except Exception:return jsonify(image='')
def extract_player_urls(tree, base):
 frames=[]
 for video in tree.select('video[src], video source[src]'):
  url=urljoin(base,video.get('src',''))
  if urlparse(url).scheme=='https' and re.search(r'\.(?:mp4|webm|ogv)(?:$|[?#])',url,re.I) and url not in frames:frames.append(url)
 for iframe in tree.select('iframe[src]'):
  url=urljoin(base,iframe.get('src',''))
  if urlparse(url).scheme=='https' and url not in frames:frames.append(url)
 return frames

@app.get('/api/watch')
def watch():
 try:
  path=request.args.get('url',''); host=urlparse(path).hostname
  if host in ('animeflix.app','www.animeflix.app'):
   params=parse_qs(urlparse(path).query);aid=(params.get('id') or [''])[0];ep=(params.get('ep') or ['1'])[0]
   if not aid.isdigit() or not ep.isdigit():raise ValueError('Invalid episode')
   query="query($id:Int){Media(id:$id,type:ANIME){title{english romaji} episodes status nextAiringEpisode{episode}}}"
   try:
    r=requests.post('https://graphql.anilist.co',json={'query':query,'variables':{'id':int(aid)}},timeout=15);r.raise_for_status();media=r.json().get('data',{}).get('Media')
    if not media:raise ValueError('Anime not found')
   except Exception:
    # No invented episode counts: keep only the selected episode when metadata is offline.
    frames=['https://animeembed.com/embed/ani/'+aid+'/'+ep+'?track=sub','https://aniembed.se/e/'+aid+'/'+ep+'?lang=sub&autoplay=0','https://vidnest.fun/anime/'+aid+'/'+ep+'/sub']
    return jsonify(title='Anime',frames=frames,episodes=[{'title':ep,'url':path}],source=path,metadata_unavailable=True)
   n=media.get('episodes') or 0
   if media.get('status')=='RELEASING' and media.get('nextAiringEpisode'):n=min(n or 9999,max(0,media['nextAiringEpisode']['episode']-1))
   eps=[{'title':str(i),'url':'https://animeflix.app/detail/?id='+aid+'&ep='+str(i)+'&type=sub'} for i in range(1,min(n,1500)+1)]
   frames=['https://animeembed.com/embed/ani/'+aid+'/'+ep+'?track=sub','https://aniembed.se/e/'+aid+'/'+ep+'?lang=sub&autoplay=0','https://vidnest.fun/anime/'+aid+'/'+ep+'/sub']
   return jsonify(title=media['title'].get('english') or media['title'].get('romaji') or 'Anime',frames=frames,episodes=eps,source=path)
  if host in ('9anime.si','www.9anime.si'):
   r=requests.get(path,timeout=20,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status()
   tree=bs4.BeautifulSoup(r.text,'html.parser');eps=[]
   for a in tree.select('a.sidebar-ep-pill[data-ep]'):
    num=a.get('data-ep','')
    if num.isdigit():eps.append({'title':num,'url':urljoin('https://9anime.si',a.get('href',''))})
   frames=extract_player_urls(tree,path)
   title=tree.title.get_text(' ',strip=True) if tree.title else 'Anime'
   return jsonify(title=title,frames=frames,episodes=eps,source=path)
  if host in ('toonworld4all.me','www.toonworld4all.me'):
   r=requests.get(path,timeout=20,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status()
   tree=bs4.BeautifulSoup(r.text,'html.parser');eps=[]; seen=set()
   for a in tree.select('article .entry-content a[href]'):
    href=a.get('href',''); m=re.search(r'/episode/[^/]+-(\d+)x(\d+)',href)
    if m and urlparse(href).hostname=='archive.toonworld4all.me' and href not in seen:eps.append({'title':m.group(2),'url':href});seen.add(href)
   h=tree.select_one('h1.entry-title');return jsonify(title=h.get_text(' ',strip=True) if h else 'Anime',frames=[],episodes=eps,source=path,reason='This source does not provide a directly embeddable player.')
  if host=='archive.toonworld4all.me':
   r=requests.get(path,timeout=20,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status();tree=bs4.BeautifulSoup(r.text,'html.parser');frames=extract_player_urls(tree,path)
   return jsonify(title=tree.title.get_text(' ',strip=True) if tree.title else 'Episode',frames=frames,episodes=[],source=path,reason='This source does not provide a directly embeddable player.')
  s=soup(path); title=s.title.get_text(' ',strip=True).split(' - ')[0] if s.title else 'Episode'; eps=[]; seen=set()
  is_anime='/anime/' in urlparse(path).path
  for a in s.select('a[href*="/watch/"]'):
   t=a.get_text(' ',strip=True); href=a.get('href','')
   if is_anime:
    if not a.get('title') or not re.search(r'episode\s*\d+',a.get('title',''),re.I): continue
    t=a.get('title')
   elif not (t and len(t)<100 and t[0].isdigit()): continue
   if href not in seen: eps.append({'title':t,'url':href});seen.add(href)
  if is_anime and eps:
   # Select the first available episode, preserving the full show list.
   def epnum(e):
    m=re.search(r'episode\s*(\d+)',e['title'],re.I);return int(m.group(1)) if m else 999999
   eps.sort(key=epnum); path=eps[0]['url']; s=soup(path); title=s.title.get_text(' ',strip=True).split(' - ')[0] if s.title else title
  if is_anime and not eps:
   return jsonify(title=title,frames=[],episodes=[],source=path)
  frames=extract_player_urls(s,path)
  return jsonify(title=title,frames=frames,episodes=eps,source=path)
 except Exception:
  app.logger.exception('Watch source failed')
  return jsonify(error='This source is temporarily unavailable. Try Refresh or another source.'),502
# Refresh the DesiDubAnime index while the server is running; requests also
# refresh expired caches on demand, so visitors don't need a redeployment.
def auto_refresh_desi():
 while not Event().wait(300):
  try: all_catalog()
  except Exception: app.logger.exception('Automatic DesiDubAnime refresh failed; next attempt will retry')
Thread(target=auto_refresh_desi,daemon=True,name='desi-catalog-refresh').start()

# MovieBox's public pages provide discovery metadata; they do not grant us a video stream.
moviebox_cache={}
@app.get('/api/moviebox-catalog')
def moviebox_catalog():
 kind=request.args.get('kind','animation')
 if kind not in ('animation','movies'):return jsonify(error='Unknown catalog'),400
 old=moviebox_cache.get(kind)
 if old and time.time()-old['time']<600:return jsonify(items=old['items'],source='MovieBox',playback='not provided')
 try:
  path='/web/animated-series' if kind=='animation' else '/web/movie'
  r=requests.get('https://movie-box.co'+path,timeout=18,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status()
  if r.is_redirect:raise ValueError('Redirect not accepted')
  tree=bs4.BeautifulSoup(r.text,'html.parser');node=tree.select_one('#__NUXT_DATA__')
  data=json.loads(node.string)
  def val(obj,key,default=''):
   ix=obj.get(key);return data[ix] if isinstance(ix,int) and 0<=ix<len(data) else default
  rows=[];seen=set()
  for x in data:
   if not isinstance(x,dict) or not all(k in x for k in ('subjectId','subjectType','title','detailPath','cover')):continue
   title=val(x,'title');cover=val(x,'cover',{});image=val(cover,'url') if isinstance(cover,dict) else ''
   typ=val(x,'subjectType');sid=val(x,'subjectId')
   if not isinstance(title,str) or not title or sid in seen or (kind=='movies' and typ!=1):continue
   seen.add(sid);rows.append({'title':title,'image':image if isinstance(image,str) and image.startswith('https://') else '', 'type':'Movie' if typ==1 else 'Series','year':str(val(x,'releaseDate'))[:4], 'rating':str(val(x,'imdbRatingValue')), 'catalog_source':'MovieBox'})
  if not rows:raise ValueError('No catalog entries')
  moviebox_cache[kind]={'time':time.time(),'items':rows[:100]}
  return jsonify(items=rows[:100],source='MovieBox',playback='not provided')
 except Exception:
  if old:return jsonify(items=old['items'],source='MovieBox',playback='not provided',stale=True)
  return jsonify(error='MovieBox catalog is temporarily unavailable.'),502

# Rankings are a discovery aid, not a guarantee that a title is playable.
rank_lock=Lock()
rank_state={'data':{'anime': [{'aliases': ['Attack on Titan', 'Shingeki no Kyojin', '進撃の巨人'], 'badges': ['25 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx16498-buvcRTBx4NSm.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=16498&ep=1&type=sub'}], 'title': 'Attack on Titan', 'url': 'https://animeflix.app/detail/?id=16498&ep=1&type=sub'}, {'aliases': ['Demon Slayer: Kimetsu no Yaiba', 'Kimetsu no Yaiba', '鬼滅の刃'], 'badges': ['26 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx101922-WBsBl0ClmgYL.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=101922&ep=1&type=sub'}], 'title': 'Demon Slayer: Kimetsu no Yaiba', 'url': 'https://animeflix.app/detail/?id=101922&ep=1&type=sub'}, {'aliases': ['JUJUTSU KAISEN', 'Jujutsu Kaisen', '呪術廻戦'], 'badges': ['24 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx113415-LHBAeoZDIsnF.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=113415&ep=1&type=sub'}], 'title': 'JUJUTSU KAISEN', 'url': 'https://animeflix.app/detail/?id=113415&ep=1&type=sub'}, {'aliases': ['Death Note', 'DEATH NOTE', 'DEATH NOTE'], 'badges': ['37 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx1535-kUgkcrfOrkUM.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=1535&ep=1&type=sub'}], 'title': 'Death Note', 'url': 'https://animeflix.app/detail/?id=1535&ep=1&type=sub'}, {'aliases': ['My Hero Academia', 'Boku no Hero Academia', '僕のヒーローアカデミア'], 'badges': ['13 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21459-nYh85uj2Fuwr.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21459&ep=1&type=sub'}], 'title': 'My Hero Academia', 'url': 'https://animeflix.app/detail/?id=21459&ep=1&type=sub'}, {'aliases': ['Hunter x Hunter (2011)', 'HUNTER×HUNTER (2011)', 'HUNTER×HUNTER (2011)'], 'badges': ['148 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx11061-y5gsT1hoHuHw.png', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=11061&ep=1&type=sub'}], 'title': 'Hunter x Hunter (2011)', 'url': 'https://animeflix.app/detail/?id=11061&ep=1&type=sub'}, {'aliases': ['One-Punch Man', 'One Punch Man', 'ワンパンマン'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21087-B5DHjqZ3kW4b.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21087&ep=1&type=sub'}], 'title': 'One-Punch Man', 'url': 'https://animeflix.app/detail/?id=21087&ep=1&type=sub'}, {'aliases': ['ONE PIECE', 'ONE PIECE', 'ONE PIECE'], 'badges': [], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx21-ELSYx3yMPcKM.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=21&ep=1&type=sub'}], 'title': 'ONE PIECE', 'url': 'https://animeflix.app/detail/?id=21&ep=1&type=sub'}, {'aliases': ['Tokyo Ghoul', 'Tokyo Ghoul', '東京喰種 トーキョーグール'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/b20605-k665mVkSug8D.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20605&ep=1&type=sub'}], 'title': 'Tokyo Ghoul', 'url': 'https://animeflix.app/detail/?id=20605&ep=1&type=sub'}, {'aliases': ['Attack on Titan Season 2', 'Shingeki no Kyojin Season 2', '進撃の巨人 Season２'], 'badges': ['12 EP'], 'image': 'https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/bx20958-HuFJyr54Mmir.jpg', 'source_name': 'AnimeFlix', 'sources': [{'languages': 'Check source', 'name': 'AnimeFlix', 'url': 'https://animeflix.app/detail/?id=20958&ep=1&type=sub'}], 'title': 'Attack on Titan Season 2', 'url': 'https://animeflix.app/detail/?id=20958&ep=1&type=sub'}], 'movies': [{'title': 'The Shawshank Redemption', 'image': 'https://pbcdn.aoneroom.com/image/2026/01/27/774df57711d123d0e9610ba622ae8a6d.jpg', 'type': 'Movie', 'year': '1994', 'rating': '9.3', 'rating_count': 3150021, 'catalog_source': 'MovieBox'}, {'title': 'The Dark Knight', 'image': 'https://pbcdn.aoneroom.com/image/2026/01/27/03a483058245baca96fe8ecd075ea99c.jpg', 'type': 'Movie', 'year': '2008', 'rating': '9.1', 'rating_count': 3125478, 'catalog_source': 'MovieBox'}, {'title': 'Inception', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/ca253305a555591c771c8122e2cfd051.jpg', 'type': 'Movie', 'year': '2010', 'rating': '8.8', 'rating_count': 2779532, 'catalog_source': 'MovieBox'}, {'title': 'Fight Club', 'image': 'https://pbcdn.aoneroom.com/image/2026/01/27/bac20df00771267d251c638e8cf479ad.jpg', 'type': 'Movie', 'year': '1999', 'rating': '8.8', 'rating_count': 2565915, 'catalog_source': 'MovieBox'}, {'title': 'Forrest Gump', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/f3ecbb70044f52a61a7539ca0e3be0fe.jpg', 'type': 'Movie', 'year': '1994', 'rating': '8.8', 'rating_count': 2464726, 'catalog_source': 'MovieBox'}, {'title': 'Pulp Fiction', 'image': 'https://pbcdn.aoneroom.com/image/2026/01/27/cf3c0e6815c92713fb0279e710ce6fd3.jpg', 'type': 'Movie', 'year': '1994', 'rating': '8.8', 'rating_count': 2407450, 'catalog_source': 'MovieBox'}, {'title': 'The Godfather', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/75702ebb628a0016c31a6671b5901a87.jpg', 'type': 'Movie', 'year': '1972', 'rating': '9.2', 'rating_count': 2198203, 'catalog_source': 'MovieBox'}, {'title': 'The Lord of the Rings: The Return of the King', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/12bdb78083dc03a1e7fbdcd4790e8116.jpg', 'type': 'Movie', 'year': '2003', 'rating': '9.0', 'rating_count': 2139533, 'catalog_source': 'MovieBox'}, {'title': 'Joker', 'image': 'https://pbcdn.aoneroom.com/image/2026/01/26/850324eb26b7d8aded5f955be9b7d289.jpg', 'type': 'Movie', 'year': '2019', 'rating': '8.3', 'rating_count': 1669545, 'catalog_source': 'MovieBox'}, {'title': "Schindler's List", 'image': 'https://pbcdn.aoneroom.com/image/2026/01/27/f871a976a8a0867272b9b396d5575e96.jpg', 'type': 'Movie', 'year': '1994', 'rating': '9.0', 'rating_count': 1570373, 'catalog_source': 'MovieBox'}], 'anime_source': 'AniList popularity', 'movie_source': 'Most-rated in sampled MovieBox listings'},'running':False,'last_attempt':0}
def _refresh_rankings():
 try:
  updated={}
  try:
   rows,_=animeflix_list(1,'')
   if rows:updated['anime']=rows[:10]
  except Exception:pass
  try:
   r=requests.get('https://movie-box.co/web/movie',timeout=18,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status()
   if r.is_redirect:raise ValueError('Redirect not accepted')
   data=json.loads(bs4.BeautifulSoup(r.text,'html.parser').select_one('#__NUXT_DATA__').string)
   def val(o,k,default=''):
    i=o.get(k);return data[i] if isinstance(i,int) and 0<=i<len(data) else default
   movies=[];seen=set()
   for x in data:
    if not isinstance(x,dict) or not all(k in x for k in ('subjectId','subjectType','title','cover','detailPath')) or val(x,'subjectType')!=1:continue
    title=val(x,'title');cover=val(x,'cover',{});image=val(cover,'url') if isinstance(cover,dict) else ''
    if not isinstance(title,str) or not title or title.casefold().strip() in seen:continue
    seen.add(title.casefold().strip())
    try: count=int(str(val(x,'imdbRatingCount',0)).replace(',',''))
    except (ValueError,TypeError):count=0
    movies.append({'title':title,'image':image if isinstance(image,str) and image.startswith('https://') else '', 'type':'Movie','year':str(val(x,'releaseDate'))[:4],'rating':str(val(x,'imdbRatingValue')),'rating_count':count,'catalog_source':'MovieBox'})
   if movies:updated['movies']=sorted(movies,key=lambda x:x['rating_count'],reverse=True)[:10]
  except Exception:pass
  if updated:
   with rank_lock:rank_state['data']={**rank_state['data'],**updated}
 finally:
  with rank_lock:rank_state['running']=False
@app.get('/api/rankings')
def rankings_endpoint():
 with rank_lock:
  start=not rank_state['running'] and time.time()-rank_state['last_attempt']>=600
  if start:rank_state.update(running=True,last_attempt=time.time())
  data=rank_state['data']
 if start:Thread(target=_refresh_rankings,daemon=True,name='rankings-refresh').start()
 return jsonify(data)




# NetMirror catalog snapshot. Entries are discovery metadata, not verified streams.
netmirror_state={'items':[{'title': 'Drishyam 3: The Conclusion [Hindi]', 'url': 'https://netmirror.plus/movie/125943', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/8f4fe39e5eb44d0eaaa95e418c73c37b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}, {'title': 'Jailer 2 [Hindi]', 'url': 'https://netmirror.plus/movie/126294', 'image': 'https://m.media-amazon.com/images/M/MV5BNTlkN2IzM2UtZmVhMy00ZmE1LThjOWMtNWE2ZGQ1ZWM3ZmE3XkEyXkFqcGc@._V1_FMjpg_UY300_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Gajab Raat [Hindi]', 'url': 'https://netmirror.plus/movie/126491', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/21/49dba48603a592aa204377bfaae96afa.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Udta Teer [Hindi]', 'url': 'https://netmirror.plus/movie/126488', 'image': 'https://imgs.search.brave.com/f5o6NFevA866cNdWAXm_S9JTXMIyN8wXI8FsZRL6ZKk/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9jZG4u/c2Fjbmlsay5jb20v/aW1hZ2UvbW92aWUv/MjAyNi8xNDMxMi5q/cGc_dj03MDQ', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Tum Mere Ho [Hindi]', 'url': 'https://netmirror.plus/movie/126500', 'image': 'https://m.media-amazon.com/images/M/MV5BZjg0MTZjNmUtNzBjZi00Zjk2LWE0MGItZWIwODJmODY3NTQ3XkEyXkFqcGc@._V1_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.2'}, {'title': '418 [Hindi]', 'url': 'https://netmirror.plus/movie/126501', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/26/b103b9869bf5862cc3b883338301bc47.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}, {'title': 'Street Fighter [Hindi]', 'url': 'https://netmirror.plus/movie/126398', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/12/324ac3d0904fe0a08bf4bf604d4c2ae3.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Nayyi Navelli [Hindi]', 'url': 'https://netmirror.plus/movie/126296', 'image': 'https://imgs.search.brave.com/Llv8QURfyPuZm22QiZMuUaxazTl45Jjn7m__PL_R9NE/rs:fit:500:0:1:0/g:ce/aHR0cHM6Ly9pbWcu/cmdzdGF0aWMuY29t/L2NvbnRlbnQvbW92/aWUvZmM3ODhiM2Et/MDBmNC00NGY2LTk3/NmYtMGRmZDQ4ZWYw/ZTY2L3Bvc3Rlci0z/NDIuanBn', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'The Punisher: One Last Kill [Hindi]', 'url': 'https://netmirror.plus/movie/126908', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/31/e304d39df8fd6d1ccc884ac3de988dc1.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}, {'title': 'Dilwali Diwali [Hindi]', 'url': 'https://netmirror.plus/movie/126899', 'image': 'https://pbcdnw.aoneroom.com/image/2026/11/03/fda1051913086a8790a6128894f1112b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': "I'm Game [Hindi]", 'url': 'https://netmirror.plus/movie/123907', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/10/46e38f57162939eda699b3055ecf425d.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Resident Evil [Hindi]', 'url': 'https://netmirror.plus/movie/124287', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/14/007d17f3449fe5d5379f8fdb185d5b31.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Anakapalli [Hindi]', 'url': 'https://netmirror.plus/movie/126778', 'image': 'https://m.media-amazon.com/images/M/MV5BNjkzNmM3MGYtMTY3Ni00OGIyLThiYzktYjQ2MmY0YjhiNmM4XkEyXkFqcGc@._V1_FMjpg_UX600_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.6'}, {'title': 'Immortal [Hindi]', 'url': 'https://netmirror.plus/movie/126777', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/07/1ee5fb785b46b38d3ded1af80b9685b7.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.9'}, {'title': 'Jhaad Phoonk', 'url': 'https://netmirror.plus/movie/126775', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/30/46ba5d47b258ee49d2f452c9dd2d0c35.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Mirzapur: The Movie', 'url': 'https://netmirror.plus/movie/123295', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/12/0e797569d01944f388aefcc5888e3995.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Apocalypse Z: New World [Hindi]', 'url': 'https://netmirror.plus/movie/126771', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/27/f148bcdc2d3b121df478e7eabf08ba65.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Nakhrewaalii [Hindi]', 'url': 'https://netmirror.plus/movie/126758', 'image': 'https://m.media-amazon.com/images/M/MV5BZWMzZmNhN2QtOGYxYi00MDJkLTk5NDQtMDkzZWM0OTExYWM5XkEyXkFqcGc@._V1_FMjpg_UY600_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Meethe Mein Kya Hai [Hindi]', 'url': 'https://netmirror.plus/movie/126676', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/28/2f274af6f90cdd893a3513463b693b15.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Little Thomas [Hindi]', 'url': 'https://netmirror.plus/movie/126561', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/25/165eb384ace6f854c8e066268d81923a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.8'}, {'title': 'Runner [Hindi]', 'url': 'https://netmirror.plus/movie/126670', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/24/ded94aef0b9a9709b1a488fb3e96923e.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Legend of the White Dragon [Hindi]', 'url': 'https://netmirror.plus/movie/126499', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/01/8ded48936fa22974496c66637c13d235.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.9'}, {'title': 'Prahaar: The Untold Story of Ujjwal Nikam [Hindi]', 'url': 'https://netmirror.plus/movie/126326', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/12/739a0a7179bf3b124b0d3f8008283f6d.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Doraemon: Nobita and the New Castle of the Undersea Devil [Hindi]', 'url': 'https://netmirror.plus/movie/126231', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/6de79bbb377fe7debf49feeaec1f9761.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}, {'title': 'Spider-Man: Brand New Day [Hindi]', 'url': 'https://netmirror.plus/movie/122804', 'image': 'https://image.tmdb.org/t/p/w342/iPOn6DinuVyLY17YM9mKuPofV08.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.4'}, {'title': 'Mandaadi [Hindi]', 'url': 'https://netmirror.plus/movie/126119', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/20/4337b23cb45a0ae0bee216bab438e727.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '9.2'}, {'title': 'Maremma [Hindi]', 'url': 'https://netmirror.plus/movie/126171', 'image': 'https://pbcdnw.aoneroom.com/image/2026/10/09/90f2eef1ae5e90a7d561dd87999e55e2.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.7'}, {'title': 'Toxic [Hindi]', 'url': 'https://netmirror.plus/movie/123467', 'image': 'https://m.media-amazon.com/images/M/MV5BMGM5MjQ3Y2QtNDFmYS00NjEzLTllY2EtNzQ2MmE3YmRkN2MxXkEyXkFqcGc@._V1_FMjpg_UY300_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.2'}, {'title': 'Khalifa [Hindi]', 'url': 'https://netmirror.plus/movie/126125', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/10/110b16b46a461ce3f25a57219538b2d3.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': "The Butcher's Blade [Hindi]", 'url': 'https://netmirror.plus/movie/126126', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/6082b6d44ef88459e2d4a77613018aac.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Vadala [Hindi]', 'url': 'https://netmirror.plus/movie/126063', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/16/7108971ba824db07dcd948e4882c05aa.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Sardar 2 [Hindi]', 'url': 'https://netmirror.plus/movie/125838', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/08/9f6c5efbd34f2e0a8899d7a7171efb56.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Insidious: Out of the Further [Hindi]', 'url': 'https://netmirror.plus/movie/123337', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/18/1823a73b41ca8c0968eb483fad450733.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Bethlehem Kudumba Unit [Hindi]', 'url': 'https://netmirror.plus/movie/125854', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/29/826dd9728ac02a75f398b9c030f6b656.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.3'}, {'title': 'The Love Hypothesis [Hindi]', 'url': 'https://netmirror.plus/movie/124335', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/21/d15c1de6f88a9bd24e3532a93086147a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'The Paradise [Hindi]', 'url': 'https://netmirror.plus/movie/124402', 'image': 'https://image.tmdb.org/t/p/w300/nwfbqtbFbgqQuMfAtkesuCVHJjq.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Hanuman Ansh [Hindi]', 'url': 'https://netmirror.plus/movie/123098', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/31/ddd3007c421b02c6a96f6f0a331e441e.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'Vishwanath and Sons [Hindi]', 'url': 'https://netmirror.plus/movie/124045', 'image': 'https://pbcdn.aoneroom.com/image/2026/08/23/487fa023df2258cdd11efab3e7b6e785.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.5'}, {'title': 'Hokum [Hindi]', 'url': 'https://netmirror.plus/movie/125872', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/28/f6b47d39018deaaf9c39d5efa3c1f59a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.0'}, {'title': 'Prem Keetanu [Hindi]', 'url': 'https://netmirror.plus/movie/126012', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/23/ee75399146a2a4abd02d5c2603e28073.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Shree Ramayan Katha [Hindi]', 'url': 'https://netmirror.plus/movie/125822', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/25/a79870dbec6de731f2943e5789be29f8.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Bombay Stories [Hindi]', 'url': 'https://netmirror.plus/movie/125832', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/08/1a57e91cfce0fca14d7c0018928564e0.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Love Lottery [Hindi]', 'url': 'https://netmirror.plus/movie/125828', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/09/d9939cb1e145aa0fd02ef3a5c9f9b113.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'This Tempting Madness [Hindi]', 'url': 'https://netmirror.plus/movie/125834', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/01/f7e7f69912e97d9b37ea0f1fe24ee8e1.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.5'}, {'title': 'Mango Pachcha [Hindi]', 'url': 'https://netmirror.plus/movie/124409', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/31/e2911e0a6afa0cc0cc9bb2ccdcbf2730.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.5'}, {'title': 'Lust Stories 3 [Hindi]', 'url': 'https://netmirror.plus/movie/124243', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/16/5222c5a855ada9fc365d4e06d75f669c.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Irumudi [Hindi]', 'url': 'https://netmirror.plus/movie/124231', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/16/815997e9c00cd5cf48357c195e4e3056.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.3'}, {'title': 'Zakir Khan: Papa Yaar [Hindi]', 'url': 'https://netmirror.plus/movie/124229', 'image': 'https://imgshare.info/images/2026/09/18/Zakir-Khan-Papa-Yaar.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.8'}, {'title': 'Last Man in Tower', 'url': 'https://netmirror.plus/movie/124067', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/09/351df22101406b1e1747df54b81ba06a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'The Runner [Hindi]', 'url': 'https://netmirror.plus/movie/123739', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/27/cca59929eb029e9f825db78b2ff5135e.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Awarapan 2 [Hindi]', 'url': 'https://netmirror.plus/movie/123115', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/12/31544ffed75dbb0888cdeb9f9f4af7be.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Gandhari [Hindi]', 'url': 'https://netmirror.plus/movie/123753', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/03/2c4018c2164acab95411aa849c7e2e3d.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Nagabandham - The Secret Treasure [Hindi]', 'url': 'https://netmirror.plus/movie/122768', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/29/b8d8a4ff837a49a71424e4954883f36f.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Korean Kanakaraju [Hindi]', 'url': 'https://netmirror.plus/movie/123802', 'image': 'https://pbcdnw.aoneroom.com/image/2026/09/02/0d4c7493efe23a7c65fdca574049102a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.7'}, {'title': 'The Last Sunrise [Hindi]', 'url': 'https://netmirror.plus/movie/123488', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/24/f1c9816931d7d52e25e91bbb74fef62d.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Mutiny [Hindi]', 'url': 'https://netmirror.plus/movie/123737', 'image': 'https://themagnificent60s.com/wp-content/uploads/2026/08/mutiny-3.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Cocktail 2 [Hindi]', 'url': 'https://netmirror.plus/movie/112234', 'image': 'https://image.tmdb.org/t/p/w342/oIQmtByV1LtEQSwM4EpdLTyoSlM.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.4'}, {'title': 'Welcome to the Jungle [Hindi]', 'url': 'https://netmirror.plus/movie/112423', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/22/ee805e50c92e36ae03b2bee7510cca50.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Batwara 1947 [Hindi]', 'url': 'https://netmirror.plus/movie/123163', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/17/aa0f8f2a1af52891e0bfa763bcbd9f34.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '4.7'}, {'title': 'Toy Story 5 [Hindi]', 'url': 'https://netmirror.plus/movie/122179', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/16/503cde6da9515420e0e34a6ce16775fa.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'In the Grey [Hindi]', 'url': 'https://netmirror.plus/movie/110514', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/09/98578574f9e2c57716c9c2a53fc94ca7.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.3'}, {'title': 'DC [Hindi]', 'url': 'https://netmirror.plus/movie/123083', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/04/c18e57285580b66fe4a0f351c7b19e82.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Main Vaapas Aaunga [Hindi]', 'url': 'https://netmirror.plus/movie/111681', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/09/a1afbe10f295a456bbf804f9f41e62fd.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Bharat Bhhagya Viddhaata [Hindi]', 'url': 'https://netmirror.plus/movie/111671', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/09/7dff3c3bbad796158ad2d85e5f1ae059.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Gatta Kusthi 2 [Hindi]', 'url': 'https://netmirror.plus/movie/122878', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/18/a2d6c3345bee25df56d489c6e213c94a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.4'}, {'title': 'Spider-Man: Brand New Day [English]', 'url': 'https://netmirror.plus/movie/122801', 'image': 'https://image.tmdb.org/t/p/w342/iPOn6DinuVyLY17YM9mKuPofV08.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.4'}, {'title': 'Ohh My Dog [Hindi]', 'url': 'https://netmirror.plus/movie/123075', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/08/8490a41432e3e3c41df38b45b66cd0e8.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '9.1'}, {'title': 'Dil Deewana Ho Gaya [Hindi]', 'url': 'https://netmirror.plus/movie/122905', 'image': 'https://pbcdnw.aoneroom.com/image/2026/08/03/4b325603d45ac27963d6b9df615ec0bf.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.6'}, {'title': 'Uyir [Hindi]', 'url': 'https://netmirror.plus/movie/122907', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/22/9b00c4ad6286fbf00118e636be92d360.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Ghost Train [Hindi]', 'url': 'https://netmirror.plus/movie/122793', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/d16e189526c555f2743f411d82cf1af6.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.2'}, {'title': 'Demon Slayer -Kimetsu no Yaiba- The Movie: Infinity Castle [Hindi]', 'url': 'https://netmirror.plus/movie/96601', 'image': 'https://pacdn.aoneroom.com/image/2025/09/15/dbea18b424bfd7018af50d07acd693f5.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.7'}, {'title': 'Jana Nayagan [Hindi]', 'url': 'https://netmirror.plus/movie/122541', 'image': 'https://pbcdn.aoneroom.com/image/2026/07/23/6bb25d52f5f43c8b7dc37303988110dd.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': "The Devil's Mouth [Hindi]", 'url': 'https://netmirror.plus/movie/122800', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/23/d7f64c152cbd3e7653444e575c1b359a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Dhamaal 4 [Hindi]', 'url': 'https://netmirror.plus/movie/110373', 'image': 'https://imgshare.info/images/2026/07/10/Dhamaal-4-2026Dhamaal-4-2026.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Avatar Aang: The Last Airbender [Hindi]', 'url': 'https://netmirror.plus/movie/122712', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/25/b24785ffae4ab45cef3c38889ba38a84.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'The Odyssey [Hindi]', 'url': 'https://netmirror.plus/movie/122542', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/19/7776e3f365295d954bdef61a258db60f.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.4'}, {'title': 'The India Story [Hindi]', 'url': 'https://netmirror.plus/movie/122764', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/16/4f02899719c3a045e4ec556479255e97.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Dulhaniya Le Aaeegi [Hindi]', 'url': 'https://netmirror.plus/movie/122680', 'image': 'https://image.tmdb.org/t/p/w342/kQBgp1eYyQVs4nKtd8lrLva2JcQ.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '9.5'}, {'title': 'Double Occupancy [Hindi]', 'url': 'https://netmirror.plus/movie/122547', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/15/3ff1092b56fff4227f9f4663907fbf00.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.7'}, {'title': 'Scary Movie [Hindi]', 'url': 'https://netmirror.plus/movie/122548', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/12/58f894ce049c7855ba9969ca9185d2a5.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.2'}, {'title': 'Obsession [Hindi]', 'url': 'https://netmirror.plus/movie/122009', 'image': 'https://pbcdnw.aoneroom.com/image/2025/11/03/c82b3442e823435d8e3835bac884bd10.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.1'}, {'title': 'Ikka [Hindi]', 'url': 'https://netmirror.plus/movie/122168', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/29/58a4ba460f2e91500079e03c88a2c7e5.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.2'}, {'title': 'Peddi [Hindi]', 'url': 'https://netmirror.plus/movie/111377', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/01/a2f78edd08aabb9a90a1db258eba8139.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Parimala and Co [Hindi]', 'url': 'https://netmirror.plus/movie/122182', 'image': 'https://pbcdnw.aoneroom.com/image/2026/07/10/1ec1113e3b8c2a12ce2db7d94bb90fa2.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.9'}, {'title': 'Evil Dead Burn [Hindi]', 'url': 'https://netmirror.plus/movie/122095', 'image': 'https://pbcdn.aoneroom.com/image/2026/08/27/036636ed9d173eab6150b25b1a390169.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Wuthering Heights [Hindi]', 'url': 'https://netmirror.plus/movie/122312', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/14/f58c85e78e903c226552068e15ea670d.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Supergirl [Hindi]', 'url': 'https://netmirror.plus/movie/112402', 'image': 'https://image.tmdb.org/t/p/w342/niSvU02l2BONH9ivubV6K1a5QiK.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Enola Holmes 3 [Hindi]', 'url': 'https://netmirror.plus/movie/122003', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/29/63f924dc46774eb0bf5e36ab185e2662.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'French Girl [Hindi]', 'url': 'https://netmirror.plus/movie/122020', 'image': 'https://pacdn.aoneroom.com/image/2024/05/04/761fe4a97541586899d518896935e058.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Karakkam [Hindi]', 'url': 'https://netmirror.plus/movie/122008', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/26/b6aee1e2df461e9f8d4abaea3236d8be.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Tavvai', 'url': 'https://netmirror.plus/movie/113676', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/29/d514e70da32d034b20a97646e69b6337.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '0.0'}, {'title': 'Little Brother [Hindi]', 'url': 'https://netmirror.plus/movie/112413', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/25/f5bf90fc52b318a2d4c2b4182e9afcd8.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Voicemails for Isabelle [Hindi]', 'url': 'https://netmirror.plus/movie/112237', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/16/8ee584c7e787b4bda67895584bd3f25b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Obsession', 'url': 'https://netmirror.plus/movie/110283', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/13/fe06dcafd65eba559ea7875d41496641.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.7'}, {'title': 'Your Fault: London [Hindi]', 'url': 'https://netmirror.plus/movie/111725', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/22/65e16db6e8bc669167bc56b99c2d654b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Avatar: Fire and Ash [Hindi]', 'url': 'https://netmirror.plus/movie/100856', 'image': 'https://pbcdnw.aoneroom.com/image/2025/12/19/e263aa76bbe35f442dd9769ce8566cea.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Masters of the Universe [Hindi]', 'url': 'https://netmirror.plus/movie/111556', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/8a4e2dd1db60d7d2a3e9754d8f91db10.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}, {'title': 'Office Romance [Hindi]', 'url': 'https://netmirror.plus/movie/111433', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/22/df954872d885cb662863000e24fc3c5f.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.2'}, {'title': 'Bhooth Bangla [Hindi]', 'url': 'https://netmirror.plus/movie/109360', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/24/1e48f069e61000bb1fe556a921f56579.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Haunted 3D: Echoes of the Past', 'url': 'https://netmirror.plus/movie/111684', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/14/30ede29849b14c2a781deb7266a25918.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '3.0'}, {'title': 'Mortal Kombat II [Hindi]', 'url': 'https://netmirror.plus/movie/110007', 'image': 'https://image.tmdb.org/t/p/w342/lIsMeDbwntNXSUVHmWMMRXEZOVc.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Suriya 45 (Karuppu) [Hindi]', 'url': 'https://netmirror.plus/movie/111480', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/7add0f45f3b25e784928ce36e5f338c3.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Governor: The Silent Saviour [Hindi]', 'url': 'https://netmirror.plus/movie/111668', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/09/66d8f4230dff74ca433faab27df87e09.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': '29 [Hindi]', 'url': 'https://netmirror.plus/movie/111375', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/11/b2bfacafc696e8714f9904cd1e2639af.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.9'}, {'title': 'My Dearest Assassin [Hindi]', 'url': 'https://netmirror.plus/movie/111436', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/06/f63b3f0d6f357bf84cf2400c6ebf4d78.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.7'}, {'title': 'Dhurandhar: The Revenge [Hindi][HD][Web}', 'url': 'https://netmirror.plus/movie/106885', 'image': 'https://m.media-amazon.com/images/M/MV5BNTU1Zjk4MTYtMjc2YS00NjI5LWJkOWEtMjJlOGQ0ZTVjNmFjXkEyXkFqcGc@._V1_FMjpg_UY400_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.7'}, {'title': 'Hai Jawani Toh Ishq Hona Hai [Hindi]', 'url': 'https://netmirror.plus/movie/111430', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/03/1fa39a5601df94a73b84acc7291c87af.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Monkey in a Cage', 'url': 'https://netmirror.plus/movie/110560', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/27/0d0697b7dcd5d6408a9d2ddc6631d8d7.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.7'}, {'title': 'México 86 [Hindi]', 'url': 'https://netmirror.plus/movie/111432', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/03/daebc9055f51c1d7bdb9163c11051f8e.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Patriot [Hindi]', 'url': 'https://netmirror.plus/movie/111376', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/15/f0540c7adc71825d5b5987d92fb0b118.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'Maa Behen [Hindi]', 'url': 'https://netmirror.plus/movie/111345', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/22/18ad12fcd3ac4fed989de71d11d075dd.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'One Two Cha Cha Chaa [Hindi]', 'url': 'https://netmirror.plus/movie/102863', 'image': 'https://pbcdnw.aoneroom.com/image/2025/12/10/f81a8fc0aff93cb39533f3b54ecbaac5.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.3'}, {'title': 'Rajni Ki Baraat [Hindi]', 'url': 'https://netmirror.plus/movie/111124', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/01/d5e6eaa2a426173c82b699381eaff93c.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Hoppers [Hindi]', 'url': 'https://netmirror.plus/movie/107746', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/be42e692f74cc75db15f079b584eb67b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.2'}, {'title': 'Scary Movie', 'url': 'https://netmirror.plus/movie/111343', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/03/5ca80ab82eefcf2374fe9c759b5e733b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.2'}, {'title': 'Obsess [Hindi]', 'url': 'https://netmirror.plus/movie/111126', 'image': 'https://pbcdnw.aoneroom.com/image/2026/06/01/5dbd4890a00b6bda23913e9d4c393692.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '9.2'}, {'title': 'Kattalan [Hindi]', 'url': 'https://netmirror.plus/movie/110590', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/19/c92b10c1dceb8dead5ff394e2221ddcc.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Kara [Hindi]', 'url': 'https://netmirror.plus/movie/110605', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/30/a711b503b4e9b49b3f713b8eec314bb9.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Chand Mera Dil [Hindi]', 'url': 'https://netmirror.plus/movie/110494', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/19/2be21bfc876d9ea9bfaa1818a5d6e647.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'The Great Grand Superhero: Aliens Ka Aagman [Hindi]', 'url': 'https://netmirror.plus/movie/110587', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/26/d59fe0c28fc463a97435ebdc35ac2a3b.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'System [Hindi]', 'url': 'https://netmirror.plus/movie/110379', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/19/db3cb888c8895aa2263a462904c5760a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}, {'title': 'Star Wars: The Mandalorian and Grogu [Hindi]', 'url': 'https://netmirror.plus/movie/110506', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/ff97b22cdfbb169168e440955d5fd3d8.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Indian Institute of Zombies [Hindi]', 'url': 'https://netmirror.plus/movie/110232', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/08/693b86db216e6d62567584e1ab778690.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Aakhri Sawal [Hindi]', 'url': 'https://netmirror.plus/movie/110245', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/06/bf29a529d7d5fa7563bd3d9b9318f18f.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Kaalidas 2 [Hindi]', 'url': 'https://netmirror.plus/movie/110145', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/04/14599c4c2d899836d7951e96fd5a2135.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.1'}, {'title': 'Daadi Ki Shaadi [Hindi]', 'url': 'https://netmirror.plus/movie/110040', 'image': 'https://pbcdnw.aoneroom.com/image/2026/05/06/3e1a1bcb8993c6a07f442cf9659973d2.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Ek Din [Hindi]', 'url': 'https://netmirror.plus/movie/109917', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/29/b9a768827835391a98d017658555c780.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.2'}, {'title': 'Biker [Hindi]', 'url': 'https://netmirror.plus/movie/109812', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/29/c718d874e0e053cff2b2c5912c95862a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Happy Raj [Hindi]', 'url': 'https://netmirror.plus/movie/109813', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/21/ca7fc64c912567ba99234ea42ed6f043.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Ginny Wedss Sunny 2 [Hindi]', 'url': 'https://netmirror.plus/movie/109526', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/26/3c71cac8a9285a68713f157d1927ce87.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Nee Forever [Hindi]', 'url': 'https://netmirror.plus/movie/109444', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/30/b3d229cd01824bf8e77d741ac467867f.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.6'}, {'title': 'Apex [Hindi]', 'url': 'https://netmirror.plus/movie/109450', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/11/8f031abb45e71ad5a4ccec8a3842bb76.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Pallichattambi [Hindi]', 'url': 'https://netmirror.plus/movie/109362', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/24/6c4acb0e3ad9de9170ebfeb423fcbfb0.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'Ustaad Bhagat Singh [Hindi]', 'url': 'https://netmirror.plus/movie/109310', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/28/ed2f18d3b8c4d805fe9bdadfb4031222.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '0.0'}, {'title': 'Toaster [Hindi]', 'url': 'https://netmirror.plus/movie/109087', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/15/c373b9d8e4ba66f0d8aada7a054a86f4.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Tu Yaa Main [Hindi]', 'url': 'https://netmirror.plus/movie/105353', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/15d151f4968052fff543bd060945aac8.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'Everybody Loves Sohrab Handa', 'url': 'https://netmirror.plus/movie/109016', 'image': 'https://pbcdnw.aoneroom.com/image/2026/04/10/9bdd5123b8b690f4ea8ec8ffbaeea2ab-s.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Thaai Kizhavi [Hindi]', 'url': 'https://netmirror.plus/movie/109012', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/04/954fe73cc3808631a85303699d890c19.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.3'}, {'title': 'Vadh 2 [Hindi]', 'url': 'https://netmirror.plus/movie/104787', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/dac0db70dbfc093756cb854e965cb2a2.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Do Not Enter [Hindi]', 'url': 'https://netmirror.plus/movie/99906', 'image': 'https://pbcdnw.aoneroom.com/image/2025/11/19/67c2f86d13c0d34a7b1e2f0a5933a4fe.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Bhabiji Ghar Par Hain - Fun on the Run [Hindi]', 'url': 'https://netmirror.plus/movie/104776', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/06/8935612e5a87df8723138da3360239b6.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.0'}, {'title': 'Scream 7 [Hindi]', 'url': 'https://netmirror.plus/movie/108066', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/ee16334eb82deebaf3de1c7dfa3b4e89.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.6'}, {'title': 'BTS: The Return [Hindi]', 'url': 'https://netmirror.plus/movie/107631', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/27/b3261e0f3b6bd34da242a14feff1045a.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Project Hail Mary [Hindi]', 'url': 'https://netmirror.plus/movie/107745', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/c9fa3caac2be4d5b410f5be15099bf16.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Mardaani 3 [Hindi]', 'url': 'https://netmirror.plus/movie/102105', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/29/db4f71d0d1e48c09328c1edb3464f6aa.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Ishqan De Lekhe', 'url': 'https://netmirror.plus/movie/106604', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/12/03a23f1e961fec5f7949aa977d90e124.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Dhurandhar: The Revenge [Tamil][Hd][Web]', 'url': 'https://netmirror.plus/movie/106964', 'image': 'https://m.media-amazon.com/images/M/MV5BNTU1Zjk4MTYtMjc2YS00NjI5LWJkOWEtMjJlOGQ0ZTVjNmFjXkEyXkFqcGc@._V1_FMjpg_UY400_.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.7'}, {'title': 'Border 2 [Hindi]', 'url': 'https://netmirror.plus/movie/101731', 'image': 'https://pbcdnw.aoneroom.com/image/2025/11/03/a24ed824b5c89e94f9e8d6e58a5cc60d.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Peaky Blinders: The Immortal Man [Hindi]', 'url': 'https://netmirror.plus/movie/106942', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/be37ab9e993e6856a92567c8b72a0bc0.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'O Romeo [Hindi]', 'url': 'https://netmirror.plus/movie/105349', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/804f962772bbe260d810b3514adecf1f.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'War Machine [Hindi]', 'url': 'https://netmirror.plus/movie/106446', 'image': 'https://image.tmdb.org/t/p/w342/rFhKkXhk7ClU03jQ5rHIApJDwev.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'One Mile: Chapter Two [Hindi]', 'url': 'https://netmirror.plus/movie/106928', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/21/c6a6b4c959b65a57fe73d0dab474ac48.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.2'}, {'title': 'The Kerala Story 2 Goes Beyond', 'url': 'https://netmirror.plus/movie/106043', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/18/8560182769007006ccceeedcf47723a0.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'Made in Korea [Hindi]', 'url': 'https://netmirror.plus/movie/106657', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/12/c5decc6f9b5b54b54d0879a86e5ccd82.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.2'}, {'title': 'Subedaar [Hindi]', 'url': 'https://netmirror.plus/movie/106259', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/11/4ac2313b7d578d9ad64040cdf68391cf.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'Zootopia 2 [Hindi]', 'url': 'https://netmirror.plus/movie/100223', 'image': 'https://pbcdnw.aoneroom.com/image/2025/10/05/7717a167d54ebff54c2048701e8d6ce2.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Akaal: The Unconquered [Hindi]', 'url': 'https://netmirror.plus/movie/88553', 'image': 'https://image.tmdb.org/t/p/w185/pzf5rPLoE5DdYkcZlvYbrH554rE.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.2'}, {'title': 'Dust Bunny [Hindi]', 'url': 'https://netmirror.plus/movie/106718', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/eba285b6732a211a742732e43a5be728.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.6'}, {'title': 'The Comeback [Hindi]', 'url': 'https://netmirror.plus/movie/106633', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/cee1d86be9007903de9223c327ba4320.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.9'}, {'title': 'Bloody Babu [Hindi]', 'url': 'https://netmirror.plus/movie/106338', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/02/e0d1f4580144784b04acb65d05e82a37.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.5'}, {'title': 'Charak – Fair of Faith [Hindi]', 'url': 'https://netmirror.plus/movie/106610', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/09/a9ad51ca83611601f7d5a1743eb7408a.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.9'}, {'title': 'Na Jaane Kaun Aa Gaya [Hindi]', 'url': 'https://netmirror.plus/movie/106616', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/28/3dd1770f7c9ca28b200260bf08b7589c.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Nukkad Naatak [Hindi]', 'url': 'https://netmirror.plus/movie/106074', 'image': 'https://pbcdnw.aoneroom.com/image/2026/03/02/73ddd93a9d45239045c510be45dca707.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '8.6'}, {'title': 'The Raja Saab [Hindi]', 'url': 'https://netmirror.plus/movie/101143', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/09/8d9905dad0421d8c819a3431154bb07c.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Xeno [Hindi]', 'url': 'https://netmirror.plus/movie/105896', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/0eb13f50b51df32994e85736918b7f95.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '5.1'}, {'title': 'The Bluff [Hindi]', 'url': 'https://netmirror.plus/movie/105790', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/eed57f272a1ec3ad450108fc215ecddf.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Honey [Hindi]', 'url': 'https://netmirror.plus/movie/105917', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/09/8c491b5d4a4226c0dfb4d4ced8cfbc66.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.9'}, {'title': 'Assi [Hindi]', 'url': 'https://netmirror.plus/movie/105647', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/10/1e2b70b6e1e40ed82f4cd9a0754beb11.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.8'}, {'title': 'Retta Thala [Hindi]', 'url': 'https://netmirror.plus/movie/105635', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/8c29a3613c88d9f77bf3d4855946edf3.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '4.4'}, {'title': 'The Strangers: Chapter 2 [Hindi]', 'url': 'https://netmirror.plus/movie/97701', 'image': 'https://pacdn.aoneroom.com/image/2025/10/05/68507baf1791c8d5ce6a54a6982549a9.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '4.9'}, {'title': 'Shelter [Hindi]', 'url': 'https://netmirror.plus/movie/105894', 'image': 'https://pbcdnw.aoneroom.com/image/2025/12/03/eded1063155ffe11d9f611eafc0b78b5.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Shatak Sangh Ke 100 Varsh [Hindi]', 'url': 'https://netmirror.plus/movie/105648', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/21/bd2df83a8c75f759084edd57d31f4a39.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.1'}, {'title': 'Do Deewane Seher Mein [Hindi]', 'url': 'https://netmirror.plus/movie/105652', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/31/4bdb714a625c6ef1a4407b572c55cf16.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.1'}, {'title': 'Crime 101 [Hindi]', 'url': 'https://netmirror.plus/movie/105609', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/7da6a05e3935bd0948c9153debac2f08.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.4'}, {'title': 'Chatha Pacha[Hindi]', 'url': 'https://netmirror.plus/movie/105634', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/27/77f9e99084bd38829cfaac0cbfe19821.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.2'}, {'title': 'Vanaveera [Hindi]', 'url': 'https://netmirror.plus/movie/105318', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/13/09d5ea770fd7d08cf771652587f35704.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.7'}, {'title': 'Zorr [Hindi]', 'url': 'https://netmirror.plus/movie/105109', 'image': 'https://pbcdnw.aoneroom.com/image/2026/02/10/4b3158eff4da26739ce7ced94da30dfe.jpeg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Mana ShankaraVaraprasad Garu [Hindi]', 'url': 'https://netmirror.plus/movie/105116', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/78c96a31de527ee840d88616654b11af.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '6.3'}, {'title': 'Anaganaga Oka Raju [Hindi]', 'url': 'https://netmirror.plus/movie/105115', 'image': 'https://pbcdnw.aoneroom.com/image/2026/01/26/6bea3e043c743ded01033430eaae4650.jpg', 'badges': ['Movie'], 'source_name': 'NetMirror', 'type': 'Movie', 'rating': '7.0'}],'updated':0,'running':False}
def refresh_netmirror_movies():
 try:
  rows=[];seen=set()
  for page in range(20):
   try:
    r=requests.get('https://api2.imdb3.shop/api/tranding',params={'page':page,'id':11,'items_per_page':14,'cache':'home'},headers={'User-Agent':'Mozilla/5.0','Origin':'https://netmirror.plus','Referer':'https://netmirror.plus/'},timeout=10);r.raise_for_status()
    for x in r.json().get('results',[]):
     id=str(x.get('id',''));title=str(x.get('title','')).strip();image=x.get('backdrop_path','')
     if x.get('media_type')!='movie' or not id.isdigit() or id in seen or not title or not image.startswith('https://'):continue
     seen.add(id);rows.append({'title':title,'url':'https://netmirror.plus/movie/'+id,'image':image,'badges':['Movie'],'source_name':'NetMirror','type':'Movie','rating':str(x.get('vote_average') or '')})
   except Exception:app.logger.exception('NetMirror catalog refresh failed')
  if rows:netmirror_state.update(items=rows,updated=time.time())
 finally:netmirror_state['running']=False
@app.get('/api/netmirror-movies')
def netmirror_movies():
 if not netmirror_state['running'] and time.time()-netmirror_state['updated']>300:
  netmirror_state['running']=True;Thread(target=refresh_netmirror_movies,daemon=True).start()
 return jsonify(items=netmirror_state['items'],source='NetMirror',updated=netmirror_state['updated'])

if __name__=='__main__': app.run(host='0.0.0.0',port=8000)
