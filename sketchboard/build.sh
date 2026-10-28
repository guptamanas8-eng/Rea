#!/usr/bin/env bash
# Builds SketchBoard.apk with only the Android SDK command-line build tools
# (no Gradle). Used by .github/workflows/sketchboard.yml; works locally too
# with ANDROID_HOME set.
set -euo pipefail
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:?set ANDROID_HOME to your Android SDK}}
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
JAR="$SDK/platforms/$(ls "$SDK/platforms" | grep -E '^android-[0-9]+$' | sort -t- -k2 -n | tail -1)/android.jar"
VERSION_CODE=${VERSION_CODE:-1}
VERSION_NAME=${VERSION_NAME:-1.0.$VERSION_CODE}
echo "build-tools: $BT"; echo "android.jar: $JAR"

rm -rf build && mkdir -p build/classes build/dex
"$BT/aapt2" compile --dir res -o build/res.zip
"$BT/aapt2" link -o build/unsigned.apk -I "$JAR" --manifest AndroidManifest.xml \
  --min-sdk-version 21 --target-sdk-version 30 \
  --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" build/res.zip
javac --release 8 -classpath "$JAR" -d build/classes $(find src -name '*.java')
"$BT/d8" --release --min-api 21 --lib "$JAR" --output build/dex $(find build/classes -name '*.class')
(cd build/dex && zip -q ../unsigned.apk classes.dex)
"$BT/zipalign" -p -f 4 build/unsigned.apk build/aligned.apk
# debug.keystore is deliberately public (see README): it only keeps the
# signature stable so new builds install over old ones.
"$BT/apksigner" sign --ks debug.keystore --ks-pass pass:android --ks-key-alias sketchboard \
  --key-pass pass:android --out build/SketchBoard.apk build/aligned.apk
"$BT/apksigner" verify --print-certs build/SketchBoard.apk | head -3
echo "built $(pwd)/build/SketchBoard.apk ($VERSION_NAME)"
