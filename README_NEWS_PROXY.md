# DTC latest-news headline proxy

Each visible stock card loads one latest-news headline through the existing Google Apps Script proxy. The headline is clickable and opens the source article.

## Google Apps Script setup

1. `script.google.com` → new project.
2. Paste `news_proxy/Code.gs` into `Code.gs`.
3. Deploy → New deployment → Web app.
4. Execute as: Me.
5. Access: Anyone.
6. Approve the requested permission once.
7. Copy the deployed `/exec` URL.

Put that URL in `docs/news-config.js`:

```js
window.BADAK_NEWS_PROXY_URL = 'https://script.google.com/macros/s/xxxxxxxxxxxxxxxx/exec';
```

The old variable name is intentionally retained so an existing configured URL keeps working.

## DTC behavior

- News is not pre-fetched during the market scan.
- It is loaded lazily only when a card approaches the viewport.
- One latest headline is displayed per card.
- Same-stock results are cached in the browser for five minutes.
- If the proxy is unavailable, the card falls back to a direct Google News search link.
