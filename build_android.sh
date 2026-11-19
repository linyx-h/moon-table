#!/bin/bash
set -euo pipefail
ROOT="$(dirname "$(realpath "$0")")"
: "${JAVA_HOME:?请配置 JDK 17 的 JAVA_HOME}"
: "${ANDROID_HOME:?请配置 Android SDK 的 ANDROID_HOME}"
export MOON_KEYSTORE="${MOON_KEYSTORE:-$ROOT/.android-signing.jks}"
if [ -z "${MOON_SIGN_PASSWORD:-}" ]; then
  if [ ! -f "$ROOT/.android-signing-password" ]; then
    umask 077
    python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$ROOT/.android-signing-password"
  fi
  export MOON_SIGN_PASSWORD="$(cat "$ROOT/.android-signing-password")"
fi
if [ ! -f "$MOON_KEYSTORE" ]; then
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$MOON_KEYSTORE" \
    -storepass:env MOON_SIGN_PASSWORD -keypass:env MOON_SIGN_PASSWORD \
    -alias moontable -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=MoonTable, O=MoonTable, C=CN"
  chmod 600 "$MOON_KEYSTORE"
fi
npm --prefix "$ROOT" ci
npm --prefix "$ROOT" run build
"$ROOT/android/gradlew" --project-dir "$ROOT/android" --no-daemon assembleRelease
cp "$ROOT/android/app/build/outputs/apk/release/app-release.apk" "$ROOT/../月下圆桌-Android.apk"
echo "APK 已生成：$ROOT/../月下圆桌-Android.apk"
