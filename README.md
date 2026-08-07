# AiMicrophone

Speak into a mic on a Raspberry Pi 5; the Pi transcribes locally, optionally
reshapes the text with a small local LLM, and types the result into a MacBook as
a Bluetooth HID keyboard. The Mac sees an ordinary keyboard.

## Layout

Code is edited on the Mac and runs on the Pi. `scripts/sync_to_pi.sh` pushes the
tree over rsync; the git history lives here on the Mac.

```
config/default.yaml   all tunables; the audio block is the hardware swap point
voicekb/              library code
scripts/              per-stage setup and verification tools
```

## Stages

Each stage is independently testable, and nothing moves forward until the stage
below it is verified on real hardware.

| # | Stage | Verify with | Status |
|---|-------|-------------|--------|
| 1 | Audio capture | `tests/test_audio.py`, `scripts/check_mic.py` | **working**; 38 dB SNR on speech |
| 2 | VAD segmentation | (tbd) | not started |
| 3 | whisper.cpp STT | `scripts/bench_whisper.sh` | **working**; base.en-q5_1 at 0.40x realtime |
| 4 | Bluetooth HID output | `voicekb/bt_hid.py --serve` | **working**; typed into macOS over an encrypted link |
| 5 | LLM reformatting + profiles | (tbd) | not started |
| 6 | GPIO buttons | (tbd) | not started |

Stage 4 lands before stage 5 deliberately: an end-to-end path of
*speak -> transcribe -> type* is the useful milestone, and the LLM layer is
optional by design. Wiring the optional layer before the required one would mean
debugging both at once.

## Stage 0: provisioning the Pi

Flashed with Raspberry Pi Imager v2.0.10, which provisions via cloud-init (user-data/network-config on the FAT boot partition), so the Pi joins
Wi-Fi and accepts SSH on first boot with no monitor attached.

| Setting | Value |
|---------|-------|
| OS | Raspberry Pi OS Lite (64-bit), Debian 13 Trixie |
| Hostname | `voicekb` (reachable as `voicekb.local`) |
| User | `tanishk` |
| SSH | public-key only, `~/.ssh/id_ed25519_pi` |
| Wi-Fi country | **US** — must be set, or the radio stays disabled |

An `ssh voicekb` alias is configured in `~/.ssh/config` on the Mac.

Two gotchas that cost real time if missed:

- **Wi-Fi country is not optional.** Leave it unset and the Pi 5's radio comes up
  administratively disabled with no obvious error — it looks like a bad password.
- **The Pi 5's USB-C port is power-only** and its USB-A ports are host ports, so
  no cable arrangement makes the Pi a USB device to a Mac. Networking has to come
  over Wi-Fi or Ethernet. This is the same reason output is Bluetooth HID.

## Stage 1: audio capture

On the Pi:

```bash
bash scripts/setup_pi.sh     # apt deps + venv
bash scripts/setup_alsa.sh   # define the "voicekbmic" plug device
bash scripts/set_gain.sh     # hardware capture gain
./.venv/bin/python tests/test_audio.py    # logic, no mic needed
./.venv/bin/python scripts/check_mic.py   # record and grade
```

### Why capture goes through an ALSA `plug` device

The USB capsule uses a Texas Instruments PCM2902 codec. PortAudio cannot open it
directly at 16 kHz — it fails with `paInvalidSampleRate` — even though `arecord`
negotiates that rate on the same device without complaint. PortAudio's ALSA
backend is simply stricter about rate negotiation.

`scripts/setup_alsa.sh` therefore defines a `plug`-wrapped PCM named
`voicekbmic` in `~/.asoundrc`, which converts rate and format transparently.
Config refers to that name rather than `hw:2,0`, which also means the ALSA card
number can change across reboots or USB ports without breaking anything.

Run `check_mic.py` twice — once in silence to measure the noise floor, once
speaking normally. It reports peak level, noise floor, and SNR, and tells you
which gain knob to turn.

`tests/test_audio.py` covers the pure logic and needs no microphone, so run it
first. If it passes and the mic still sounds wrong, the fault is in the device
or ALSA layer rather than in this code — which narrows the search a lot.

### Why gain order matters

The cheap USB capsule ships near-silent. Two knobs raise the level, and they are
not equivalent:

1. **`alsa_capture_percent`** (hardware, via `amixer`) amplifies early in the
   chain and costs comparatively little SNR. Always try this first.
2. **`audio.software_gain`** multiplies samples after capture. It lifts your
   voice and the noise floor by exactly the same amount, so it buys VAD
   reliability but no actual clarity.

Some very cheap USB mics expose no mixer control at all — their gain is fixed in
hardware. `set_gain.sh` says so explicitly if that's your case, and then
software gain is the only option.

Level matters more for VAD than for whisper: whisper normalizes its input, but a
VAD comparing against an absolute threshold will silently clip off quiet
syllables at the start and end of speech.

### Swapping in a beamforming array later

Downstream code consumes 16 kHz mono int16 frames and knows nothing about the
microphone. Moving to a ReSpeaker-style array is a `config/default.yaml` edit:
raise `channels`, set `channel_select` to the array's beamformed output channel,
and point `device` at the new card. No code below `voicekb/audio.py` changes.

## Stage 3: whisper.cpp STT

```bash
bash scripts/setup_whisper.sh              # build + fetch models
bash scripts/bench_whisper.sh /tmp/speech.wav
```

### Model choice: base.en-q5_1, decided by measurement

Benchmarked on this Pi 5 against a 6-second recording, 4 threads, no active
cooler:

| Model | Decoding | Wall time | Realtime factor | Peak SoC temp |
|-------|----------|-----------|-----------------|---------------|
| base.en-q5_1 | greedy | 2.40s | **0.40x** | 56 °C |
| base.en-q5_1 | beam 5 | 2.46s | **0.41x** | 60 °C |
| small.en-q5_1 | greedy | 35.22s | 5.87x | 75 °C |
| small.en-q5_1 | beam 5 | 19.61s | 3.27x | 82 °C |

`base.en-q5_1` wins on every axis that matters here. It transcribed the test
clip correctly, runs at 0.4x realtime — transcription finishes well before you
could have re-spoken the sentence — and stays cool.

`small.en` is not viable on this hardware. Beyond being 8x slower, it drove the
SoC to 81.8 °C and tripped the soft thermal limit: `vcgencmd get_throttled`
returned `0x80000` (bit 19, "soft temperature limit has occurred"). That is the
predicted no-active-cooler throttling showing up for real. Revisit only with
active cooling, and even then the latency is likely disqualifying for dictation.

Two details worth remembering:

- **Beam search is free at this size.** base.en costs 0.06s more for beam 5 than
  greedy, so there is no reason to give up the accuracy. The default should be
  beam search.
- **The 35s small.en greedy figure is misleading.** It was the first run, so it
  paid to read a 182 MB model off the microSD with a cold page cache. The 19.6s
  beam figure that followed is the warmer, fairer number. Model load time from
  SD is real and argues for keeping whisper resident rather than re-spawning it
  per utterance.

## Hardware notes

- **The Pi 5's USB-C port is power-only.** It has no USB gadget/data mode, so
  connecting it to a Mac with USB-C powers it but creates no network link. This
  is also why output is Bluetooth HID rather than USB gadget.
- No active cooler yet, so sustained whisper + LLM load will thermally throttle.
  Expect this to show up as latency variance in stages 3 and 5, not as errors.
- 15W USB-C supply rather than the full 27W. The Pi 5 will limit downstream USB
  current; a bus-powered USB device budget of 600mA applies.
