# CHIPTUNE QUEST Vol.1 — Original 8-bit BGM Library

**15 original NES-style chiptune tracks for games. Loop-ready. 100% code-generated. 100% original.**

Three themed suites cover the core moments of a retro-style game: **battle & advancement**, a
**knights/banner suite** in the classic SRPG idiom, and an **exotic journey** across desert,
shrine, and ruins scales. Every loop track is rendered at an exact musical bar boundary, so the
end of the file flows seamlessly back into the start — no gap, no click, no edit.

| Suite | Tracks | Character |
|---|---|---|
| A. Battle & Advance | 5 loops | field battle, world-map march, fast encounter, danger, camp |
| B. Knights' Banner | 3 loops + 2 stingers | player-phase march, enemy assault, final boss, victory fanfare, level-up jingle |
| C. Exotic Journey | 5 loops | Hijaz desert caravan, Hirajoshi shrine, double-harmonic ruins, tresillo festival, harbor bazaar |

Total runtime ≈ 13.5 minutes · full track table in [`docs/TRACKLIST.md`](docs/TRACKLIST.md) ·
audition in [`preview/`](preview/) (128 kbps MP3).

## Why "code-generated" matters

Every track here was composed and synthesized by `engine/synth_bgm_shop.py` — a dependency-free
Python + NumPy chiptune engine (square / triangle / sine / noise voices, one-pole filters, a
16th-note slot sequencer, per-voice panning, and −3 dBFS peak normalization). That means:

- **Reproducible** — `python engine/synth_bgm_shop.py` regenerates all 15 WAVs + MIDI files
  byte-identically (fixed RNG seed);
- **Tweakable** — change a tempo, a chord, a gain, and re-render; the whole library is source;
- **Loop-native** — renders extend past the loop point and the tail is wrapped back onto bar 0,
  so loop seams measure < 3.5% of the file's maximum sample step.

The mix targets (bass-heavy balance, restrained mids, filtered hats/snare) were tuned against
measurements taken from classic NES SRPG battle themes — tempo and key anchors included — while
all melodies and arrangements are original.

## Using the tracks in your game

1. Import the OGG files (included in the full pack) into Unity / Unreal / Godot / GameMaker.
2. Set the audio source to **loop** — the files are seamless at `loop start = 0`,
   `loop end = file end`.
3. Exact loop lengths per track are listed in `docs/TRACKLIST.csv` (`loop_sec` column), useful
   for engines that want an explicit loop region.
4. `b4_victory_fanfare` and `b5_levelup_jingle` are one-shot stingers — do **not** loop them.

## Formats

| Content | Where |
|---|---|
| Preview audio (MP3 128k) | `preview/` in this repo |
| MIDI (all 4 voices, GM-mapped) | `midi/` in this repo |
| Full pack: OGG Q6 + MP3 192k + MIDI + license + cover | the sellable pack (see itch.io listing) |
| 48 kHz WAV masters | regenerable from `engine/` anytime |

## License

- **Code** (`engine/`): MIT — see [LICENSE-CODE.md](LICENSE-CODE.md).
- **Music** (audio & MIDI): royalty-free license — see [LICENSE-MUSIC.md](LICENSE-MUSIC.md).
  Commercial games allowed, unlimited projects, no attribution required. Resale/redistribution
  of the files themselves and AI-training use are not permitted.

## 中文说明

**CHIPTUNE QUEST Vol.1** 是一套 15 首的原创 NES 风格芯片音乐游戏曲库：战斗与进阶（5 首）、
骑士团旗 SRPG 风（3 循环 + 2 短曲）、异域之旅（5 首）。全部由 `engine/synth_bgm_shop.py`
纯代码合成——只依赖 numpy，改参数即可重渲整个曲库；所有循环曲在小节边界精确收口，
首尾无缝衔接，游戏引擎里直接开 loop 即可。音乐成品按免版税授权商用（详见
LICENSE-MUSIC.md）：可商用、不限项目数、无需署名；禁止转售文件本体与 AI 训练。
仓库内 `preview/` 为 128k 试听版，完整上架包（OGG Q6 + MP3 192k + MIDI + 授权文本 + 封面）
在 itch.io 商品页提供。
