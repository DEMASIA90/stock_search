# DTC Firebase Hosting migration

This repository no longer hard-codes the old Firebase project or Hosting URL.
GitHub Actions injects the production Hosting endpoint into `docs/runtime-config.js`.

## Required GitHub repository settings

Repository Settings → Secrets and variables → Actions

### Variables
- `FIREBASE_PROJECT_ID`: Firebase / Google Cloud project ID
- `FIREBASE_SITE_ID`: Firebase Hosting site ID; the public URL is `https://<SITE_ID>.web.app`

### Secret
- `FIREBASE_SERVICE_ACCOUNT_JSON`: full service-account JSON used by Firebase CLI

Do not commit a service-account JSON file to the repository.

## Exact DTC URL

If you want `https://dtc.web.app`, the Hosting site ID must be exactly `dtc`.
Hosting site IDs are globally unique, so this works only if `dtc` is still available.
The Firebase project ID can be different from the Hosting site ID.

Example:

```bash
npm install -g firebase-tools
firebase login
firebase hosting:sites:create dtc --project YOUR_FIREBASE_PROJECT_ID
```

If Firebase reports that `dtc` is already reserved, choose another site ID such as
`dtc-stock` or connect a custom domain.

## First deployment

1. Create the new Firebase project in Firebase Console. Display name can be `DTC`.
2. Create/claim the Hosting site ID.
3. Firebase Console → Project settings → Service accounts → Generate new private key.
4. Add the two GitHub variables and one GitHub secret above.
5. Push this repository.
6. GitHub Actions → `Dongtan Trading Center · Build & Deploy` → Run workflow.
7. Select `ALL` and `FULL` for the first run.
8. Confirm `https://<FIREBASE_SITE_ID>.web.app/build-info.json` shows the current GitHub SHA.
9. Re-run the Android workflow after the variables are set so the APK uses the new Firebase origin.

The workflow writes the selected `FIREBASE_SITE_ID` into `firebase.json` only inside the CI runner,
so no Firebase project/site ID needs to be hard-coded in source control.
