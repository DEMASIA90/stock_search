/**
 * Dongtan Trading Center (DTC) — Latest News JSONP proxy
 * Deploy as: Web app / Execute as Me / Who has access: Anyone
 */
function doGet(e) {
  var p = (e && e.parameter) || {};
  var callback = String(p.callback || p.prefix || '').trim();
  var query = String(p.q || '').trim();
  var region = String(p.region || 'KR').toUpperCase() === 'US' ? 'US' : 'KR';
  var limit = Math.max(1, Math.min(5, Number(p.limit || 5) || 5));

  var payload;
  try {
    if (!query) throw new Error('Missing q');
    payload = {
      ok: true,
      query: query,
      generated_at: new Date().toISOString(),
      articles: fetchLatestNews_(query, region, limit)
    };
  } catch (err) {
    payload = {ok:false, error:String(err && err.message ? err.message : err), articles:[]};
  }

  var json = JSON.stringify(payload);
  if (isSafeCallback_(callback)) {
    return ContentService.createTextOutput(callback + '(' + json + ');')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService.createTextOutput(json)
    .setMimeType(ContentService.MimeType.JSON);
}

function fetchLatestNews_(query, region, limit) {
  var isKr = region === 'KR';
  var url = 'https://news.google.com/rss/search?q=' + encodeURIComponent(query) +
    (isKr ? '&hl=ko&gl=KR&ceid=KR:ko' : '&hl=en-US&gl=US&ceid=US:en');

  var response = UrlFetchApp.fetch(url, {
    method: 'get',
    followRedirects: true,
    muteHttpExceptions: true,
    headers: {'User-Agent':'Mozilla/5.0 BadakNationNews/1.0'}
  });

  var code = response.getResponseCode();
  if (code < 200 || code >= 300) throw new Error('News upstream HTTP ' + code);

  var document = XmlService.parse(response.getContentText('UTF-8'));
  var root = document.getRootElement();
  var channel = root.getChild('channel');
  if (!channel) return [];

  var items = channel.getChildren('item');
  var rows = items.map(function(item) {
    var title = text_(item, 'title');
    var link = text_(item, 'link');
    var pubDate = text_(item, 'pubDate');
    var sourceEl = item.getChild('source');
    var source = sourceEl ? String(sourceEl.getText() || '').trim() : '';
    var dt = pubDate ? new Date(pubDate) : null;
    return {
      title: title,
      link: link,
      source: source,
      published_at: dt && !isNaN(dt.getTime()) ? dt.toISOString() : null,
      ts: dt && !isNaN(dt.getTime()) ? dt.getTime() : 0
    };
  }).filter(function(x) {
    return x.title && /^https?:\/\//i.test(x.link);
  });

  rows.sort(function(a,b){ return b.ts - a.ts; });

  var seen = {};
  var out = [];
  for (var i=0; i<rows.length && out.length<limit; i++) {
    var key = rows[i].title.toLowerCase();
    if (seen[key]) continue;
    seen[key] = true;
    delete rows[i].ts;
    out.push(rows[i]);
  }
  return out;
}

function text_(parent, name) {
  var child = parent.getChild(name);
  return child ? String(child.getText() || '').trim() : '';
}

function isSafeCallback_(name) {
  return /^[A-Za-z_$][0-9A-Za-z_$\.]{0,80}$/.test(name);
}
