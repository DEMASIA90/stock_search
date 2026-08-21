# DTC v11.8 PWA

The `docs/` frontend is an installable Progressive Web App and is also used by the Capacitor Android wrapper.

- UI build: `20260821-v11_8`
- Service-worker shell cache: `dtc-pwa-v11-7-shell`
- Market and Quiz data under `/data/` are always fetched fresh rather than stored in the service-worker shell cache.
- GitHub Pages and Android use the Firebase Hosting data endpoint when appropriate.
- Quiz mode lazy-loads only the selected 100T+ stock histories rather than downloading the whole historical pool at startup.

Run **ALL + FULL** once after upgrading so the new Quiz data tree exists for all categories.
