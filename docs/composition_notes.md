# Composition & Engine Notes

Technical notes on how CHIPTUNE QUEST Vol.1 was made, and why it sounds the way it does.

## Engine

`engine/synth_bgm_shop.py` — Python 3 + NumPy only, no external audio dependencies.

- **Voices**: square wave with selectable duty cycle (12.5% / 25% / 50%), triangle, sine,
  and white-noise percussion — the classic 2A03 (NES APU) instrumentation: two pulse channels,
  triangle bass, noise drums.
- **Filters**: one-pole IIR high-pass (snare 1.5 kHz, hats 5 kHz) and low-pass (kick ~320 Hz)
  per hit, in pure NumPy.
- **Sequencer**: 16th-note slot grid (`bar`, `slot`), stereo panner (equal-power), peak
  normalization to −3 dBFS, 48 kHz stereo WAV output.
- **Loop-native rendering**: the renderer schedules `bars + tail` bars, then wraps any energy
  beyond the loop boundary back onto bar 0 before truncation. Measured end→start discontinuity
  is under 3.5% of each file's maximum sample step (most tracks under 2%), i.e. inaudible.
- **MIDI export**: the same note events that drive the synthesizer are mirrored into a
  General MIDI type-1 file (ch1 lead square, ch2 harmony square, ch3 triangle bass,
  ch10 drums) — audio and MIDI can never drift apart.

## Style anchoring (measured, not sampled)

Mix and groove targets were calibrated against acoustic measurements of classic NES SRPG
battle themes (tempo, key, event density, band energy distribution, spectral centroid).
Anchors used for Suite B:

| Track | Anchor | Tempo | Key |
|---|---|---|---|
| b1 Knights Advance | player-phase map theme | 132 | B♭ major |
| b2 Iron Tide | mid-boss assault | 115 | C minor |
| b3 Dark Lord | final boss | 100 | C minor |
| b4 Victory Fanfare | stage-clear fanfare | 123 | G major |

Mix balance follows the same measurements: bass-heavy spectrum (≈ 60–75% of energy below
150 Hz), deliberately restrained but present midrange (15–27%) so melodies stay audible over
the engine-room bass, filtered noise percussion (snare HP 1.5 kHz, hats HP 5 kHz at low gain).

## Signature devices (inherited from the engine lineage)

- **engine_bass** — continuous eighth-note "pump" (root, root, fifth, root) that never rests;
  on designated *breathing bars* it ducks 25–27% while drums drop to hats only, creating
  dynamics inside a constant-energy chiptune mix.
- **Half-cadence loop endings** — loop tracks end on the dominant (V) so the return to bar 1
  carries its own forward momentum.
- **arp_tail** — 16th-note arpeggio fills only on the back half of phrase-ending bars
  (density = punctuation, not wallpaper).
- **Call & response** — low-register question phrases answered by high-register replies
  (e.g. b2's B5→C6 leading-tone answer), with a one-beat gap between phrases.
- **Exotic color** — Suite C uses Hijaz / Hirajoshi / double-harmonic scales with maqsum,
  taiko, and tresillo (3+3+2) rhythm engines instead of standard backbeats.

## Originality

All melodies, harmonies, and arrangements in this library were written from scratch as note
data in this repository. No note sequences, recordings, or rips from any existing game are
included; only quantitative style parameters (numbers) informed the design. The full
composition is inspectable in `engine/synth_bgm_shop.py`.
