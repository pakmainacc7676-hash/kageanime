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
  source={'name':x['source_name'],'url':x['url'],'languages':', '.join(x['badges']) or 'Check source'}
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

anilist_cache={}
def animeflix_list(page=1,q=''):
 key=(page,q.casefold()); old=anilist_cache.get(key)
 if old and time.time()-old[0]<600:return old[1]
 query="""query ($page:Int,$search:String){Page(page:$page,perPage:50){pageInfo{total currentPage hasNextPage} media(type:ANIME,sort:POPULARITY_DESC,search:$search){id title{english romaji native} coverImage{large} episodes status}}}"""
 r=requests.post('https://graphql.anilist.co',json={'query':query,'variables':{'page':page,'search':q or None}},timeout=15);r.raise_for_status();body=r.json()
 if body.get('errors') or not body.get('data'):raise ValueError('AniList request failed')
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
   desi,desi_complete=all_catalog(); others,other_complete=external_catalog()
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
     ext_page=page-base_pages+1;ext,_=animeflix_list(ext_page)
     existing={norm(n) for item in all_items for n in [item['title']]+item.get('aliases',[]) if n}
     cards=[]
     for item in ext:
      names=[norm(n) for n in [item['title']]+item.get('aliases',[]) if n]
      if any(name in existing for name in names):continue
      existing.update(names);item=dict(item);item['sources']=[{'name':'AnimeFlix','url':item['url'],'languages':'Check source'}];cards.append(item)
   else:
    pages=base_pages;cards=matching[(page-1)*28:page*28]
  return jsonify(items=cards,source=BASE,pages=pages,complete=complete,archive=archive_state if not (lang and not q) else None)
 except Exception: return jsonify(error='Could not load data from the source. Please try again later.'),502
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
@app.get('/api/watch')
def watch():
 try:
  path=request.args.get('url',''); host=urlparse(path).hostname
  if host in ('animeflix.app','www.animeflix.app'):
   params=parse_qs(urlparse(path).query);aid=(params.get('id') or [''])[0];ep=(params.get('ep') or ['1'])[0]
   if not aid.isdigit() or not ep.isdigit():raise ValueError('Invalid episode')
   query="query($id:Int){Media(id:$id,type:ANIME){title{english romaji} episodes status nextAiringEpisode{episode}}}"
   r=requests.post('https://graphql.anilist.co',json={'query':query,'variables':{'id':int(aid)}},timeout=15);r.raise_for_status();media=r.json().get('data',{}).get('Media')
   if not media:raise ValueError('Anime not found')
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
   frames=[x.get('src') for x in tree.select('iframe[src]') if x.get('src','').startswith('https://')]
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
   r=requests.get(path,timeout=20,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False);r.raise_for_status();tree=bs4.BeautifulSoup(r.text,'html.parser');frames=[x.get('src') for x in tree.select('iframe[src]') if x.get('src','').startswith('https://')]
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
  frames=[]
  for f in s.select('iframe[src]'):
   u=f.get('src','')
   if u.startswith('https://') and u not in frames: frames.append(u)
  return jsonify(title=title,frames=frames,episodes=eps,source=path)
 except Exception: return jsonify(error='Could not load the episode from the source.'),502
# Refresh the DesiDubAnime index while the server is running; requests also
# refresh expired caches on demand, so visitors don't need a redeployment.
def auto_refresh_desi():
 while not Event().wait(300):
  try: all_catalog()
  except Exception: app.logger.exception('Automatic DesiDubAnime refresh failed; next attempt will retry')
Thread(target=auto_refresh_desi,daemon=True,name='desi-catalog-refresh').start()
if __name__=='__main__': app.run(host='0.0.0.0',port=8000)
