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
| 1 | Audio capture | `scripts/check_mic.py` | scaffolded, **not yet run on hardware** |
| 2 | VAD segmentation | (tbd) | not started |
| 3 | whisper.cpp STT | (tbd) | not started |
| 4 | Bluetooth HID output | (tbd) | not started |
| 5 | LLM reformatting + profiles | (tbd) | not started |
| 6 | GPIO buttons | (tbd) | not started |

Stage 4 lands before stage 5 deliberately: an end-to-end path of
*speak -> transcribe -> type* is the useful milestone, and the LLM layer is
optional by design. Wiring the optional layer before the required one would mean
debugging both at once.

## Stage 0: provisioning the Pi

Flashed with Raspberry Pi Imager, using its customization dialog so the Pi joins
Wi-Fi and accepts SSH on first boot with no monitor attached.

| Setting | Value |
|---------|-------|
| OS | Raspberry Pi OS Lite (64-bit), Bookworm |
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
bash scripts/setup_pi.sh
./.venv/bin/python scripts/check_mic.py --list   # find the card
# set audio.device in config/default.yaml, then:
bash scripts/set_gain.sh                          # hardware capture gain
./.venv/bin/python scripts/check_mic.py           # record 5s and grade it
```

Run `check_mic.py` twice — once in silence to measure the noise floor, once
speaking normally. It reports peak level, noise floor, and SNR, and tells you
which gain knob to turn.

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

## Hardware notes

- **The Pi 5's USB-C port is power-only.** It has no USB gadget/data mode, so
  connecting it to a Mac with USB-C powers it but creates no network link. This
  is also why output is Bluetooth HID rather than USB gadget.
- No active cooler yet, so sustained whisper + LLM load will thermally throttle.
  Expect this to show up as latency variance in stages 3 and 5, not as errors.
- 15W USB-C supply rather than the full 27W. The Pi 5 will limit downstream USB
  current; a bus-powered USB device budget of 600mA applies.
