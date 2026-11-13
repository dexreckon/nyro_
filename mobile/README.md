# Nyro Android app

A native Android shell (Capacitor) around the same chat UI as the web app (`../pwa`). It adds native voice
dictation (Android speech recognition, English + Urdu), the hardware back button, and an installable APK.

## Get the APK (no Android Studio needed)
GitHub builds it for you on every push that touches `mobile/` or `pwa/`:
1. Open the repo's **Actions** tab, then **Android APK**, then the latest run, and download the **nyro-apk** artifact, **or**
2. Open **Releases**, then **Nyro Android (latest build)**, and download `nyro.apk`.
3. Copy it to your phone and open it (Android asks once to allow "install unknown apps").

Every build has a higher version number, and all builds use the same signing key, so a new APK installs
**over** the old one and keeps your pairing.

## First run
Enter your server address (`https://nyro.yourdomain.com`) and the pairing token. The app only talks to
HTTPS/WSS servers (cleartext is blocked) and stores the token on the phone only (backups are disabled).

## Build locally (needs JDK 21 + Android SDK)
```bash
cd mobile
npm ci
npm run sync            # builds www/ from ../pwa and syncs the native project
cd android && ./gradlew assembleRelease
# -> android/app/build/outputs/apk/release/app-release.apk
```

## Signing key
`android/app/nyro-sideload.keystore` is a **personal sideload key** committed on purpose (so CI builds update
cleanly). It is not secret and must **not** be used for Play Store publishing. To use your own key, set
`NYRO_KEYSTORE_PATH`, `NYRO_KEYSTORE_PASSWORD`, `NYRO_KEY_ALIAS`, `NYRO_KEY_PASSWORD` (e.g. as GitHub secrets).

## Layout
- `scripts/build-web.mjs`: copies `../pwa` to `www/`, injects the app CSP, drops the service worker
- `scripts/brand_android.py`: regenerates icons and splash (Pillow)
- `android/`: generated Capacitor project plus our edits (manifest, signing, version from CI)
