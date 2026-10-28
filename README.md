# tv.sh: clean up and control an Android TV over ADB

One script, run from your computer, that cleans ads and bloat off an Android TV
without uninstalling anything, makes it feel faster, installs open-source apps
from their official GitHub releases, and plays a YouTube video with real remote
key presses. Every change is recorded and can be undone.

## Your TV

From the Settings → About photo:

| | |
|---|---|
| Model | "Smart TV": a white-label set, no brand model name |
| OS | **Android TV OS 11**, build RP1A.200622.001 (not Google TV) |
| Security patch | 5 December 2024 |
| Kernel | 4.14.187 |
| Firmware | V8-R51AT01-LF1V126.020732 (likely a Realtek RTD2851-based board; `./tv.sh info` prints the real chip) |

It runs Android TV, so ADB works and everything below applies.

## 1. On the TV, with the remote

1. **Turn on Developer options:** press **Settings** (gear) → **Device Preferences** → **About**,
   scroll down to **Android TV OS build** and press **OK 7 times** until it says "You are now a developer".
2. Press **Back** once → **Developer options** (now in Device Preferences) → turn on **USB debugging**.
   If the list also has **Wireless debugging** or **Network debugging**, turn that on too.
3. **Find the IP:** **Settings** → **Network & Internet** → select your Wi-Fi network → note the **IP address**
   (or **Device Preferences → About → Status → IP address**).

## 2. On your computer (macOS or Linux; on Windows use WSL or Git Bash)

```bash
./tv.sh install-adb            # only if adb is missing; downloads Google's platform-tools
./tv.sh monitor                # in a second terminal: live command log + logcat
./tv.sh connect 192.168.1.23   # use your TV's IP; then press OK on "Allow USB debugging?"
```

- **Connection refused?** On the TV: Developer options → **Wireless debugging** → **Pair device with pairing code**, then
  `./tv.sh pair <ip:pairing-port> <code>` and `./tv.sh connect <ip:port>` with the port shown under *IP address & Port*.
- **macOS "No route to host"?** System Settings → Privacy & Security → **Local Network** → turn on your terminal app,
  quit and reopen it, retry.

## 3. Clean up (nothing changes until you type APPLY)

```bash
./tv.sh backup    # saves the package list, every setting it will touch, and state/undo.sh
./tv.sh scan      # prints the proposal; nothing changes
./tv.sh apply     # shows the plan again and waits for you to type APPLY
```

What `scan` proposes:

- **Disable** (`pm disable-user`, never uninstall) packages that are ad, recommendation, feedback or demo apps:
  Google Feedback, the TV recommendations services, retail demo, and vendor packages whose names say ads/demo/feedback.
- **Never touched:** live TV, tuner and TV-input apps, content providers, the launcher, Play Services and Play Store,
  keyboards and input methods, voice search, setup, System UI, Settings, remotes, Bluetooth, WebView, updaters,
  and **anything it can't identify** (those go to `state/kept.txt`).
- **Settings:** window, transition and animator animation scales to **0.5**; Private DNS to **dns.adguard-dns.com**
  (blocks ad domains). If the TV loses internet after the DNS change, `apply` puts the old DNS setting back by itself.

Edit `state/proposal.tsv` to remove any line you want to keep before running `apply`.

## 4. Apps and YouTube

```bash
./tv.sh install-app                        # TV Bro browser, latest release from github.com/truefedex/tv-bro
./tv.sh install-app owner/repo [pattern]   # any other project's official GitHub release APK
./tv.sh play "lofi hip hop radio"          # opens YouTube, searches, plays
./tv.sh play --profile 2 "..."             # picks the 2nd profile on "Who's watching?"
./tv.sh play-url https://www.youtube.com/watch?v=...
```

`install-app` only accepts URLs under `https://github.com/<owner>/<repo>/releases/download/`, picks the APK
matching the TV's CPU, and checks the SHA-256 against GitHub's published digest when the release has one.

`play` steps you can watch on the TV: **Wake → Home → open YouTube → "Who's watching?" → Search → type → Enter →
check results → OK → confirm playing**. Before every OK it reads the screen (UI tree, or OCR with `tesseract` if installed);
when it can't confirm, it saves a screenshot, opens it, and asks you. Playback is confirmed from Android's media
session, because screenshots of a playing video are black (DRM). If YouTube asks you to sign in, it stops; it never
signs in or types passwords.

## Cheat sheet

| Command | Does |
|---|---|
| `./tv.sh info` | model, chip, Android version, memory |
| `./tv.sh status` | what tv.sh has changed, current setting values |
| `./tv.sh key HOME` | press a remote key (`DPAD_UP/DOWN/LEFT/RIGHT`, `DPAD_CENTER`, `BACK`, `HOME`, `MEDIA_PLAY_PAUSE`, `VOLUME_UP`, `POWER`…) |
| `./tv.sh screenshot` | save what the TV shows |
| `./tv.sh reboot` | restart the TV |
| `TV=192.168.1.40:5555 ./tv.sh …` | target a different TV |
| `STEP_DELAY=3 ./tv.sh play "…"` | slower key presses |

## Undo

```bash
./tv.sh undo        # shows the commands, asks, then re-enables every disabled package,
                    # uninstalls apps tv.sh installed, and restores the original settings
bash state/undo.sh  # the same commands as a plain script
```

By hand, for one package or setting:

```bash
adb shell pm enable --user 0 <package>
adb shell settings put global window_animation_scale 1.0     # also transition_animation_scale, animator_duration_scale
adb shell settings put global private_dns_mode opportunistic # Android's default ("Automatic")
adb uninstall <package-you-installed>
```

To stop ADB access entirely: Settings → Device Preferences → Developer options → turn off USB debugging.

`state/` (backups, undo script, screenshots, logs) stays on your computer and is not committed.
