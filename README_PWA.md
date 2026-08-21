# DTC v11.5 PWA

This package keeps the DTC v11.5 scanner and v11.5 Android wrapper, and adds an installable Progressive Web App (PWA) to the `docs/` Firebase/GitHub Pages frontend.

## Added

- `docs/manifest.webmanifest`
- `docs/sw.js` service worker
- `docs/pwa.js` install flow
- PWA icons under `docs/icons/`
- `앱 설치` button shown when the browser exposes an install prompt
- Firebase headers for service-worker freshness
- GitHub Pages fallback now includes all PWA files

## Deployment

1. Upload the extracted repository contents to GitHub.
2. Run **Dongtan Trading Center · Build & Deploy**.
3. `UI_ONLY` is sufficient if only the PWA/UI files changed.
4. Open `https://morninginv.web.app` in Android Chrome.
5. Tap **앱 설치** when shown, or Chrome menu → **앱 설치 / 홈 화면에 추가**.

## Cache policy

- `/data/**` and `build-info.json` are never served from the PWA cache.
- Navigation is network-first.
- Versioned UI assets use stale-while-revalidate for fast launch.
- A new deployment changes the UI version to `20260821-v11_5`.

## iOS

Safari does not expose the same install prompt. Use Share → Add to Home Screen.
