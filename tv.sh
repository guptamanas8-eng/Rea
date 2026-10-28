#!/usr/bin/env bash
# tv.sh: reversible cleanup and remote control for an Android TV over ADB.
# Runs on macOS and Linux (bash 3.2+). Run ./tv.sh help for the commands.
set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
STATE="${TVCTL_STATE:-$SCRIPT_DIR/state}"
LOG="$STATE/commands.log"          # read by ./tv.sh monitor
ORIGINAL="$STATE/original"         # first backup, never overwritten
CHANGES="$STATE/changes.txt"       # one undo action per line, in the order applied
UNDO_SH="$STATE/undo.sh"
PROPOSAL="$STATE/proposal.tsv"
YT=com.google.android.youtube.tv
STEP_DELAY="${STEP_DELAY:-1.5}"    # pause after each key press so you can follow on the TV
GITHUB_API="${GITHUB_API:-https://api.github.com}"
mkdir -p "$STATE"

# Settings this tool changes: namespace key new-value. Order matters for DNS
# (specifier first, then mode).
TOUCHED_SETTINGS="global:window_animation_scale:0.5
global:transition_animation_scale:0.5
global:animator_duration_scale:0.5
global:private_dns_specifier:dns.adguard-dns.com
global:private_dns_mode:hostname"

# Never proposed for disabling: launchers, providers, live TV and tuner/input
# stacks, keyboards and input methods, Play Services/Store, setup, system UI,
# settings, remotes, Bluetooth, WebView, voice search.
PROTECTED_RE='launcher|tvlauncher|[.]home([.]|$)|homescreen|provider|livetv|live[.]?tv|tuner|tvinput|[.]tv[.]input|[.]input|inputmethod|[.]ime([.]|$)|keyboard|latinime|gboard|leanbackime|[.]gms|gsf|vending|katniss|assistant|setupwizard|setupwraith|systemui|settings|packageinstaller|permissioncontroller|bluetooth|remote|webview|mediaprovider|framework|^android$|networkstack|wifi|dtv|[.]atv|hdmi|[.]cec|cast|mediashell|tvsettings|^com[.]android[.]shell$|ota|update'
AD_RE='(^|[._])(ad|ads|adsdk|adservice|adservices|adcore|adplayer|admob|advert|advertising|adtrack)([._]|$)|advertis'
RECO_RE='recommend'
FEEDBACK_RE='feedback|survey'
DEMO_RE='demo|storemode|shopmode|retailmode|instore'

c_dim=$'\033[2m'; c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_err=$'\033[31m'; c_hi=$'\033[1;36m'; c_off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$c_hi" "$*" "$c_off"; }
ok()   { printf '%s  ok: %s%s\n' "$c_ok" "$*" "$c_off"; }
warn() { printf '%s  warning: %s%s\n' "$c_warn" "$*" "$c_off" >&2; }
die()  { printf '%serror: %s%s\n' "$c_err" "$*" "$c_off" >&2; exit 1; }
ts()   { date '+%Y-%m-%d %H:%M:%S'; }

ask_yes() {  # ask_yes "question" -> 0 on y/yes
  printf '%s [y/N] ' "$1"
  read -r ans </dev/tty || return 1
  case $ans in y|Y|yes|YES) return 0;; esac
  return 1
}

find_adb() {
  ADB=""
  if command -v adb >/dev/null 2>&1; then ADB=$(command -v adb)
  elif [ -x "$STATE/platform-tools/adb" ]; then ADB="$STATE/platform-tools/adb"
  elif [ -x "$STATE/platform-tools/adb.exe" ]; then ADB="$STATE/platform-tools/adb.exe"
  fi
}

# a: run one adb command against the TV and log it for the monitor.
a() {
  local id="$$-$RANDOM" rc
  printf 'START %s %s adb %s\n' "$id" "$(ts)" "$*" >> "$LOG"
  "$ADB" -s "$SERIAL" "$@" </dev/null
  rc=$?
  printf 'END %s %s rc=%s\n' "$id" "$(ts)" "$rc" >> "$LOG"
  return $rc
}
ash() { a shell "$@" | tr -d '\r'; }

# q: quote one value for the TV's shell, because adb joins its arguments into a single command line
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# launch <pkg>: start the app's TV launcher activity, the same one the home screen opens
launch() {
  local cat comp
  for cat in LEANBACK_LAUNCHER LAUNCHER; do
    comp=$(ash cmd package resolve-activity --brief -a android.intent.action.MAIN \
           -c "android.intent.category.$cat" "$1" | tail -1)
    case $comp in
      */*) ash am start -n "$comp" | grep -q Error && return 1; return 0;;
    esac
  done
  return 1
}

# pkg_info <pkg>: version, system or user app, installer, first install time
pkg_info() {
  ash dumpsys package "$1" | awk '
    /versionName=/ && !v {sub(/.*versionName=/,""); v=$1}
    /installerPackageName=/ && !i {sub(/.*installerPackageName=/,""); i=$1}
    /firstInstallTime=/ && !f {sub(/.*firstInstallTime=/,""); f=$1" "$2}
    /pkgFlags=\[/ && !s {s=($0 ~ / SYSTEM /) ? "system app" : "user-installed"}
    END {printf "v%s, %s, installer %s, first installed %s", v?v:"?", s?s:"?", i?i:"none", f?f:"?"}'
}

need_device() {
  find_adb
  [ -n "$ADB" ] || die "adb not found. Run: ./tv.sh install-adb"
  SERIAL="${TV:-$(cat "$STATE/device" 2>/dev/null)}"
  [ -n "$SERIAL" ] || die "No TV yet. Run: ./tv.sh connect <tv-ip>"
  local st
  st=$("$ADB" -s "$SERIAL" get-state 2>&1)
  if [ "$st" != device ]; then
    "$ADB" connect "$SERIAL" >/dev/null 2>&1
    sleep 1
    st=$("$ADB" -s "$SERIAL" get-state 2>&1)
  fi
  [ "$st" = device ] || die "TV $SERIAL is not reachable ($st). Is it on, and is debugging still enabled? Try: ./tv.sh connect <tv-ip>"
}

# ---------------------------------------------------------------- install-adb
cmd_install_adb() {
  find_adb
  if [ -n "$ADB" ]; then say "adb is already installed: $ADB"; "$ADB" version | head -1; return 0; fi
  local os
  case "$(uname -s)" in
    Darwin) os=darwin;;
    Linux) os=linux;;
    MINGW*|MSYS*|CYGWIN*) os=windows;;
    *) die "Unsupported OS $(uname -s). Get platform-tools from https://developer.android.com/tools/releases/platform-tools";;
  esac
  local url="https://dl.google.com/android/repository/platform-tools-latest-$os.zip" tmp
  step "Downloading Android SDK Platform-Tools from Google ($url)"
  tmp=$(mktemp -d)
  curl -fL --proto '=https' -o "$tmp/platform-tools.zip" "$url" || die "Download failed"
  rm -rf "$STATE/platform-tools"
  unzip -q "$tmp/platform-tools.zip" -d "$STATE" || die "Unzip failed"
  rm -rf "$tmp"
  find_adb
  ok "installed $ADB"
  "$ADB" version | head -1
}

# ------------------------------------------------------------ connect / pair
cmd_connect() {
  local target=${1:-}
  [ -n "$target" ] || die "usage: ./tv.sh connect <tv-ip>[:port]"
  case $target in *:*) ;; *) target="$target:5555";; esac
  find_adb; [ -n "$ADB" ] || die "adb not found. Run: ./tv.sh install-adb"
  step "Connecting to $target"
  local out
  out=$("$ADB" connect "$target" 2>&1); say "$out"
  case $out in
    *"connected to"*) ;;
    # first connection from this computer: the TV is showing its approval prompt
    *"failed to authenticate"*|*unauthorized*) ;;
    *"No route to host"*)
      if [ "$(uname -s)" = Darwin ]; then
        die "No route to host. On macOS give your terminal Local Network access: System Settings > Privacy & Security > Local Network > turn on Terminal (or iTerm/VS Code), then quit and reopen the terminal and retry."
      fi
      die "No route to host. Check the IP and that this computer and the TV are on the same network.";;
    *)
      die "adb connect was refused. On the TV open Settings > Device Preferences > Developer options > Wireless debugging (if present) > 'Pair device with pairing code', then run:
  ./tv.sh pair <ip:pairing-port> <6-digit-code>
  ./tv.sh connect <ip:port shown under 'IP address & Port'>";;
  esac
  say "${c_warn}Look at the TV now: if it asks 'Allow USB debugging?', tick 'Always allow from this computer' and press OK on the remote.$c_off"
  local i st=""
  for i in $(seq 1 90); do
    st=$("$ADB" -s "$target" get-state 2>&1)
    [ "$st" = device ] && break
    sleep 1
  done
  [ "$st" = device ] || die "The TV did not authorize this computer within 90 s ($st). Run connect again and accept the prompt on the TV."
  printf '%s\n' "$target" > "$STATE/device"
  SERIAL=$target
  ok "connected and authorized"
  cmd_info
}

cmd_pair() {
  [ $# -eq 2 ] || die "usage: ./tv.sh pair <ip:pairing-port> <pairing-code>"
  find_adb; [ -n "$ADB" ] || die "adb not found. Run: ./tv.sh install-adb"
  "$ADB" pair "$1" "$2" || die "Pairing failed. Codes expire quickly; open the pairing dialog again and retry."
  say "Paired. Now run ./tv.sh connect <ip:port> using the port shown under 'IP address & Port' (not the pairing port)."
}

cmd_info() {
  need_device
  step "TV identity"
  say "  manufacturer : $(ash getprop ro.product.manufacturer)"
  say "  model        : $(ash getprop ro.product.model)"
  say "  device       : $(ash getprop ro.product.device)"
  say "  platform/SoC : $(ash getprop ro.board.platform) $(ash getprop ro.hardware)"
  say "  android      : $(ash getprop ro.build.version.release) (SDK $(ash getprop ro.build.version.sdk)), build $(ash getprop ro.build.id)"
  say "  cpu abi      : $(ash getprop ro.product.cpu.abi)"
  if ash pm list features | grep -q 'android.software.leanback'; then
    ok "this is an Android TV device (leanback feature present)"
  else
    warn "leanback feature not found: this may not be Android TV"
  fi
  say "  memory       : $(ash cat /proc/meminfo | awk '/MemTotal|MemAvailable/ {printf "%s %d MB  ", $1, $2/1024}')"
}

# --------------------------------------------------------------------- backup
cmd_backup() {
  need_device
  local b
  b="$STATE/backup-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$b"
  step "Saving package list and settings to $b"
  ash pm list packages -f -u > "$b/packages_all.txt"
  ash pm list packages -e > "$b/packages_enabled.txt"
  ash pm list packages -d > "$b/packages_disabled.txt"
  ash pm list packages -s > "$b/packages_system.txt"
  ash pm list packages -3 > "$b/packages_thirdparty.txt"
  for ns in global secure system; do ash settings list "$ns" > "$b/settings_$ns.txt"; done
  ash getprop > "$b/getprop.txt"
  ash cat /proc/meminfo > "$b/meminfo.txt"
  : > "$b/touched_settings.txt"
  local line ns key v
  printf '%s\n' "$TOUCHED_SETTINGS" | while IFS=: read -r ns key _; do
    v=$(ash settings get "$ns" "$key")
    printf '%s %s %s\n' "$ns" "$key" "$v" >> "$b/touched_settings.txt"
  done
  say "  $(wc -l < "$b/packages_all.txt" | tr -d ' ') packages, $(wc -l < "$b/packages_disabled.txt" | tr -d ' ') already disabled"
  say "  settings this tool will touch (current values):"
  sed 's/^/    /' "$b/touched_settings.txt"
  if [ ! -d "$ORIGINAL" ]; then
    cp -R "$b" "$ORIGINAL"
    ok "saved as the original state ($ORIGINAL); undo restores to this"
  else
    say "  (original state already saved earlier in $ORIGINAL; kept as is)"
  fi
  write_undo
  ok "undo commands written to $UNDO_SH"
}

# ----------------------------------------------------------------------- scan
classify() {  # classify <pkg> -> "category<TAB>reason", or nothing to leave it alone
  local p=$1 kw
  case $p in
    com.google.android.feedback) printf 'feedback\tGoogle Feedback (sends bug and usage reports)\n'; return;;
    com.google.android.tvrecommendations) printf 'recommendation\tGoogle TV recommendations service\n'; return;;
    com.google.android.leanbacklauncher.recommendations) printf 'recommendation\trecommendation rows feed (separate from the launcher itself)\n'; return;;
    com.android.retaildemo) printf 'demo\tAndroid retail demo mode\n'; return;;
    android.autoinstalls.config.google.gtvpai) printf 'ad\tPlay Auto Installs config: pushes promoted partner apps onto the TV\n'; return;;
    com.tcl.esticker) printf 'demo\tTCL E-Sticker: shop-display feature stickers\n'; return;;
    com.tcl.waterfall.overseas) printf 'recommendation\tTCL content recommendation feed\n'; return;;
  esac
  printf '%s' "$p" | grep -Eiq "$PROTECTED_RE" && return
  for pair in "ad:$AD_RE" "recommendation:$RECO_RE" "feedback:$FEEDBACK_RE" "demo:$DEMO_RE"; do
    kw=$(printf '%s' "$p" | grep -Eio "${pair#*:}" | head -1)
    if [ -n "$kw" ]; then
      printf '%s\tpackage name contains "%s"\n' "${pair%%:*}" "$(printf '%s' "$kw" | tr -d '._')"
      return
    fi
  done
}

cmd_scan() {
  need_device
  [ -d "$ORIGINAL" ] || die "Save the current state first: ./tv.sh backup"
  step "Scanning enabled packages"
  local all p cls path n=0 kept=0
  all=$(ash pm list packages -f -u)
  : > "$PROPOSAL.tmp"
  : > "$STATE/kept.txt"
  for p in $(ash pm list packages -e | sed 's/^package://' | sort); do
    cls=$(classify "$p")
    path=$(printf '%s\n' "$all" | grep "=$p\$" | head -1 | sed 's/^package://; s/=[^=]*$//')
    if [ -n "$cls" ]; then
      printf '%s\t%s\t%s\t%s\n' "$p" "$cls" "${path:-?}" "$(pkg_info "$p")" >> "$PROPOSAL.tmp"; n=$((n+1))
    else
      printf '%s\t%s\n' "$p" "${path:-?}" >> "$STATE/kept.txt"; kept=$((kept+1))
    fi
  done
  {
    printf '# Proposed: disable (not uninstall) these packages. Delete a line to keep that app.\n'
    printf '# package\tcategory\treason\tinstalled at\tdetails\n'
    cat "$PROPOSAL.tmp"
  } > "$PROPOSAL"
  rm -f "$PROPOSAL.tmp"
  step "Proposed cleanup ($n packages to disable, $kept left alone)"
  awk -F'\t' '!/^#/ {printf "  %-55s %-15s %s\n  %55s %s | %s\n", $1, $2, $3, "", $4, $5}' "$PROPOSAL"
  step "Proposed settings"
  printf '%s\n' "$TOUCHED_SETTINGS" | while IFS=: read -r ns key val; do
    printf '  %-40s %-22s -> %s\n' "$ns $key" "$(ash settings get "$ns" "$key")" "$val"
  done
  say ""
  say "Everything else ($kept packages, including anything unrecognized) stays untouched; see $STATE/kept.txt."
  say "Details on any of them: ./tv.sh pkg-info <package>  (no package: all of kept.txt, slow)"
  say "Review $PROPOSAL, delete any line you want to keep, then run: ./tv.sh apply"
}

# ---------------------------------------------------------------------- apply
dns_works() { ash ping -c 1 -W 3 www.google.com 2>&1 | grep -q '1 received\|1 packets received'; }

cmd_apply() {
  need_device
  [ -d "$ORIGINAL" ] || die "Save the current state first: ./tv.sh backup"
  [ -f "$PROPOSAL" ] || die "No proposal yet: ./tv.sh scan"
  step "About to disable:"
  awk -F'\t' '!/^#/ && NF {printf "  %-55s %s\n", $1, $2}' "$PROPOSAL"
  step "About to set:"
  printf '%s\n' "$TOUCHED_SETTINGS" | sed 's/^/  /; s/:/ /; s/:/ -> /'
  say ""
  printf 'Type APPLY to make these changes (anything else cancels): '
  local ans; read -r ans </dev/tty
  [ "$ans" = APPLY ] || die "Cancelled; nothing changed."

  local before; before=$(ash cat /proc/meminfo | awk '/MemAvailable/ {print int($2/1024)}')
  step "Disabling packages"
  local pkg cat reason out
  while IFS=$'\t' read -r pkg cat reason _; do
    case $pkg in ''|\#*) continue;; esac
    out=$(ash pm disable-user --user 0 "$pkg" 2>&1)
    if printf '%s' "$out" | grep -q 'new state: disabled'; then
      printf 'shell pm enable --user 0 %s\n' "$pkg" >> "$CHANGES"
      ok "$pkg ($cat)"
    else
      warn "$pkg not changed: $out"
    fi
  done < "$PROPOSAL"
  write_undo

  step "Changing settings"
  local dns_before=1
  dns_works || dns_before=0
  printf '%s\n' "$TOUCHED_SETTINGS" | while IFS=: read -r ns key val; do
    ash settings put "$ns" "$key" "$val" && ok "$ns $key = $(ash settings get "$ns" "$key")"
  done
  sleep 5
  if [ $dns_before = 1 ] && ! dns_works; then
    warn "The TV lost internet with Private DNS dns.adguard-dns.com; restoring the previous DNS settings."
    restore_setting global private_dns_mode
    restore_setting global private_dns_specifier
  else
    ok "internet still works with Private DNS"
  fi
  write_undo
  local after; after=$(ash cat /proc/meminfo | awk '/MemAvailable/ {print int($2/1024)}')
  step "Done"
  say "  available memory: ${before} MB before, ${after} MB now (a reboot frees more: ./tv.sh reboot)"
  say "  undo everything: ./tv.sh undo   (commands listed in $UNDO_SH)"
}

restore_setting() {  # restore_setting <ns> <key> to its value in the original backup
  local v
  v=$(awk -v n="$1" -v k="$2" '$1==n && $2==k {print $3}' "$ORIGINAL/touched_settings.txt")
  if [ -z "$v" ] || [ "$v" = null ]; then ash settings delete "$1" "$2" >/dev/null
  else ash settings put "$1" "$2" "$v"; fi
}

# ----------------------------------------------------------------------- undo
undo_actions() {  # every undo action, newest first, as adb arguments
  [ -f "$CHANGES" ] && awk '{l[NR]=$0} END {for (i=NR; i>0; i--) print l[i]}' "$CHANGES"
  [ -f "$ORIGINAL/touched_settings.txt" ] || return 0
  # mode before specifier when restoring DNS
  awk '{l[NR]=$0} END {for (i=NR; i>0; i--) print l[i]}' "$ORIGINAL/touched_settings.txt" |
  while read -r ns key v; do
    if [ -z "$v" ] || [ "$v" = null ]; then printf 'shell settings delete %s %s\n' "$ns" "$key"
    else printf 'shell settings put %s %s %s\n' "$ns" "$key" "$v"; fi
  done
}

write_undo() {
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Undo everything tv.sh changed on the TV (generated %s). Safe to run twice.\n' "$(ts)"
    printf 'ADB="${ADB:-adb}"; TV="${TV:-%s}"\n' "$SERIAL"
    undo_actions | sed 's/^/"$ADB" -s "$TV" /'
  } > "$UNDO_SH"
  chmod +x "$UNDO_SH"
}

cmd_undo() {
  need_device
  local acts; acts=$(undo_actions)
  [ -n "$acts" ] || die "Nothing to undo."
  step "Undo will run:"
  printf '%s\n' "$acts" | sed 's/^/  adb /'
  ask_yes "Run these now?" || die "Cancelled."
  printf '%s\n' "$acts" | while read -r line; do
    # shellcheck disable=SC2086
    a $line | tr -d '\r' | sed 's/^/  /'
  done
  [ -f "$CHANGES" ] && mv "$CHANGES" "$CHANGES.undone-$(date +%Y%m%d-%H%M%S)"
  write_undo
  ok "Restored. Reboot recommended: ./tv.sh reboot"
}

# ---------------------------------------------------------------- install-app
sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

cmd_install_app() {
  local repo=${1:-truefedex/tv-bro} pat=${2:-}
  case $repo in */*) ;; *) die "usage: ./tv.sh install-app <github-owner/repo> [apk-name-pattern]";; esac
  need_device
  step "Looking up the latest official release of github.com/$repo"
  local json tag
  json=$(curl -fsSL -H 'Accept: application/vnd.github+json' "$GITHUB_API/repos/$repo/releases/latest") ||
    die "Could not read releases for $repo (does it publish GitHub releases?)"
  tag=$(printf '%s\n' "$json" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
  # url<TAB>digest for each asset; digest is null when GitHub has none
  local assets
  assets=$(printf '%s\n' "$json" | awk '
    /"digest":/ {d=$0; sub(/.*"digest": */,"",d); gsub(/[",]/,"",d)}
    /"browser_download_url":/ {u=$0; sub(/.*"browser_download_url": *"/,"",u); sub(/".*/,"",u); print u "\t" (d==""?"null":d); d=""}' |
    grep -i '\.apk	')
  [ -n "$pat" ] && assets=$(printf '%s\n' "$assets" | grep -Ei "$pat")
  [ -n "$assets" ] || die "Release $tag of $repo has no matching .apk asset."
  if [ "$(printf '%s\n' "$assets" | wc -l)" -gt 1 ]; then
    local abi m
    abi=$(ash getprop ro.product.cpu.abi)
    m=$(printf '%s\n' "$assets" | grep -i -- "$abi")
    [ -z "$m" ] && m=$(printf '%s\n' "$assets" | grep -i universal)
    if [ -n "$m" ] && [ "$(printf '%s\n' "$m" | wc -l)" -eq 1 ]; then assets=$m
    else
      say "Release $tag has several APKs:"
      printf '%s\n' "$assets" | awk -F'\t' '{n=split($1,p,"/"); printf "  %d) %s\n", NR, p[n]}'
      printf 'Pick a number: '; local i; read -r i </dev/tty
      assets=$(printf '%s\n' "$assets" | sed -n "${i}p")
      [ -n "$assets" ] || die "No such choice."
    fi
  fi
  local url digest name dir file
  url=$(printf '%s' "$assets" | cut -f1); digest=$(printf '%s' "$assets" | cut -f2)
  case $url in "https://github.com/$repo/releases/download/"*) ;; *) die "Refusing non-GitHub-release URL: $url";; esac
  name=${url##*/}
  dir="$STATE/downloads/${repo%%/*}_${repo##*/}/$tag"; mkdir -p "$dir"; file="$dir/$name"
  step "Downloading $name ($tag)"
  curl -fL --proto '=https' -o "$file" "$url" || die "Download failed"
  local sum; sum=$(sha256_of "$file")
  case $digest in
    sha256:*) [ "sha256:$sum" = "$digest" ] || die "Checksum mismatch for $name (got $sum, GitHub says ${digest#sha256:})"
              ok "sha256 matches GitHub's published digest";;
    *) say "  sha256 $sum (this release publishes no digest to compare against)";;
  esac
  local before after new
  before=$(ash pm list packages -3 | sort)
  step "Installing on the TV"
  a install -r "$file" | tr -d '\r' | sed 's/^/  /'
  after=$(ash pm list packages -3 | sort)
  new=$(printf '%s\n' "$after" | grep -vxF "$before" | sed 's/^package://' | head -1)
  if [ -n "$new" ]; then
    printf 'uninstall %s\n' "$new" >> "$CHANGES"
    write_undo
    ok "installed $new (undo removes it)"
    launch "$new" &&
      ok "opened $new on the TV"
  else
    ok "installed (it was already present, so it was updated in place and undo leaves it)"
  fi
}

# ---------------------------------------------------------------------- screen
key() { say "  ${c_dim}remote: $1$c_off"; a shell input keyevent "KEYCODE_$1"; sleep "$STEP_DELAY"; }

open_file() {
  if command -v open >/dev/null && [ "$(uname -s)" = Darwin ]; then open "$1"
  elif command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1 &
  fi
}

shot() {  # shot <label> -> path of a PNG screenshot
  mkdir -p "$STATE/screens"
  local f; f="$STATE/screens/$(date +%Y%m%d-%H%M%S)-$1.png"
  a exec-out screencap -p > "$f"
  printf '%s' "$f"
}

screen_text() {  # visible text on the TV, one item per line (UI tree, else OCR)
  local t
  ash uiautomator dump /sdcard/tvctl_ui.xml >/dev/null 2>&1
  t=$(a exec-out cat /sdcard/tvctl_ui.xml 2>/dev/null | tr '>' '\n' |
      grep -Eo '(text|content-desc)="[^"]+"' | sed -E 's/^[^=]+="//; s/"$//')
  if [ -z "$t" ] && command -v tesseract >/dev/null; then
    t=$(tesseract "$(shot ocr)" - 2>/dev/null)
  fi
  printf '%s' "$t"
}

check_screen() {  # check_screen <regex> <description>: automatic check, else ask you
  local t f
  t=$(screen_text)
  if printf '%s' "$t" | grep -Eiq "$1"; then ok "screen check: $2"; return 0; fi
  f=$(shot check)
  say "  Couldn't confirm \"$2\" automatically. Screenshot: $f"
  open_file "$f"
  ask_yes "  Does the TV show $2?"
}

stop_if_sign_in() {
  if printf '%s' "$1" | grep -Eiq 'sign in to|enter your password|add an? account|use your phone or computer'; then
    die "YouTube is asking to sign in. I don't sign in for you: finish or skip it with the remote, then run the command again."
  fi
}

# ------------------------------------------------------------------------ play
cmd_play() {
  local profile=""
  if [ "${1:-}" = --profile ]; then profile=${2:?}; shift 2; fi
  local query="$*"
  [ -n "$query" ] || die 'usage: ./tv.sh play [--profile N] "video name"'
  need_device
  ash pm path "$YT" | grep -q package: || die "YouTube for Android TV ($YT) is not installed."
  ash pm list packages -d | grep -qx "package:$YT" && die "YouTube is disabled. Enable it: adb shell pm enable $YT"

  step "Waking the TV and going home"
  key WAKEUP
  key HOME

  step "Opening YouTube from a fresh start"
  ash am force-stop "$YT"
  launch "$YT" || die "Could not open YouTube's launcher activity."
  local i
  for i in $(seq 1 20); do
    ash dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity' | grep -q "$YT" && break
    sleep 1
  done
  sleep 5   # splash screen

  step "Checking for \"Who's watching?\""
  local t; t=$(screen_text)
  stop_if_sign_in "$t"
  if printf '%s' "$t" | grep -Eiq "who.?s watching|choose (an|your) account"; then
    if [ -n "$profile" ]; then
      for i in $(seq 2 "$profile"); do key DPAD_RIGHT; done
      check_screen "who.?s watching" "profile $profile highlighted on \"Who's watching?\"" || die "Stopped."
    else
      say "  \"Who's watching?\" is on screen. Highlight your profile with the remote if needed."
      ask_yes "  Press OK on the highlighted profile now?" || die "Stopped. Pick a profile on the TV, then run the command again."
    fi
    key DPAD_CENTER
    sleep 4
  else
    say "  not shown (or not detectable); continuing"
  fi

  step "Opening YouTube search"
  key SEARCH
  local typed=1
  if ! check_screen 'search' 'the YouTube search screen'; then
    say "  Falling back to YouTube's search intent with the query filled in."
    ash am start -a android.intent.action.SEARCH -n "$YT/com.google.android.apps.youtube.tv.activity.ShellActivity" \
      --es query "$(q "$query")" >/dev/null
    typed=0
    sleep 4
  fi

  if [ $typed = 1 ]; then
    step "Typing \"$query\""
    local typed_q; typed_q=$(printf '%s' "$query" | sed 's/[^A-Za-z0-9 ._-]//g; s/ /%s/g')
    [ -n "$typed_q" ] || die "This name has no characters adb can type; use plain letters, or play a link: ./tv.sh play-url <youtube-url>"
    a shell input text "$(q "$typed_q")"; sleep "$STEP_DELAY"
    key ENTER
    sleep 3
  fi

  step "Checking the results before pressing OK"
  local first; first=$(printf '%s' "$query" | awk '{print $1}')
  t=$(screen_text)
  stop_if_sign_in "$t"
  local f; f=$(shot results)
  open_file "$f"
  say "  Screenshot of the results: $f"
  if printf '%s' "$t" | grep -Fiq "$first"; then ok "results mention \"$first\""; fi
  ask_yes "  Is the video you want highlighted on the TV? (Move the highlight with the remote first if not.) Press OK on it?" ||
    die "Stopped before pressing OK."
  key DPAD_CENTER

  step "Confirming playback"
  if wait_playing; then
    ok "YouTube reports the video is playing"
  else
    warn "No playing state reported yet. Screenshots of video are blank (DRM), so look at the TV; press OK on the remote if it's waiting."
  fi
}

wait_playing() {
  local i
  for i in $(seq 1 20); do
    ash dumpsys media_session | grep -A 25 "package=$YT" | grep -q 'state=3,' && return 0
    sleep 1
  done
  return 1
}

cmd_play_url() {
  local url=${1:-}
  case $url in https://www.youtube.com/watch\?v=*|https://youtu.be/*|https://youtube.com/watch\?v=*) ;; *) die "usage: ./tv.sh play-url https://www.youtube.com/watch?v=...";; esac
  need_device
  key WAKEUP
  step "Opening $url in YouTube"
  ash am start -a android.intent.action.VIEW -d "$(q "$url")" "$YT" >/dev/null
  wait_playing && ok "playing" || warn "No playing state reported; check the TV (\"Who's watching?\" may need a press of OK)."
}

# -------------------------------------------------------------------- monitor
cmd_monitor() {
  find_adb
  touch "$LOG"
  local filter=${TVCTL_LOGCAT:-'ActivityManager:I PackageManager:I *:W'}
  say "Live monitor: every adb command with a timestamp; TV logcat ($filter) only while a command runs. Ctrl-C to stop."
  trap 'kill 0 2>/dev/null' INT TERM EXIT
  local lc="" depth=0 kind id d t rest serial
  tail -n 0 -F "$LOG" 2>/dev/null | while read -r kind id d t rest; do
    case $kind in
      START)
        printf '%s[%s %s]%s %s\n' "$c_hi" "$d" "$t" "$c_off" "$rest"
        depth=$((depth+1))
        serial="${TV:-$(cat "$STATE/device" 2>/dev/null)}"
        if [ -z "$lc" ] && [ -n "$ADB" ] && [ -n "$serial" ]; then
          # shellcheck disable=SC2086
          "$ADB" -s "$serial" logcat -T 1 -v time $filter > >(sed "s/^/   ${c_dim}│ /; s/\$/$c_off/") 2>/dev/null &
          lc=$!
        fi;;
      END)
        printf '%s[%s %s] done, %s%s\n' "$c_dim" "$d" "$t" "$rest" "$c_off"
        depth=$((depth-1)); [ $depth -lt 0 ] && depth=0
        if [ $depth -eq 0 ] && [ -n "$lc" ]; then
          sleep 0.3; kill "$lc" 2>/dev/null; lc=""
        fi;;
    esac
  done
}

# ---------------------------------------------------------------------- misc
cmd_remote() {
  need_device
  command -v python3 >/dev/null || die "python3 is missing. In Termux: pkg install python"
  exec python3 "$SCRIPT_DIR/remote/server.py"
}

cmd_pkg_info() {
  need_device
  local p
  if [ $# -eq 0 ]; then
    [ -f "$STATE/kept.txt" ] || die "usage: ./tv.sh pkg-info <package>... (or run scan first)"
    set -- $(cut -f1 "$STATE/kept.txt")
  fi
  for p in "$@"; do printf '%-55s %s\n' "$p" "$(pkg_info "$p")"; done
}

cmd_status() {
  need_device
  cmd_info
  step "Changed by tv.sh"
  if [ -s "$CHANGES" ]; then sed 's/^shell pm enable --user 0 /  disabled: /; s/^uninstall /  installed: /' "$CHANGES"
  else say "  no package changes"; fi
  printf '%s\n' "$TOUCHED_SETTINGS" | while IFS=: read -r ns key _; do
    printf '  %-40s %s\n' "$ns $key" "$(ash settings get "$ns" "$key")"
  done
}

cmd_help() {
  cat <<'EOF'
Usage: ./tv.sh <command>

Setup
  install-adb                     download Google's platform-tools if adb is missing
  connect <tv-ip>[:port]          connect and wait for you to allow debugging on the TV
  pair <ip:port> <code>           Wireless debugging pairing, if connect is refused
  info                            model, Android version, memory

Cleanup (reversible)
  backup                          save packages, settings and undo commands (do this first)
  scan                            propose what to disable; nothing changes
  apply                           do the reviewed proposal (asks you to type APPLY)
  pkg-info [package...]           version, system/user, installer, install date
  status                          what tv.sh has changed
  undo                            put everything back (or run state/undo.sh)

Apps and playback
  install-app [owner/repo] [pat]  latest APK from a project's official GitHub releases
                                  (default truefedex/tv-bro, the TV Bro browser)
  play [--profile N] "name"       open YouTube, search, and play with real key presses
  play-url <youtube-url>          play a specific YouTube link
  remote                          phone remote + live sketch board at http://127.0.0.1:8765

Other
  monitor                         run in a second terminal: commands + logcat while they run
  key <NAME>                      press one remote key, e.g. key HOME, key DPAD_DOWN
  screenshot                      save what the TV shows (video areas are black: DRM)
  reboot                          restart the TV

Env: TV=<ip:port> picks a TV, STEP_DELAY=<seconds> paces key presses,
     TVCTL_LOGCAT='<filter>' changes the monitor's logcat filter.
EOF
}

cmd=${1:-help}; [ $# -gt 0 ] && shift
case $cmd in
  install-adb) cmd_install_adb "$@";;
  connect) cmd_connect "$@";;
  pair) cmd_pair "$@";;
  info) cmd_info;;
  backup) cmd_backup;;
  scan) cmd_scan;;
  apply) cmd_apply;;
  pkg-info) cmd_pkg_info "$@";;
  status) cmd_status;;
  undo) cmd_undo;;
  install-app) cmd_install_app "$@";;
  play) cmd_play "$@";;
  play-url) cmd_play_url "$@";;
  remote) cmd_remote;;
  monitor) cmd_monitor;;
  key) need_device; key "${1:?usage: ./tv.sh key HOME}";;
  screenshot) need_device; f=$(shot manual); say "$f"; open_file "$f";;
  reboot) need_device; ask_yes "Restart the TV now?" && a reboot;;
  help|-h|--help) cmd_help;;
  *) cmd_help; exit 1;;
esac
