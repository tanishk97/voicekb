# Building your own voicekb

A start-to-finish guide to reproducing this device: a Raspberry Pi 5 that
listens on a USB mic and types what you say into a Mac over Bluetooth HID.

Every stage below ends with a **verification checkpoint**. Do not move on until
it passes. The whole design of this project is built around not debugging two
unproven layers at once, and the guide is arranged the same way.

Read [the gotchas](#gotchas-read-these-first) before you start. Each one cost
real debugging time here.

---

## Gotchas, read these first

| Gotcha | Symptom if you miss it |
|---|---|
| **Wi-Fi country must be set when flashing.** | The Pi 5's radio comes up administratively disabled with no obvious error. It looks exactly like a bad Wi-Fi password. |
| **The Pi 5's USB-C port is power-only.** | No cable arrangement makes the Pi a USB device to a Mac. Networking must come over Wi-Fi or Ethernet, and output must be Bluetooth, not USB gadget mode. |
| **ALSA config goes in `/etc/asound.conf`, not `~/.asoundrc`.** | Every interactive test passes (they run as your user) and the daemon dies with `No input device matching 'voicekbmic'` — immediately *after* accepting the Bluetooth connection, so an audio bug presents as a Bluetooth bug. The daemon runs as root, and root does not read `/home/<user>/.asoundrc`. |
| **BlueZ's `input` plugin must be disabled.** | It implements the HID *Host* role and binds L2CAP PSMs 17 and 19 — the exact two the HID *Device* needs. `bind()` fails with `EADDRINUSE`. |
| **macOS needs a `NoInputNoOutput` agent for Just Works pairing.** | With any other agent macOS asks for a PIN to be typed *on the keyboard being paired* — impossible before that keyboard is paired. |
| **A stale ACL link leaves the daemon stuck in `accept()`.** | macOS keeps the baseband link open and believes it is still connected, so it never reopens the L2CAP channels. The Pi sits in `accept()` forever with no log output at all, while the Mac shows a live connection. Toggle Bluetooth on the Mac, or "Forget" and re-pair. |

---

## Bill of materials

| Item | Notes |
|---|---|
| Raspberry Pi 5 | The build here has **no active cooler**. See the thermal caveat below — an active cooler is the single best upgrade. |
| microSD card | Must hold Raspberry Pi OS Lite, two C++ build trees (whisper.cpp and llama.cpp), and roughly 2 GB of model weights. |
| USB-C power supply | **27 W USB-C PD is what the Pi 5 wants (5 V/5 A).** This build ran on a 15 W supply and hit under-voltage — see the caveat below. |
| A cheap single-capsule USB microphone | The one used here presents a Texas Instruments PCM2902 codec. Quiet, no noise rejection. Anything USB-audio-class will work; see §2 for the codec workaround. |
| Wi-Fi (or Ethernet) | Required — the Pi cannot reach the Mac over USB. |
| A MacBook | The HID host, and also where you edit code. |

### Hardware caveats

**No active cooler.** Sustained whisper + LLM load will thermally throttle.
Measured on this build: building llama.cpp peaked at **77 °C** (16 minutes of
CPU, no throttle event), and an earlier `small.en` inference run hit **81.8 °C**
and tripped the soft thermal limit — `vcgencmd get_throttled` returned
`0x80000`. Expect throttling to show up as *latency variance*, not as errors.
Do not build and run inference at the same time; `setup_llama.sh` deliberately
holds one core back for this reason.

**15 W supply is marginal.** After the llama.cpp build,
`vcgencmd get_throttled` reported `0x50000` — under-voltage *and* throttling had
both occurred. Under-voltage risks SD-card corruption, which is a worse failure
than heat, so a 27 W supply is the higher-priority purchase of the two. A 15 W
supply also limits downstream USB current; budget 600 mA for bus-powered USB
devices.

---

## Stage 0 — flash and provision the Pi

Flashed with **Raspberry Pi Imager v2.0.10**, which provisions via **cloud-init**
(`user-data` / `network-config` on the FAT boot partition) rather than the older
`custom.toml` mechanism. That difference cost real debugging time here; if you
follow an older guide expecting `custom.toml`, your settings will not be applied.

| Setting | Value used |
|---|---|
| OS | Raspberry Pi OS Lite (64-bit), Debian 13 Trixie |
| Hostname | `voicekb` (reachable as `voicekb.local`) |
| User | `tanishk` (pick your own) |
| SSH | public-key only |
| Wi-Fi country | **US — must be set, or the radio stays disabled** |

Set Wi-Fi SSID, password, and country in the Imager's OS-customisation screen so
the Pi joins the network and accepts SSH on first boot with no monitor attached.

Add a convenience alias on the Mac in `~/.ssh/config`:

```
Host voicekb
  HostName voicekb.local
  User tanishk
  IdentityFile ~/.ssh/id_ed25519_pi
```

### Checkpoint 0

```bash
ssh voicekb          # from the Mac
```

You should land in a shell. If the Pi is unreachable, check the Wi-Fi country
setting first — a disabled radio is the most likely cause, and it presents as a
network problem rather than a configuration one.

> **When scanning the LAN for the Pi**, match on `voicekb.local` or a Debian SSH
> banner. Do *not* match on "port 22 is open" — other hosts on the network answer
> there too, and that decoy has already produced one false positive here.

---

## Stage 1 — audio capture

Get the code onto the Pi first. Clone the repo on your **Mac** (that is where
the git history lives and where you will edit), then push it over:

```bash
# on the Mac, from the repo root
PI_HOST=tanishk@voicekb.local scripts/sync_to_pi.sh
```

See [WORKFLOW.md](WORKFLOW.md) for why the code lives on the Mac and why you
must never edit directly on the Pi.

Now, **on the Pi**:

```bash
cd ~/AiMicrophone
bash scripts/setup_pi.sh      # apt deps + venv + requirements.txt
bash scripts/setup_alsa.sh    # define the "voicekbmic" plug device
bash scripts/set_gain.sh      # hardware capture gain, default 62%
```

`setup_pi.sh` installs `alsa-utils`, `libportaudio2`, `python3-venv`,
`python3-dev` and `git`, then creates `.venv` with `--system-site-packages` (so
a piwheels-built numpy can be reused; building numpy from source on a Pi is
slow) and installs `requirements.txt`.

`setup_alsa.sh` auto-detects the USB capture card and writes
**`/etc/asound.conf`** defining a `plug`-wrapped PCM named `voicekbmic`. Pass a
card number to override: `bash scripts/setup_alsa.sh 3`.

`set_gain.sh` walks the card's mixer controls and sets every capture volume to
62 %, then persists it with `alsactl store`. If your mic exposes **no mixer
control at all** — some very cheap USB capsules have fixed hardware gain — the
script says so explicitly and exits 2; raise `audio.software_gain` in
`config/default.yaml` instead.

### Checkpoint 1

Run the logic tests first. They need no microphone, so if they pass and the mic
still sounds wrong, the fault is in the device or ALSA layer rather than in the
code — which narrows the search a lot.

```bash
./.venv/bin/python tests/test_audio.py     # no mic needed
./.venv/bin/python scripts/check_mic.py --list
./.venv/bin/python scripts/check_mic.py    # records 5s and grades it
```

`check_mic.py --list` shows both `arecord -l` and what PortAudio actually sees.
`voicekbmic` must appear in the PortAudio list.

**Run the recording test twice** — once in silence to measure the noise floor,
once speaking normally at your intended distance. It reports peak level, noise
floor and SNR, and names which gain knob to turn.

Targets: peak between **−18 and −1 dBFS**, **SNR above 20 dB**. This build
measured **38 dB SNR**, and 62 % gain was chosen because 81 % peaked at
**−1.6 dBFS** — only 1.6 dB of headroom, which normal speech variation would
blow through. Clipped audio degrades whisper far more than a quiet signal does.

**Gain order matters.** Always raise `alsa_capture_percent` (hardware, via
`set_gain.sh`) before `audio.software_gain`. Hardware gain amplifies early and
costs comparatively little SNR; software gain lifts your voice and the noise
floor by exactly the same amount.

Keep the WAV — the next two stages tune against it:

```bash
./.venv/bin/python scripts/check_mic.py --out /tmp/speech.wav
```

---

## Stage 2 & 3 — VAD segmentation and whisper.cpp

```bash
bash scripts/setup_whisper.sh      # clone + build + fetch base.en and small.en (q5_1)
```

Built from source rather than pip-installed, to get the ARM NEON / fp16 paths
the Pi 5's Cortex-A76 cores provide and control over thread count. This is the
slow part. Watch the temperature.

To fetch only one model: `bash scripts/setup_whisper.sh base.en-q5_1`.

### Checkpoint 3a — benchmark

```bash
bash scripts/bench_whisper.sh /tmp/speech.wav
```

It runs every model in `models/` in both greedy and beam-5 modes and prints wall
time, realtime factor, and SoC temperature, ending with `vcgencmd get_throttled`
(`0x0` means no throttling occurred).

Reference numbers from this build (6-second clip, 4 threads, no active cooler):

| Model | Decoding | Wall time | Realtime factor | Peak SoC temp |
|---|---|---|---|---|
| base.en-q5_1 | greedy | 2.40 s | **0.40x** | 56 °C |
| base.en-q5_1 | beam 5 | 2.46 s | **0.41x** | 60 °C |
| small.en-q5_1 | greedy | 35.22 s | 5.87x | 75 °C |
| small.en-q5_1 | beam 5 | 19.61 s | 3.27x | 82 °C |

A realtime factor under ~1.0x is the practical bar for dictation. `small.en` is
not viable without active cooling — it tripped the 80 °C soft limit here. If
base.en is too slow for you, `tiny.en-q5_1` runs in 1.10 s vs base's 2.45 s
(2.2x faster) and is a one-line change to `stt.model`.

Ignore the first run of any model: it pays a cold page-cache cost reading the
weights off the microSD. Warm model load is 72 ms.

### Checkpoint 3b — the offline pipeline

```bash
./.venv/bin/python scripts/transcribe_file.py /tmp/speech.wav
```

This runs VAD → whisper → normalization over a fixed recording with no mic and
no Bluetooth involved, so you can tune segmentation by re-running rather than by
talking at the mic repeatedly. It prints how many utterances the VAD found, the
raw whisper text, the normalized text, and warns if any untypeable characters
survive normalization.

`--no-vad` transcribes the whole file as one utterance, which is useful for
separating "the VAD is wrong" from "whisper is wrong".

If it reports no speech, lower `vad.aggressiveness` or check the recording.

**Tuning note:** `vad.silence_ms` is the main latency dial. It defaults to
**1200 ms** here. It started at 700 ms and split single sentences in two,
because an ordinary thinking pause exceeded the window. Shorter feels snappier
but fragments your sentences; every millisecond is added to every utterance.
There is no *throughput* reason to prefer short segments — whisper's encoder
processes a fixed 30-second window regardless, so 0.5 s of audio costs 2.40 s
and 6 s costs 2.46 s.

---

## Stage 4 — Bluetooth HID output

```bash
bash scripts/setup_bluetooth.sh
```

Two changes, both necessary:

1. **Class of Device → `0x002540`** (Peripheral / Keyboard) in
   `/etc/bluetooth/main.conf`. macOS decides what icon and pairing flow to use
   from this. Left at the default, the Mac treats the Pi as a generic computer
   and will not offer to use it as a keyboard.
2. **`bluetoothd --noplugin=input`**, via a systemd drop-in at
   `/etc/systemd/system/bluetooth.service.d/10-hid-device.conf`. BlueZ's
   built-in `input` plugin implements the HID *Host* role and binds L2CAP
   PSMs 17 and 19 — the exact two our HID *Device* needs. With the plugin
   loaded, `bind()` fails with `EADDRINUSE`.

Both are reversible: `bash scripts/setup_bluetooth.sh --revert`.

The script prints the running `--noplugin` flag, the device class, and the
service state at the end. Confirm `--noplugin=input` actually appears.

### Pairing agent

macOS needs **Just Works** pairing, which requires a `NoInputNoOutput` agent on
the Pi. Otherwise macOS asks for a PIN to be typed on the keyboard being
paired — which is impossible, because that keyboard is the thing you are trying
to pair. In `bluetoothctl`:

```bash
sudo bluetoothctl
[bluetooth]# agent NoInputNoOutput
[bluetooth]# default-agent
[bluetooth]# discoverable on
[bluetooth]# pairable on
```

Once the Mac has paired, trust it so it can reconnect without re-pairing:

```bash
[bluetooth]# trust <MAC-ADDRESS>
```

Trusting the host is what fixed the original stale-link failure here.

### Checkpoint 4

Two levels. First, validate the descriptor and SDP record without touching
Bluetooth at all, and run the pure keycode tests:

```bash
./.venv/bin/python tests/test_hid.py
./.venv/bin/python -m voicekb.bt_hid --check
```

Then serve for real (root is required — binding low L2CAP PSMs is privileged):

```bash
sudo ./.venv/bin/python -m voicekb.bt_hid --serve
```

It registers the HID profile, binds PSM 17 and 19, and waits. On the Mac, open
**System Settings → Bluetooth**, find `voicekb`, and connect. When the Pi prints
`connected:`, it types `hello from voicekb` into whatever has focus on the Mac.
Have a text editor focused.

Pass `--text` to type something else.

> **macOS will show a "Keyboard Setup Assistant"** asking for a keypress to
> identify the layout. This is cosmetic — typing works regardless. Quit the
> dialog. Fixing it properly needs the Pi to send the key right of left Shift
> (`Z` on ANSI) on demand, which requires a control channel into the running
> daemon that does not exist yet.

> **If the Pi sits silently in `accept()` forever** with the Mac showing a live
> connection, that is the stale half-open ACL link. Toggle Bluetooth off and on
> on the Mac, or "Forget This Device" and re-pair.

**Dependency note:** `voicekb/bt_hid.py` imports `dbus` and `gi` (PyGObject).
No setup script installs these, and `requirements.txt` does not list them — the
venv is created with `--system-site-packages`, so system packages are visible to
it. If `import dbus` or `from gi.repository import GLib` fails, install them
with apt:

```bash
sudo apt install -y python3-dbus python3-gi
```

---

## Stage 5 — LLM reformatting (optional)

This layer is optional by design. The pipeline runs without it and types the raw
transcription, logging a warning. Stage 4 landed first here deliberately:
*speak → transcribe → type* is the useful milestone, and wiring the optional
layer before the required one means debugging both at once.

```bash
bash scripts/setup_llama.sh          # build llama.cpp + fetch two models
bash scripts/serve_llm.sh --service  # resident server on 127.0.0.1:8080
```

`setup_llama.sh` fetches two models so the tradeoff can be measured rather than
guessed — `Llama-3.2-1B-Instruct` (~0.8 GB) and `Qwen2.5-1.5B-Instruct`
(~1.1 GB), both Q4_K_M. It builds **both `llama-cli` and `llama-server`**; the
server is the one that matters, because `voicekb/llm.py` talks to its
OpenAI-compatible API.

It builds with `nproc - 1` jobs deliberately. With no active cooler, `-j4`
pushed the SoC to 72 °C. Peak temperature is the binding constraint here, not
build time. Override with `JOBS=4` if you have added cooling.

`serve_llm.sh` reads model, threads and context size from
`config/default.yaml` so there is one source of truth, and binds the server to
**loopback only** — it is an unauthenticated inference endpoint and there is no
reason to expose it on a home network. Loading Qwen2.5-1.5B takes ~8 s.

Other forms: `bash scripts/serve_llm.sh` (foreground),
`bash scripts/serve_llm.sh --stop`, or `MODEL=... bash scripts/serve_llm.sh`.

### Checkpoint 5

```bash
curl -s localhost:8080/health
./.venv/bin/python tests/test_llm.py      # no server needed
./.venv/bin/python scripts/bench_llm.py   # needs the server
```

`bench_llm.py` runs every profile over real whisper output captured from this
device — including the `"voice he be working"` mangling — plus a prompt-injection
probe. Expect Qwen2.5-1.5B at **0.7–2.1 s** per utterance, on top of whisper's
~2.5 s.

Note that the `clean` profile no longer calls the model at all (see
[ARCHITECTURE.md §5.11](ARCHITECTURE.md#511-clean-does-deterministic-filler-removal-not-llm-cleanup)),
so its rows report 0.0 s and `[UNCHANGED]`. To exercise the model, benchmark the
profiles that do use it:

```bash
./.venv/bin/python scripts/bench_llm.py --profiles slack,commit,email
```

---

## Running it

The LLM server first, if you want the reformatting layer:

```bash
bash scripts/serve_llm.sh --service    # ~8s to load Qwen2.5-1.5B
```

Then the pipeline. It runs as root for the L2CAP bind:

```bash
cd ~/AiMicrophone
sudo systemd-run --unit=voicekb --working-directory=$PWD \
  ./.venv/bin/python -u scripts/run_voicekb.py
journalctl -u voicekb -f          # watch it work
```

Connect `voicekb` from the Mac's Bluetooth menu when the log says it is waiting.
Then speak. Text appears wherever the Mac has focus.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Transcribe and log but never type. No Bluetooth needed — the fastest way to test the audio half. |
| `--profile commit` | Override `llm.profile` without editing config. Also `clean`, `slack`, `email`, `raw`. |
| `--no-llm` | Type the raw transcription, bypassing the LLM. |
| `--no-trailing-space` | Do not append a space after each utterance. |
| `--delay-ms` | Inter-report delay, default 15 ms. Raise it if an application drops characters. |

The startup sequence deliberately opens the mic **before** touching Bluetooth,
so an audio fault does not present as a Bluetooth fault. If it fails there, the
log tells you to run `check_mic.py --list` and reminds you about
`/etc/asound.conf`.

Expected latency per utterance is roughly `vad.silence_ms` (1200 ms) + ~2.4 s of
whisper + typing time, plus 0.7–2.1 s if a model-using profile is active.

---

## Tuning after it works

Add your own mis-transcriptions as you find them. whisper cannot emit a word
outside its vocabulary, so invented terms come back mangled *the same way every
time* — which is what makes a lookup table work where audio tuning cannot:

```yaml
stt:
  substitutions:
    "voice he be": voicekb
```

Explicit phrases only — there is deliberately no fuzzy matching, because a
similarity matcher would eventually rewrite a word you actually said.

Other dials worth knowing, all in `config/default.yaml` and all commented there
with the reasoning:

| Setting | Default | What it trades |
|---|---|---|
| `vad.silence_ms` | 1200 | Latency vs. sentences staying whole. |
| `vad.aggressiveness` | 2 | Fewer false triggers vs. clipping quiet syllables. Raise to 3 if room noise keeps opening utterances. |
| `vad.min_level_dbfs` | −42 | Energy gate. Sits in the measured gap between a −53 dBFS noise floor and −15 dBFS speech. |
| `stt.model` | base.en-q5_1 | Accuracy vs. latency. `tiny.en-q5_1` is 2.2x faster. |
| `stt.beam_size` | 5 | Effectively free at this model size — decode is 16 ms of a 2456 ms total. |
| `audio.software_gain` | 1.0 | Last resort. Raise `alsa_capture_percent` first. |
| `llm.profile` | clean | `clean` is deterministic and instant; `slack`/`commit`/`email` call the model. |
