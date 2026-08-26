const CACHE_NAME = 'dtc-pwa-v14-4-7-st20-4';
const SHELL_FILES = [
  './','./index.html',
  './styles.css?v=20260825-v14_4_5_ui_algo',
  './runtime-config.js?v=20260825-v14_4_1_excel_exact_ui',
  './app.js?v=20260826-v14_4_7_st20_4',
  './news-config.js?v=20260825-v14_4_1_excel_exact_ui',
  './pwa.js?v=20260825-v14_4_2_pwa_install',
  './manifest.webmanifest','./icons/favicon-64.png','./icons/icon-192.png','./icons/icon-512.png','./icons/icon-512-maskable.png'
];
self.addEventListener('install',event=>{self.skipWaiting();event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(SHELL_FILES)));});
self.addEventListener('activate',event=>{event.waitUntil(Promise.all([caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k)))),self.clients.claim()]));});
self.addEventListener('fetch',event=>{const request=event.request;if(request.method!=='GET')return;const url=new URL(request.url);if(url.origin!==self.location.origin){event.respondWith(fetch(request));return;}if(url.pathname.includes('/data/')||url.pathname.endsWith('/build-info.json')){event.respondWith(fetch(request,{cache:'no-store'}));return;}if(request.mode==='navigate'){event.respondWith((async()=>{try{const fresh=await fetch(request,{cache:'no-store'}),cache=await caches.open(CACHE_NAME);cache.put(request,fresh.clone());return fresh;}catch(_){return(await caches.match(request))||(await caches.match('./index.html'));}})());return;}event.respondWith((async()=>{const cached=await caches.match(request);const net=fetch(request).then(async response=>{if(response&&response.ok){const cache=await caches.open(CACHE_NAME);cache.put(request,response.clone());}return response;}).catch(()=>null);return cached||(await net)||Response.error();})());});
