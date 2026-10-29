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

Each proposed package shows its version, whether it's a system or user-installed app, its installer and first install date.
For anything in `state/kept.txt` you're curious about: `./tv.sh pkg-info <package>`.

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

## 5. Phone remote and sketch board

A remote for your phone, plus a drawing board on the TV that updates live as you draw.

```bash
pkg install python                                    # Termux, once
bash tv.sh install-app guptamanas8-eng/Rea sketchboard # the TV app, once
bash tv.sh remote                                     # start the remote; leave it running
```

Open **http://127.0.0.1:8765** in the phone's browser and use **Add to Home screen** to make it an app icon.

- **Remote tab:** arrows (hold to repeat), OK, Back, Home, media and volume keys, typing into the TV's focused text box,
  YouTube search, which app is open (with **Close app** and **Recent**), and a searchable button for every app,
  including phone apps installed on the TV (📱), which Google TV's home screen hides.
- **Screen tab:** a live picture of the TV. Tap it to tap on the TV, swipe to scroll, hold for a long press; handy for
  phone apps that expect touch. Video areas show black (copy protection) though the TV plays them normally.
- **Sketch tab:** draw with colours, sizes and an eraser; undo, clear and change the background; add a note line at the top.
  **Show board on TV** opens the Sketch Board app on the TV and tells it where your phone is. After that, open
  Sketch Board from the TV's home screen any time (press **OK** in it to reload).
- **Put it on the home screen:** Google TV doesn't let other apps add rows to its home screen, so the closest is the
  favourites row: go to **Apps**, highlight **Sketch Board**, **hold OK** → **Add to favourites**, then hold OK on it
  again → **Move** it to the front.
- Only the phone running the server can press keys or draw. Other devices on your Wi-Fi can view the board but get
  "403" for everything else. The drawing is saved in `state/sketch.json` and comes back after a restart.
- The phone's IP can change when it reconnects to Wi-Fi; tap **Show board on TV** again if the board says it can't
  reach your phone.

Sketch Board (`sketchboard/`) is built from this repo by GitHub Actions and published as a release here, so
`install-app` installs it the same way as any other app. It is signed with `sketchboard/debug.keystore`, a deliberately
public key whose only job is letting new builds install over old ones. Uninstall: `adb uninstall com.guptamanas8.sketchboard`.

## 6. Family games on the TV

Two party games, with everyone's phone as the controller. Players need nothing installed, only the same Wi-Fi.

1. On the phone running `bash tv.sh remote`, open the **Games** tab, pick **Draw & Guess** or **Quiz Buzzer**, then
   **Open on TV**.
2. Everyone scans the QR code on the TV (or types the address under it) and enters a name. To play on the host phone
   too, tap **Play on this phone too**.
3. Tap **Start**. **Skip** moves on, **Play again** restarts with the same players, **End** closes the game.

- **Draw & Guess** (2+ players): each player takes turns drawing a secret word on their phone; it appears on the TV
  while the others type guesses. Faster guesses score more, the drawer scores for each correct guess, and letters
  appear as hints halfway through. Near-miss guesses ("so close!") are only shown to the person who typed them.
- **Quiz Buzzer** (1+ players): a question with four answers on the TV; everyone taps an answer on their phone. Right
  answers score 500–1000 depending on speed, plus 100 for the fastest.
- **Highway Brawl** (1–4 players): an original retro motorbike racer in the style of the 90s classics. Race computer
  rivals along a winding country road and punch or kick riders next to you; health runs out and you're knocked off
  for a few seconds. Two or more players race in split screen on the TV. Phones show the controls (steer, gas, brake,
  punch, kick, optional auto-accelerate) plus your place, lap, speed and health. Points go by finishing place, plus 10
  per knockout. Choose laps and how many computer rivals in the Games tab; **End race** stops early.
- **Toot Boat** (1–4 players, co-op): everyone shares one boat on a winding river and pushes it by tooting from
  their seat. Toot together and the boat goes straight; toot alone and it turns. Hold your phone's button to charge a
  bigger toot, grab beans to refill gas and stars for points, and dodge rocks, drifting logs and whirlpools. A solo
  player gets both seats (left and right buttons). The whole crew shares one score from speed and stars. Toot sounds
  play on the TV and on each phone. An original game inspired by co-op "steer it together" party games.
- **Your own content:** add family words, or questions as `Question | right answer | wrong | wrong | wrong`, one
  per line. They're used first, topped up from the built-in lists in `remote/games_data.py`.
- Other phones can only join and play. They can't press TV keys, open apps or control the game.

## Cheat sheet

| Command | Does |
|---|---|
| `./tv.sh info` | model, chip, Android version, memory |
| `./tv.sh pkg-info <package>` | version, system/user app, installer, install date |
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
