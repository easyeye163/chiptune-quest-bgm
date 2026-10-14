#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CHIPTUNE QUEST Vol.1 — itch.io 上架曲库（15 曲，loop 原生渲染 + MIDI 同步导出）

套房:
  A 战斗与进阶      a1-a5（5 loop）
  B 骑士团旗(FE 风)  b1-b3（3 loop）+ b4 胜利号角 / b5 升级铃音（one-shot）
  C 异域之旅        c1-c5（5 loop）

技术要点（v2 引擎 DNA）:
  - 16 分 slot 音序器, Song 类立体声声像混音, 峰值归一 -3 dBFS, 48 kHz
  - loop 原生: 渲染 bars+tail, 尾部能量回卷曲头后截齐 loop_n -> 无缝循环
  - engine_bass 八分连泵 r r r5 r（duck 撤退小节轻收）、arp_tail 句尾 16 分琶音、
    light bars 呼吸（仅 hat 0.012）、半终止循环收尾
  - 混音锚点(v2 实测收敛): lead 0.115, bass 0.30, snare hp1500, hat hp5000 g0.020,
    pads <=0.03 duty0.5, arp duty0.25 少量 —— 中频刻意保留 19-23% 保证旋律可闻
  - 全部旋律原创；风格参数参考（FE 实测锚定 BPM/调性、异域音阶/节奏型），无版权素材
输出: shop_out/<file>.wav + .mid + TRACKLIST.csv
"""
import re
import wave
import numpy as np
from pathlib import Path

SR = 48000
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "shop_out"
OUT.mkdir(exist_ok=True)

rng = np.random.default_rng(7)

# ---------------- 基础合成 ----------------
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def parse_name(name):
    s = NOTE[name[0]]
    i = 1
    while i < len(name) and name[i] in "#b":
        s += 1 if name[i] == "#" else -1
        i += 1
    return s, int(name[i:])


def hz(name):
    s, o = parse_name(name)
    return 440.0 * 2 ** ((12 * (o + 1) + s - 69) / 12)


def midi_of(name):
    s, o = parse_name(name)
    return 12 * (o + 1) + s


def shift(name, semi):
    s, o = parse_name(name)
    s += semi
    o += s // 12
    s %= 12
    return FLAT[s] + str(o)


def tvec(dur):
    return np.arange(int(round(dur * SR))) / SR


def env(dur, a=0.004, r=0.05):
    x = tvec(dur)
    e = np.ones_like(x)
    na = max(1, int(a * SR))
    nr = min(max(1, int(r * SR)), len(e))
    e[:na] = np.linspace(0, 1, na)
    e[-nr:] *= np.linspace(1, 0, nr)
    return e


def w_square(f, dur, duty=0.25):
    x = tvec(dur)
    return np.sign(((x * f) % 1.0) - duty) * env(dur)


def w_tri(f, dur):
    x = tvec(dur)
    return (2 / np.pi) * np.arcsin(np.sin(2 * np.pi * f * x)) * env(dur, r=0.06)


def w_sine(f, dur):
    return np.sin(2 * np.pi * f * tvec(dur)) * env(dur)


def w_noise(dur):
    return rng.uniform(-1, 1, int(round(dur * SR)))


def hp1(x, fc):
    a = 1 / (1 + 2 * np.pi * fc / SR)
    y = np.empty_like(x)
    prev = 0.0
    px = 0.0
    for i in range(len(x)):
        y[i] = a * (prev + x[i] - px)
        prev = y[i]
        px = x[i]
    return y


def lp1(x, fc):
    k = 2 * np.pi * fc / SR
    a = k / (k + 1)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a * (x[i] - acc)
        y[i] = acc
    return y


# ---------------- Song ----------------
CH_IDS = {"lead": 0, "harm": 1, "bass": 2}
PROG = {0: 80, 1: 80, 2: 38}  # MIDI program: square lead x2, synth bass
PERC_PITCH = {"kick": 36, "snare": 38, "hat": 42, "tom": 45, "taiko": 41, "rim": 37}


class Song:
    def __init__(self, bpm, bars, tail_bars=1):
        self.bpm = bpm
        self.bars = bars
        self.spb = 60.0 / bpm
        self.slot_dur = self.spb / 4.0
        self.loop_n = int(round(bars * 4 * self.spb * SR))
        tail_n = int(round(tail_bars * 4 * self.spb * SR))
        self.n_total = self.loop_n + tail_n
        self.L = np.zeros(self.n_total)
        self.R = np.zeros(self.n_total)
        self.ev = {c: [] for c in (0, 1, 2, 9)}

    def T(self, bar, slot):
        return (bar * 16 + slot) * self.slot_dur

    def _mix(self, sig, t, gain, pan):
        s = int(round(t * SR))
        if s >= self.n_total or len(sig) == 0:
            return
        sig = sig[: self.n_total - s] * gain
        a = (pan + 1) * np.pi / 4
        self.L[s:s + len(sig)] += sig * np.cos(a)
        self.R[s:s + len(sig)] += sig * np.sin(a)

    def note(self, ch, bar, slot, name, dur_slots, gain, pan=0.0,
             duty=0.25, wave="square", midi=True):
        dur = dur_slots * self.slot_dur
        f = hz(name)
        if wave == "square":
            sig = w_square(f, dur, duty)
        elif wave == "tri":
            sig = w_tri(f, dur)
        else:
            sig = w_sine(f, dur)
        self._mix(sig, self.T(bar, slot), gain, pan)
        if midi:
            vel = max(1, min(127, int(gain * 520)))
            self.ev[CH_IDS[ch]].append((bar * 16 + slot, dur_slots, midi_of(name), vel))

    def perc(self, kind, bar, slot, gain, pan=0.0):
        t = self.T(bar, slot)
        if kind == "kick":
            sig = lp1(w_sine(84, 0.10) * np.exp(-tvec(0.10) * 30), 320)
        elif kind == "snare":
            sig = hp1(w_noise(0.09), 1500) * np.exp(-tvec(0.09) * 28)
            body = w_sine(196, 0.05) * np.exp(-tvec(0.05) * 50)
            self._mix(body * 0.5 * gain, t, 1.0, pan)
        elif kind == "hat":
            sig = hp1(w_noise(0.03), 5000) * np.exp(-tvec(0.03) * 150)
        elif kind == "tom":
            sig = lp1(w_sine(150, 0.14) * np.exp(-tvec(0.14) * 22), 700)
        elif kind == "taiko":
            sig = lp1(w_sine(64, 0.30) * np.exp(-tvec(0.30) * 11), 240)
            thud = lp1(w_noise(0.06), 400) * np.exp(-tvec(0.06) * 40) * 0.8
            sig[: len(thud)] += thud
        else:  # rim
            sig = hp1(w_noise(0.02), 2200) * np.exp(-tvec(0.02) * 220)
        self._mix(sig, t, gain, pan)
        self.ev[9].append((bar * 16 + slot, 1, PERC_PITCH[kind],
                           max(1, min(127, int(gain * 520)))))

    def finish(self, name, loop=True, trim=False):
        mix = np.stack([self.L, self.R], axis=1)
        if loop:
            tn = self.n_total - self.loop_n
            if tn > 0:
                tail = mix[self.loop_n:]
                fade = np.linspace(1.0, 0.0, tn) ** 0.5
                mix[:tn] += tail * fade[:, None]
            mix = mix[: self.loop_n]
        elif trim:
            amp = np.max(np.abs(mix), axis=1)
            idx = np.where(amp > 10 ** (-50 / 20))[0]
            if len(idx):
                end = min(idx[-1] + int(0.20 * SR), len(mix))
                mix = mix[:end]
            nf = int(0.03 * SR)
            mix[-nf:] *= np.linspace(1, 0, nf)[:, None]
        peak = float(np.max(np.abs(mix))) or 1.0
        mix = mix / peak * (10 ** (-3 / 20))
        data = (mix * 32767).astype(np.int16)
        with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data.tobytes())
        d = np.abs(np.diff(mix.astype(np.float64), axis=0))
        jump = float(np.max(np.abs(mix[-1].astype(np.float64) - mix[0].astype(np.float64))))
        glob = float(d.max())
        rms = float(np.sqrt(np.mean(mix ** 2)))
        out = dict(name=name, dur=len(mix) / SR,
                   rms_db=20 * np.log10(rms + 1e-12),
                   seam_ratio=jump / max(glob, 1e-9))
        if not loop:
            tail_rms = float(np.sqrt(np.mean(mix[-int(0.06 * SR):] ** 2)))
            out["tail_rms_db"] = 20 * np.log10(tail_rms + 1e-12)
        return out


# ---------------- 编曲助手 ----------------
CH_RE = re.compile(r"^([A-G])([#b]?)(\d)(m?)$")


def parse_ch(ch):
    m = CH_RE.match(ch)
    return m.group(1) + m.group(2), int(m.group(3)), m.group(4) == "m"


def put(S, ch, bar, notes, gain, pan=0.12, duty=0.25, wave="square"):
    for sl, nm, ds in notes:
        S.note(ch, bar, sl, nm, ds, gain, pan, duty, wave)


def emit(S, ch, start_bar, bars_data, gain, pan, duty, wave="square"):
    for i, bd in enumerate(bars_data):
        put(S, ch, start_bar + i, bd, gain, pan, duty, wave)


def engine_bass(S, prog, gain=0.30, pan=-0.08, style="pump", duck=None):
    duck = duck or {}
    for bar, ch in enumerate(prog):
        root, octv, mq = parse_ch(ch)
        r = f"{root}{octv}"
        g = gain * duck.get(bar, 1.0)
        if style == "pump":
            r5 = shift(r, 7)
            for i, sl in enumerate(range(0, 16, 2)):
                S.note("bass", bar, sl, r5 if i % 4 == 2 else r, 1.8, g, pan, wave="tri")
        elif style == "half":
            S.note("bass", bar, 0, r, 7.5, g, pan, wave="tri")
            S.note("bass", bar, 8, shift(r, 7), 7.5, g, pan, wave="tri")
        else:  # stacc
            for sl in (0, 4, 8, 12):
                S.note("bass", bar, sl, r, 1.6, g, pan, wave="tri")


def arp_tail(S, prog, bars, gain=0.022, octave=4, duty=0.25, tail=8, pan=-0.15):
    for bar in bars:
        root, octv, mq = parse_ch(prog[bar])
        base = f"{root}{octave}"
        seq = [0, 3, 7, 12, 7, 3] if mq else [0, 4, 7, 12, 7, 4]
        for k, sl in enumerate(range(16 - tail, 16)):
            S.note("harm", bar, sl, shift(base, seq[k % len(seq)]), 1, gain, pan, duty)


def pads(S, prog, bars=None, gain=0.03, duty=0.5, pan=-0.18):
    for bar, ch in enumerate(prog):
        if bars is not None and bar not in bars:
            continue
        root, octv, mq = parse_ch(ch)
        b = f"{root}{octv + 2}"
        S.note("harm", bar, 0, b, 8, gain, pan, duty)
        S.note("harm", bar, 8, shift(b, 3 if mq else 4), 8, gain, pan, duty)


def drone(S, bars, names, gain=0.04, pan=-0.15):
    for bar in range(bars):
        for nm in names:
            S.note("harm", bar, 0, nm, 16, gain, pan, wave="tri")


def drums_std(S, bars, kick=(0, 8), snare=(4, 12), hat8=True, light=(), rolls=(),
              gk=0.30, gs=0.16, gh=0.020):
    for bar in range(bars):
        if bar in light:
            for sl in (0, 4, 8, 12):
                S.perc("hat", bar, sl, 0.012, 0.2)
            continue
        for sl in kick:
            S.perc("kick", bar, sl, gk)
        S.perc("snare", bar, snare[0], gs)
        if bar in rolls:
            for i in range(8):
                S.perc("snare", bar, 8 + i, 0.05 + 0.11 * i / 7)
        elif len(snare) > 1:
            S.perc("snare", bar, snare[1], gs)
        if hat8:
            for sl in range(0, 16, 2):
                S.perc("hat", bar, sl, gh, 0.2)


def drums_maqsum(S, bars, light=()):
    for bar in range(bars):
        if bar in light:
            for sl in (0, 6):
                S.perc("rim", bar, sl, 0.10, 0.2)
            continue
        S.perc("kick", bar, 0, 0.30)
        S.perc("rim", bar, 4, 0.14, 0.2)
        S.perc("rim", bar, 6, 0.12, 0.2)
        S.perc("kick", bar, 8, 0.26)
        S.perc("rim", bar, 12, 0.14, 0.2)
        S.perc("rim", bar, 14, 0.12, 0.2)
        for sl in range(2, 16, 4):
            S.perc("hat", bar, sl, 0.014, -0.2)


def drums_taiko(S, bars):
    for bar in range(bars):
        S.perc("taiko", bar, 0, 0.34)
        if bar % 2 == 0:
            S.perc("taiko", bar, 10, 0.20)
        else:
            S.perc("rim", bar, 6, 0.10, 0.15)
            S.perc("taiko", bar, 12, 0.16)


def drums_tresillo(S, bars, gk=0.28, gs=0.14):
    for bar in range(bars):
        for sl in (0, 3, 6, 8, 11, 14):
            S.perc("kick", bar, sl, gk if sl in (0, 8) else gk * 0.8)
        S.perc("snare", bar, 12, gs)
        for sl in range(0, 16, 2):
            S.perc("hat", bar, sl, 0.016, 0.2)


def drums_soft(S, bars, gk=0.20):
    for bar in range(bars):
        S.perc("kick", bar, 0, gk)
        S.perc("rim", bar, 8, 0.09, 0.2)
        if bar % 4 == 3:
            for i in range(4):
                S.perc("rim", bar, 12 + i, 0.06 + 0.05 * i, 0.2)


# ---------------- MIDI 写出 ----------------
def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n > 0:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _chunk(data):
    return b"MTrk" + len(data).to_bytes(4, "big") + data


def write_midi(path, bpm, ev):
    ppq = 480
    mpq = int(60_000_000 / bpm)
    t = bytearray()
    t += vlq(0) + b"\xFF\x58\x04\x04\x02\x18\x08"
    t += vlq(0) + b"\xFF\x51\x03" + mpq.to_bytes(3, "big")
    t += vlq(0) + b"\xFF\x2F\x00"
    chunks = [_chunk(bytes(t))]
    for ch in (0, 1, 2, 9):
        body = bytearray()
        if ch in PROG:
            body += vlq(0) + bytes([0xC0 | ch, PROG[ch]])
        items = []
        for st, ds, pt, v in ev.get(ch, []):
            t0 = int(round(st * ppq / 4))
            t1 = max(int(round((st + max(ds, 1)) * ppq / 4)), t0 + 1)
            items.append((t0, 1, 0x90 | ch, pt & 0x7F, v))
            items.append((t1, 0, 0x80 | ch, pt & 0x7F, 0))
        items.sort(key=lambda x: (x[0], x[1]))
        last = 0
        for tick, _, status, p, v in items:
            body += vlq(tick - last) + bytes([status, p, v])
            last = tick
        body += vlq(0) + b"\xFF\x2F\x00"
        chunks.append(_chunk(bytes(body)))
    head = (b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
            + len(chunks).to_bytes(2, "big") + ppq.to_bytes(2, "big"))
    Path(path).write_bytes(head + b"".join(chunks))


# ================= A 套房: 战斗与进阶 =================
def build_a1(S):
    """Steel Verdict — 140BPM A 小调 32 小节正面战斗, 半终止停 V 级回环"""
    progA = ["A2m", "A2m", "F2", "F2", "G2", "G2", "A2m", "E2"]
    progB = ["C2", "C2", "G2", "G2", "A2m", "A2m", "F2", "E2"]
    prog = progA * 2 + progB + progA
    MEL_A = [
        [(0, "A4", 2), (2, "C5", 2), (4, "E5", 6)],
        [(0, "F5", 4), (4, "E5", 2), (6, "D5", 2), (8, "C5", 4), (12, "D5", 2), (14, "E5", 2)],
        [(0, "C5", 4), (4, "A4", 4), (8, "C5", 4), (12, "D5", 2), (14, "E5", 2)],
        [(0, "D5", 6), (6, "B4", 2), (8, "G4", 8)],
        [(0, "A4", 2), (2, "B4", 2), (4, "C5", 6), (10, "E5", 2), (12, "D5", 2), (14, "C5", 2)],
        [(0, "C5", 4), (4, "A4", 4), (8, "A4", 8)],
        [(0, "B4", 4), (4, "D5", 4), (8, "G5", 6), (14, "F5", 2)],
        [(0, "E5", 6), (6, "D5", 2), (8, "B4", 4), (12, "G#4", 2), (14, "B4", 2)],
    ]
    MEL_B = [
        [(0, "E5", 4), (4, "G5", 4), (8, "A5", 6), (14, "G5", 2)],
        [(0, "G5", 4), (4, "E5", 4), (8, "D5", 8)],
        [(0, "E5", 4), (4, "D5", 2), (6, "C5", 2), (8, "G4", 8)],
        [(0, "B4", 4), (4, "D5", 4), (8, "G5", 4), (12, "F5", 4)],
        [(0, "E5", 4), (4, "C5", 4), (8, "A4", 6), (14, "B4", 2)],
        [(0, "C5", 4), (4, "E5", 4), (8, "A5", 8)],
        [(0, "A5", 4), (4, "G5", 4), (8, "F5", 4), (12, "E5", 4)],
        [(0, "D5", 4), (4, "B4", 4), (8, "G#4", 8)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A[:7], 0.115, 0.12, 0.25)
    put(S, "lead", 15, [(0, "E5", 12)], 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    emit(S, "lead", 24, MEL_A[:6], 0.115, 0.12, 0.25)
    put(S, "lead", 30, [(0, "A4", 4), (4, "C5", 4), (8, "E5", 4), (12, "D5", 4)], 0.115, 0.12, 0.25)
    put(S, "lead", 31, [(0, "B4", 6), (6, "G#4", 2), (8, "E4", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30)
    drums_std(S, 32, rolls={7, 15, 23, 31})
    pads(S, prog, bars=set(range(16, 24)), gain=0.028)
    arp_tail(S, prog, [7, 15, 23, 31])


def build_a2(S):
    """Bright Advance — 126BPM C 大调 32 小节地图进军, 呼应句 + 呼吸小节"""
    progA = ["C2", "A2m", "F2", "G2", "C2", "A2m", "D2m", "G2"]
    progB = ["F2", "G2", "C2", "A2m", "F2", "D2m", "G2", "G2"]
    prog = progA * 2 + progB + progA
    MEL_A = [
        [(0, "E4", 2), (2, "G4", 2), (4, "C5", 6), (10, "G4", 2), (12, "E4", 4)],
        [(0, "C5", 3), (3, "B4", 1), (4, "A4", 4), (8, "E4", 4), (12, "G4", 4)],
        [(0, "A4", 2), (2, "C5", 2), (4, "F5", 4), (8, "E5", 2), (10, "D5", 2), (12, "C5", 4)],
        [(0, "B4", 4), (4, "D5", 4), (8, "G4", 8)],
        [(0, "G4", 2), (2, "A4", 2), (4, "B4", 2), (6, "C5", 6), (12, "B4", 2), (14, "A4", 2)],
        [(0, "E5", 4), (4, "C5", 4), (8, "A4", 8)],
        [(0, "F5", 4), (4, "E5", 4), (8, "D5", 4), (12, "C5", 2), (14, "B4", 2)],
        [(0, "D5", 6), (6, "B4", 2), (8, "D5", 8)],
    ]
    MEL_B = [
        [(0, "E4", 4), (4, "G4", 4), (8, "C5", 4)],
        [(0, "D5", 4), (4, "B4", 4), (8, "G4", 4), (12, "B4", 4)],
        [(0, "E5", 6), (6, "C5", 2), (8, "G4", 8)],
        [(0, "A4", 4), (4, "C5", 4), (8, "E5", 4), (12, "D5", 2), (14, "C5", 2)],
        [(0, "C5", 2), (2, "D5", 2), (4, "E5", 2), (6, "F5", 6), (12, "E5", 2), (14, "D5", 2)],
        [(0, "A5", 4), (4, "F5", 4), (8, "D5", 8)],
        [(0, "D5", 2), (2, "E5", 2), (4, "F5", 2), (6, "G5", 4), (10, "F5", 2), (12, "E5", 2), (14, "D5", 2)],
        [(0, "B4", 8), (8, "D5", 6), (14, "F5", 2)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A[:7], 0.115, 0.12, 0.25)
    put(S, "lead", 15, [(0, "C5", 12)], 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    emit(S, "lead", 24, MEL_A[:6], 0.115, 0.12, 0.25)
    put(S, "lead", 30, [(0, "D5", 4), (4, "F5", 4), (8, "E5", 4), (12, "D5", 4)], 0.115, 0.12, 0.25)
    put(S, "lead", 31, [(0, "B4", 4), (4, "D5", 4), (8, "G4", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30, duck={16: 0.75, 17: 0.75})
    drums_std(S, 32, rolls={7, 15, 23, 31}, light={16, 17})
    pads(S, prog, bars=set(range(16, 24)), gain=0.028)
    arp_tail(S, prog, [7, 15, 23, 31])


def build_a3(S):
    """Skirmish Rush — 164BPM E 小调 24 小节快节奏遭遇战"""
    prog8 = ["E2m", "E2m", "C2", "C2", "G2", "G2", "D2", "D2"]
    prog = prog8 * 3
    MEL = [
        [(0, "E5", 2), (2, "E5", 2), (4, "G5", 2), (6, "E5", 2), (8, "B4", 2), (10, "E5", 2), (12, "G5", 4)],
        [(0, "A5", 2), (2, "G5", 2), (4, "F#5", 2), (6, "E5", 2), (8, "D5", 4), (12, "B4", 4)],
        [(0, "C5", 2), (2, "E5", 2), (4, "G5", 2), (6, "E5", 2), (8, "C6", 4), (12, "B5", 2), (14, "A5", 2)],
        [(0, "G5", 4), (4, "E5", 4), (8, "C5", 6)],
        [(0, "D5", 2), (2, "D5", 2), (4, "B4", 2), (6, "D5", 2), (8, "G5", 4), (12, "F#5", 4)],
        [(0, "G5", 4), (4, "D5", 4), (8, "B4", 8)],
        [(0, "F#5", 4), (4, "A5", 4), (8, "D5", 4), (12, "E5", 2), (14, "F#5", 2)],
        [(0, "A5", 6), (6, "F#5", 2), (8, "D5", 8)],
    ]
    for off in (0, 8, 16):
        emit(S, "lead", off, MEL, 0.115, 0.12, 0.25)
    put(S, "lead", 22, [(0, "E5", 4), (4, "F#5", 4), (8, "G5", 4), (12, "A5", 4)], 0.115, 0.12, 0.25)
    put(S, "lead", 23, [(0, "B5", 6), (6, "A5", 2), (8, "F#5", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30)
    drums_std(S, 24, rolls={7, 15, 23}, gk=0.28, gs=0.15, gh=0.016)
    arp_tail(S, prog, [7, 15, 23])


def build_a4(S):
    """Shadow Draws Near — 116BPM D 小调 32 小节紧张压迫, 断奏 + 呼吸"""
    progA = ["D2m", "D2m", "Bb2", "Bb2", "G2m", "G2m", "A2", "A2"]
    progB = ["D2m", "F2", "G2m", "A2", "D2m", "Bb2", "G2m", "A2"]
    prog = progA * 2 + progB + progA
    MEL_A = [
        [(0, "D5", 2), (4, "D5", 2), (8, "F5", 2), (12, "E5", 2)],
        [(0, "D5", 2), (4, "C5", 2), (8, "D5", 4)],
        [(0, "F5", 2), (4, "F5", 2), (8, "G5", 2), (12, "F5", 2)],
        [(0, "D5", 2), (4, "Bb4", 2), (8, "D5", 4)],
        [(0, "G5", 2), (4, "F5", 2), (8, "D5", 2), (12, "Bb4", 2)],
        [(0, "G4", 2), (4, "Bb4", 2), (8, "D5", 4)],
        [(0, "E5", 2), (4, "C#5", 2), (8, "E5", 2), (12, "A5", 2)],
        [(0, "A4", 2), (4, "E5", 2), (8, "C#5", 6)],
    ]
    MEL_B = [
        [(0, "D5", 2), (4, "F5", 2), (8, "A5", 2), (12, "G5", 2)],
        [(0, "F5", 2), (4, "A5", 2), (8, "C6", 4)],
        [(0, "Bb5", 2), (4, "G5", 2), (8, "D5", 4)],
        [(0, "C#5", 2), (4, "E5", 2), (8, "A5", 4)],
        [(0, "A5", 2), (4, "F5", 2), (8, "D5", 4)],
        [(0, "F5", 2), (4, "D5", 2), (8, "Bb4", 4)],
        [(0, "G5", 2), (4, "Bb5", 2), (8, "G5", 2), (12, "D5", 2)],
        [(0, "E5", 2), (4, "C#5", 2), (8, "A4", 6)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A[:7], 0.115, 0.12, 0.25)
    put(S, "lead", 15, [(0, "E5", 2), (4, "C#5", 2), (8, "A4", 6)], 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    emit(S, "lead", 24, MEL_A[:6], 0.115, 0.12, 0.25)
    put(S, "lead", 30, [(0, "F5", 2), (4, "E5", 2), (8, "D5", 2), (12, "C#5", 2)], 0.115, 0.12, 0.25)
    put(S, "lead", 31, [(0, "C#5", 4), (4, "E5", 4), (8, "A4", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30, style="stacc", duck={16: 0.75, 17: 0.75})
    drums_std(S, 32, rolls={7, 15, 23, 31}, light={16, 17}, gs=0.15, gh=0.018)
    pads(S, prog, bars=set(range(0, 8)), gain=0.026)
    arp_tail(S, prog, [15, 31], gain=0.020)


def build_a5(S):
    """Ember Campfire — 96BPM F 大调 24 小节营地休整, 柔和圆 duty"""
    prog8 = ["F2", "F2", "D2m", "D2m", "Bb2", "Bb2", "C2", "C2"]
    prog = prog8 * 3
    MEL = [
        [(0, "A4", 4), (4, "C5", 4), (8, "F5", 8)],
        [(0, "E5", 6), (6, "D5", 2), (8, "C5", 8)],
        [(0, "D5", 4), (4, "F5", 4), (8, "A5", 8)],
        [(0, "G5", 6), (6, "F5", 2), (8, "E5", 8)],
        [(0, "F5", 4), (4, "D5", 4), (8, "Bb4", 8)],
        [(0, "C5", 4), (4, "D5", 4), (8, "E5", 8)],
        [(0, "G5", 4), (4, "E5", 4), (8, "C5", 4), (12, "D5", 2), (14, "E5", 2)],
        [(0, "D5", 6), (6, "B4", 2), (8, "C5", 8)],
    ]
    emit(S, "lead", 0, MEL, 0.105, 0.10, 0.5)
    emit(S, "lead", 8, MEL[:7], 0.105, 0.10, 0.5)
    put(S, "lead", 15, [(0, "A4", 12)], 0.105, 0.10, 0.5)
    emit(S, "lead", 16, MEL[:6], 0.105, 0.10, 0.5)
    put(S, "lead", 22, [(0, "F5", 2), (2, "G5", 2), (4, "A5", 2), (6, "Bb5", 2), (8, "A5", 4), (12, "G5", 4)], 0.105, 0.10, 0.5)
    put(S, "lead", 23, [(0, "E5", 6), (6, "G5", 2), (8, "C5", 8)], 0.105, 0.10, 0.5)
    engine_bass(S, prog, 0.28, style="half")
    drums_soft(S, 24)
    arp_tail(S, prog, [7, 15, 23], gain=0.018)


# ================= B 套房: 骑士团旗 (FE 风格) =================
def build_b1(S):
    """Knights Advance — 132BPM Bb 大调 32 小节玩家回合进军(FE 我方回合锚定)"""
    progA = ["Bb2", "Bb2", "Eb2", "Bb2", "F2", "Eb2", "Bb2", "F2"]
    progB = ["Eb2", "Bb2", "G2m", "G2m", "Eb2", "F2", "F2", "F2"]
    prog = progA * 2 + progB + progA
    MEL_A = [
        [(0, "F4", 2), (2, "Bb4", 2), (4, "D5", 6), (10, "C5", 2), (12, "Bb4", 4)],
        [(0, "Bb4", 2), (2, "D5", 2), (4, "F5", 6), (10, "Eb5", 2), (12, "D5", 4)],
        [(0, "Eb5", 4), (4, "D5", 2), (6, "C5", 2), (8, "Bb4", 4), (12, "D5", 4)],
        [(0, "F5", 6), (6, "Eb5", 2), (8, "D5", 8)],
        [(0, "C5", 2), (2, "D5", 2), (4, "Eb5", 2), (6, "C5", 2), (8, "A4", 4), (12, "C5", 4)],
        [(0, "G5", 4), (4, "F5", 2), (6, "Eb5", 2), (8, "Bb4", 8)],
        [(0, "D5", 2), (2, "F5", 2), (4, "Bb5", 6), (10, "A5", 2), (12, "F5", 4)],
        [(0, "Eb5", 4), (4, "C5", 4), (8, "A4", 8)],
    ]
    MEL_B = [
        [(0, "F4", 4), (4, "Bb4", 4), (8, "F4", 4), (12, "D4", 4)],
        [(0, "Bb5", 4), (4, "F5", 4), (8, "D5", 4), (12, "Bb4", 4)],
        [(0, "D5", 4), (4, "G5", 4), (8, "Bb5", 6), (14, "A5", 2)],
        [(0, "G5", 4), (4, "D5", 4), (8, "Bb4", 8)],
        [(0, "Eb5", 4), (4, "G5", 4), (8, "Bb5", 8)],
        [(0, "A5", 4), (4, "F5", 4), (8, "C6", 8)],
        [(0, "C6", 4), (4, "Bb5", 2), (6, "A5", 2), (8, "F5", 4), (12, "Eb5", 2), (14, "C5", 2)],
        [(0, "A5", 4), (4, "F5", 4), (8, "C5", 8)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A[:7], 0.115, 0.12, 0.25)
    put(S, "lead", 15, [(0, "Bb4", 12)], 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    emit(S, "lead", 24, MEL_A[:6], 0.115, 0.12, 0.25)
    put(S, "lead", 30, [(0, "Eb5", 2), (2, "D5", 2), (4, "C5", 2), (6, "Bb4", 2), (8, "A4", 4), (12, "C5", 4)], 0.115, 0.12, 0.25)
    put(S, "lead", 31, [(0, "Eb5", 6), (6, "C5", 2), (8, "A4", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30, duck={16: 0.75, 17: 0.75})
    drums_std(S, 32, rolls={7, 15, 23, 31}, light={16, 17})
    pads(S, prog, bars=set(range(16, 24)), gain=0.028)
    arp_tail(S, prog, [15, 23])


def build_b2(S):
    """Iron Tide — 115BPM C 小调 32 小节敌方回合/中Boss(FE 锚定, B5 导音呼应)"""
    progA = ["C2m", "C2m", "Ab2", "Ab2", "Bb2", "Bb2", "G2", "G2"]
    progB = ["C2m", "F2m", "Ab2", "G2", "C2m", "F2m", "Bb2", "G2"]
    prog = progA * 2 + progB + progA
    MEL_A = [
        [(0, "G4", 2), (2, "Ab4", 2), (4, "C5", 6), (10, "B4", 2), (12, "C5", 4)],
        [(0, "Eb5", 4), (4, "C5", 4), (8, "G4", 8)],
        [(0, "Ab4", 2), (2, "C5", 2), (4, "Eb5", 4), (8, "C5", 4), (12, "Ab4", 4)],
        [(0, "G4", 4), (4, "Ab4", 4), (8, "B4", 2), (10, "C5", 6)],
        [(0, "D5", 4), (4, "F5", 4), (8, "D5", 4), (12, "Bb4", 4)],
        [(0, "G5", 6), (6, "F5", 2), (8, "D5", 8)],
        [(0, "F5", 2), (2, "Eb5", 2), (4, "D5", 4), (8, "B4", 4), (12, "D5", 4)],
        [(0, "F5", 4), (4, "D5", 4), (8, "B4", 8)],
    ]
    MEL_B = [
        [(0, "C4", 4), (4, "Eb4", 4), (8, "G4", 6)],
        [(0, "B5", 2), (2, "C6", 6), (8, "G5", 8)],
        [(0, "Ab5", 4), (4, "F5", 4), (8, "C5", 8)],
        [(0, "G5", 4), (4, "F5", 2), (6, "D5", 2), (8, "B4", 8)],
        [(0, "Eb5", 2), (2, "D5", 2), (4, "Eb5", 2), (6, "F5", 2), (8, "Eb5", 4), (12, "C5", 4)],
        [(0, "Ab4", 4), (4, "C5", 4), (8, "F5", 8)],
        [(0, "Bb5", 4), (4, "F5", 4), (8, "D5", 4), (12, "F5", 4)],
        [(0, "D5", 4), (4, "B4", 4), (8, "G4", 8)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A[:7], 0.115, 0.12, 0.25)
    put(S, "lead", 15, [(0, "D5", 12)], 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    emit(S, "lead", 24, MEL_A[:6], 0.115, 0.12, 0.25)
    put(S, "lead", 30, [(0, "C5", 4), (4, "Eb5", 4), (8, "D5", 4), (12, "B4", 4)], 0.115, 0.12, 0.25)
    put(S, "lead", 31, [(0, "D5", 6), (6, "B4", 2), (8, "G4", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30, duck={9: 0.75, 13: 0.75})
    drums_std(S, 32, rolls={7, 15, 23, 31}, light={9, 13})
    pads(S, prog, bars=set(range(8, 16)), gain=0.026)
    arp_tail(S, prog, [15, 23])


def build_b3(S):
    """Throne of the Dark Lord — 100BPM C 小调 24 小节最终 Boss(FE 梅迪乌斯锚定)"""
    prog8 = ["C2m", "C2m", "Ab2", "Ab2", "F2m", "F2m", "G2", "G2"]
    prog = prog8 * 3
    MEL_A = [
        [(0, "C5", 4), (4, "G4", 4), (8, "Ab4", 4), (12, "G4", 4)],
        [(0, "Eb5", 6), (6, "D5", 2), (8, "C5", 8)],
        [(0, "Ab4", 4), (4, "C5", 4), (8, "Eb5", 8)],
        [(0, "D5", 4), (4, "B4", 4), (8, "G4", 8)],
        [(0, "F5", 4), (4, "Eb5", 4), (8, "C5", 4), (12, "Ab4", 4)],
        [(0, "Bb4", 4), (4, "C5", 4), (8, "Db5", 8)],
        [(0, "D5", 4), (4, "Eb5", 2), (6, "D5", 2), (8, "B4", 8)],
        [(0, "D5", 12)],
    ]
    MEL_B = [
        [(0, "G5", 4), (4, "F5", 4), (8, "Eb5", 4), (12, "D5", 4)],
        [(0, "Eb5", 4), (4, "D5", 4), (8, "C5", 8)],
        [(0, "Ab4", 4), (4, "C5", 4), (8, "Eb5", 4), (12, "Ab5", 4)],
        [(0, "G5", 8), (8, "Eb5", 8)],
        [(0, "F5", 4), (4, "G5", 4), (8, "Ab5", 8)],
        [(0, "C6", 6), (6, "Ab5", 2), (8, "F5", 8)],
        [(0, "F5", 2), (2, "Eb5", 2), (4, "D5", 4), (8, "B4", 8)],
        [(0, "D5", 8), (8, "B4", 8)],
    ]
    for i, bd in enumerate(MEL_A):
        bar = i
        for sl, nm, ds in bd:
            S.note("lead", bar, sl, nm, ds, 0.11, 0.0, 0.5)
            S.note("bass", bar, sl, shift(nm, -12), ds, 0.09, -0.05, wave="tri")
    for i, bd in enumerate(MEL_A[:7]):
        bar = 8 + i
        for sl, nm, ds in bd:
            S.note("lead", bar, sl, nm, ds, 0.11, 0.0, 0.5)
            S.note("bass", bar, sl, shift(nm, -12), ds, 0.09, -0.05, wave="tri")
    put(S, "lead", 15, [(0, "Eb5", 12)], 0.11, 0.0, 0.5)
    S.note("bass", 15, 0, "Eb3", 12, 0.09, -0.05, wave="tri")
    for i, bd in enumerate(MEL_B):
        bar = 16 + i
        for sl, nm, ds in bd:
            S.note("lead", bar, sl, nm, ds, 0.11, 0.0, 0.5)
            S.note("bass", bar, sl, shift(nm, -12), ds, 0.09, -0.05, wave="tri")
    engine_bass(S, prog, 0.32, duck={7: 0.73, 15: 0.73})
    drums_std(S, 24, rolls={14}, light={7, 15}, gk=0.32, gs=0.17, gh=0.018)
    arp_tail(S, prog, [6, 14, 22], gain=0.020)


def build_b4(S):
    """Victory Fanfare — 123BPM G 大调 one-shot 胜利号角"""
    chords = [("G", 2), ("C", 3), ("D", 3), ("G", 2)]
    MEL = [
        [(0, "G4", 2), (2, "G4", 2), (4, "G4", 2), (6, "B4", 2), (8, "D5", 8)],
        [(0, "E5", 4), (4, "G5", 4), (8, "C6", 8)],
        [(0, "B5", 4), (4, "A5", 4), (8, "F#5", 8)],
        [(0, "D5", 2), (2, "B4", 2), (4, "G5", 12)],
    ]
    for i, bd in enumerate(MEL):
        put(S, "lead", i, bd, 0.12, 0.10, 0.25)
    for bar, (rt, ov) in enumerate(chords):
        r = f"{rt}{ov}"
        S.note("bass", bar, 0, r, 7.5, 0.30, -0.08, wave="tri")
        S.note("bass", bar, 8, shift(r, 7), 7.5, 0.30, -0.08, wave="tri")
        S.note("harm", bar, 0, shift(f"{rt}{ov + 2}", 4), 16, 0.028, -0.18, 0.5)
    for bar in range(4):
        S.perc("kick", bar, 0, 0.32)
        S.perc("snare", bar, 4, 0.15)
    for i in range(8):
        S.perc("snare", 2, 8 + i, 0.05 + 0.11 * i / 7)
    S.perc("hat", 3, 8, 0.05, 0.2)
    S.perc("hat", 3, 10, 0.03, 0.2)
    S.perc("hat", 3, 12, 0.02, 0.2)


def build_b5(S):
    """Level Up! — C 大调 one-shot 升级铃音 ~2.6s"""
    put(S, "lead", 0, [(0, "C5", 2), (2, "E5", 2), (4, "G5", 2), (6, "C6", 10)], 0.12, 0.08, 0.25)
    S.note("harm", 0, 6, "G5", 10, 0.045, -0.15, 0.5)
    S.note("bass", 0, 0, "C3", 12, 0.28, -0.08, wave="tri")
    S.note("bass", 0, 12, "G3", 4, 0.24, -0.08, wave="tri")
    for k, nm in enumerate(("E6", "G6")):
        S.note("lead", 0, 10 + k * 2, nm, 2, 0.06, 0.2, 0.25)


# ================= C 套房: 异域之旅 =================
def build_c1(S):
    """Desert Caravan — 100BPM D Hijaz 24 小节沙漠商队, maqsum 节奏"""
    prog8 = ["D2", "D2", "Bb2", "Bb2", "A2", "A2", "D2", "D2"]
    prog = prog8 * 3
    MEL_A = [
        [(0, "D4", 2), (2, "Eb4", 2), (4, "F#4", 4), (8, "G4", 4), (12, "A4", 4)],
        [(0, "Bb4", 4), (4, "A4", 2), (6, "G4", 2), (8, "F#4", 8)],
        [(0, "D5", 4), (4, "C5", 2), (6, "Bb4", 2), (8, "A4", 4), (12, "G4", 4)],
        [(0, "F#4", 4), (4, "Eb4", 4), (8, "D4", 8)],
        [(0, "E4", 2), (2, "F#4", 2), (4, "A4", 6), (10, "Bb4", 2), (12, "A4", 4)],
        [(0, "A4", 2), (2, "Bb4", 2), (4, "C5", 2), (6, "Bb4", 2), (8, "A4", 4), (12, "F#4", 4)],
        [(0, "G4", 2), (2, "F#4", 2), (4, "Eb4", 2), (6, "F#4", 2), (8, "D4", 8)],
        [(0, "D4", 2), (2, "F#4", 2), (4, "A4", 4), (8, "D5", 8)],
    ]
    MEL_B = [
        [(0, "A4", 2), (2, "Bb4", 2), (4, "D5", 4), (8, "Eb5", 4), (12, "D5", 4)],
        [(0, "C5", 2), (2, "Bb4", 2), (4, "A4", 4), (8, "F#4", 8)],
        [(0, "Bb4", 2), (2, "C5", 2), (4, "D5", 4), (8, "Eb5", 2), (10, "F#5", 2), (12, "Eb5", 4)],
        [(0, "D5", 8), (8, "A4", 8)],
        [(0, "F#4", 2), (2, "G4", 2), (4, "A4", 4), (8, "Bb4", 4), (12, "A4", 4)],
        [(0, "G4", 2), (2, "A4", 2), (4, "Bb4", 2), (6, "C5", 2), (8, "D5", 8)],
        [(0, "Eb5", 4), (4, "D5", 4), (8, "C5", 4), (12, "Bb4", 4)],
        [(0, "A4", 2), (2, "F#4", 2), (4, "Eb4", 2), (6, "F#4", 2), (8, "D4", 8)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A[:7], 0.115, 0.12, 0.25)
    put(S, "lead", 15, [(0, "D4", 2), (2, "F#4", 2), (4, "A4", 4), (8, "D5", 8)], 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    drone(S, 24, ["D4", "A4"], 0.032)
    engine_bass(S, prog, 0.30)
    drums_maqsum(S, 24, light={15})
    arp_tail(S, prog, [7, 23])


def build_c2(S):
    """Shrine of Petals — 84BPM E Hirajoshi 24 小节东国神社, 太鼓"""
    prog8 = ["E2", "E2", "A2", "A2", "F2", "F2", "B2", "B2"]
    prog = prog8 * 3
    MEL_A = [
        [(0, "B4", 6), (6, "A4", 2), (8, "E4", 8)],
        [(0, "F5", 4), (4, "E5", 4), (8, "B4", 8)],
        [(0, "A4", 4), (4, "C5", 4), (8, "B4", 8)],
        [(0, "A4", 2), (2, "F4", 2), (4, "E4", 12)],
        [(0, "B4", 4), (4, "E5", 4), (8, "F5", 8)],
        [(0, "E5", 6), (6, "C5", 2), (8, "A4", 8)],
        [(0, "B4", 4), (4, "C5", 4), (8, "F4", 8)],
        [(0, "E4", 16)],
    ]
    MEL_B = [
        [(0, "E5", 4), (6, "F5", 1), (7, "E5", 5), (12, "B4", 4)],
        [(0, "C5", 4), (4, "A4", 4), (8, "B4", 8)],
        [(0, "A4", 4), (4, "C5", 4), (8, "E5", 4), (12, "F5", 4)],
        [(0, "E5", 6), (6, "C5", 2), (8, "A4", 8)],
        [(0, "B4", 4), (4, "E5", 4), (8, "F5", 4), (12, "E5", 4)],
        [(0, "C5", 4), (4, "B4", 4), (8, "A4", 8)],
        [(0, "F4", 4), (4, "E4", 4), (8, "B4", 4), (12, "C5", 4)],
        [(0, "B4", 2), (2, "A4", 2), (4, "E4", 12)],
    ]
    emit(S, "lead", 0, MEL_A, 0.105, 0.10, 0.5)
    emit(S, "lead", 8, MEL_A, 0.105, 0.10, 0.5)
    emit(S, "lead", 16, MEL_B, 0.105, 0.10, 0.5)
    drone(S, 24, ["E3", "B3"], 0.045)
    engine_bass(S, prog, 0.26, style="half")
    drums_taiko(S, 24)


def build_c3(S):
    """Ruins of the Forgotten — 72BPM C 双和声 20 小节遗迹, 神秘稀疏"""
    prog = ["C2", "C2", "Ab2", "Ab2", "F2m", "F2m", "G2", "G2"] + ["C2", "Ab2", "G2", "C2"]
    MEL_A = [
        [(0, "C5", 4), (4, "Db5", 4), (8, "E5", 8)],
        [(0, "F5", 6), (6, "E5", 2), (8, "Db5", 8)],
        [(0, "B4", 4), (4, "C5", 4), (8, "G4", 8)],
        [(0, "Ab4", 4), (4, "B4", 4), (8, "C5", 8)],
        [(0, "E5", 4), (4, "F5", 4), (8, "G5", 4), (12, "Ab5", 4)],
        [(0, "G5", 6), (6, "F5", 2), (8, "E5", 8)],
        [(0, "Db5", 4), (4, "B4", 4), (8, "G4", 8)],
        [(0, "C5", 16)],
    ]
    emit(S, "lead", 0, MEL_A, 0.105, 0.10, 0.5)
    emit(S, "lead", 8, MEL_A[:7], 0.105, 0.10, 0.5)
    put(S, "lead", 15, [(0, "E5", 12)], 0.105, 0.10, 0.5)
    put(S, "lead", 16, [(0, "Ab4", 4), (4, "G4", 4), (8, "F4", 4), (12, "E4", 4)], 0.105, 0.10, 0.5)
    put(S, "lead", 17, [(0, "Db5", 4), (4, "C5", 4), (8, "B4", 4), (12, "Ab4", 4)], 0.105, 0.10, 0.5)
    put(S, "lead", 18, [(0, "G4", 6), (6, "B4", 2), (8, "Db5", 8)], 0.105, 0.10, 0.5)
    put(S, "lead", 19, [(0, "C5", 16)], 0.105, 0.10, 0.5)
    drone(S, 20, ["C3", "G3"], 0.040)
    engine_bass(S, prog, 0.28, style="half")
    drums_soft(S, 20, gk=0.18)


def build_c4(S):
    """Oasis Festival — 128BPM G Hijaz 32 小节绿洲祭典, tresillo 3+3+2"""
    progA = ["G2", "G2", "C2", "C2", "D2", "D2", "Eb2", "D2"]
    progB = ["C2", "C2", "G2", "G2", "Eb2", "Eb2", "D2", "D2"]
    prog = progA * 2 + progB + progA
    MEL_A = [
        [(0, "D5", 3), (3, "D5", 1), (4, "Eb5", 2), (6, "D5", 2), (8, "C5", 2), (10, "B4", 2), (12, "D5", 4)],
        [(0, "G5", 2), (2, "F#5", 2), (4, "G5", 4), (8, "D5", 8)],
        [(0, "C5", 2), (2, "Eb5", 2), (4, "G5", 2), (6, "Eb5", 2), (8, "C5", 4), (12, "D5", 4)],
        [(0, "B4", 4), (4, "D5", 4), (8, "G4", 8)],
        [(0, "D5", 3), (3, "Eb5", 1), (4, "F#5", 2), (6, "G5", 2), (8, "F#5", 4), (12, "Eb5", 4)],
        [(0, "D5", 2), (2, "C5", 2), (4, "B4", 2), (6, "C5", 2), (8, "D5", 8)],
        [(0, "Eb5", 4), (4, "D5", 4), (8, "C5", 4), (12, "B4", 4)],
        [(0, "B4", 2), (2, "C5", 2), (4, "D5", 10)],
    ]
    MEL_B = [
        [(0, "G5", 3), (3, "G5", 1), (4, "Ab5", 2), (6, "G5", 2), (8, "Eb5", 2), (10, "C5", 2), (12, "D5", 4)],
        [(0, "Eb5", 2), (2, "D5", 2), (4, "C5", 4), (8, "G4", 8)],
        [(0, "D5", 2), (2, "Eb5", 2), (4, "D5", 2), (6, "B4", 2), (8, "G4", 4), (12, "D5", 4)],
        [(0, "G5", 4), (4, "F#5", 4), (8, "D5", 8)],
        [(0, "Eb5", 3), (3, "Eb5", 1), (4, "F#5", 2), (6, "G5", 2), (8, "Ab5", 4), (12, "G5", 4)],
        [(0, "F#5", 2), (2, "Eb5", 2), (4, "D5", 4), (8, "B4", 8)],
        [(0, "A5", 4), (4, "F#5", 4), (8, "D5", 4), (12, "F#5", 4)],
        [(0, "A5", 6), (6, "F#5", 2), (8, "D5", 8)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    emit(S, "lead", 24, MEL_A[:6], 0.115, 0.12, 0.25)
    put(S, "lead", 30, [(0, "Eb5", 4), (4, "F#5", 4), (8, "G5", 4), (12, "A5", 4)], 0.115, 0.12, 0.25)
    put(S, "lead", 31, [(0, "A5", 4), (4, "F#5", 4), (8, "D5", 8)], 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.30)
    drums_tresillo(S, 32)
    pads(S, prog, bars=set(range(16, 24)), gain=0.026)
    arp_tail(S, prog, [15, 31])


def build_c5(S):
    """Harbor Bazaar — 108BPM A 小调五声 24 小节港市集市, 轻快"""
    prog8 = ["A2m", "A2m", "F2", "F2", "C2", "C2", "G2", "G2"]
    prog = prog8 * 3
    MEL_A = [
        [(0, "E5", 2), (2, "C5", 2), (4, "D5", 2), (6, "E5", 2), (8, "G5", 4), (12, "E5", 4)],
        [(0, "A5", 4), (4, "G5", 2), (6, "E5", 2), (8, "D5", 4), (12, "C5", 4)],
        [(0, "C5", 2), (2, "D5", 2), (4, "E5", 2), (6, "G5", 2), (8, "E5", 4), (12, "D5", 4)],
        [(0, "C5", 4), (4, "A4", 4), (8, "A4", 8)],
        [(0, "G4", 2), (2, "A4", 2), (4, "C5", 2), (6, "D5", 2), (8, "E5", 4), (12, "D5", 4)],
        [(0, "C5", 4), (4, "D5", 2), (6, "E5", 2), (8, "G5", 8)],
        [(0, "E5", 2), (2, "D5", 2), (4, "C5", 2), (6, "D5", 2), (8, "E5", 4), (12, "G5", 4)],
        [(0, "A5", 6), (6, "G5", 2), (8, "E5", 8)],
    ]
    MEL_B = [
        [(0, "A5", 4), (4, "C6", 4), (8, "A5", 4), (12, "G5", 4)],
        [(0, "E5", 4), (4, "G5", 4), (8, "A5", 8)],
        [(0, "G5", 2), (2, "E5", 2), (4, "D5", 2), (6, "C5", 2), (8, "A4", 4), (12, "C5", 4)],
        [(0, "D5", 4), (4, "E5", 4), (8, "G5", 8)],
        [(0, "E5", 2), (2, "G5", 2), (4, "A5", 2), (6, "G5", 2), (8, "E5", 4), (12, "D5", 4)],
        [(0, "C5", 4), (4, "E5", 4), (8, "A5", 8)],
        [(0, "G5", 4), (4, "E5", 4), (8, "D5", 4), (12, "E5", 4)],
        [(0, "C5", 6), (6, "D5", 2), (8, "A4", 8)],
    ]
    emit(S, "lead", 0, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 8, MEL_A, 0.115, 0.12, 0.25)
    emit(S, "lead", 16, MEL_B, 0.115, 0.12, 0.25)
    engine_bass(S, prog, 0.26)
    drums_std(S, 24, rolls={7, 15, 23}, gk=0.26, gs=0.14, gh=0.018)
    pads(S, prog, bars=set(range(8, 16)), gain=0.026)
    arp_tail(S, prog, [15, 23])


# ================= 元数据 + 主流程 =================
META = [
    dict(id="a1", file="a1_steel_verdict", title="Steel Verdict", sub="Battle Theme",
         suite="A. Battle & Advance", bpm=140, bars=32, loop=True, key="A minor",
         tags="battle intense driving"),
    dict(id="a2", file="a2_bright_advance", title="Bright Advance", sub="World Map March",
         suite="A. Battle & Advance", bpm=126, bars=32, loop=True, key="C major",
         tags="map march hopeful"),
    dict(id="a3", file="a3_skirmish_rush", title="Skirmish Rush", sub="Fast Encounter",
         suite="A. Battle & Advance", bpm=164, bars=24, loop=True, key="E minor",
         tags="encounter fast urgent"),
    dict(id="a4", file="a4_shadow_draws_near", title="Shadow Draws Near", sub="Danger Approaches",
         suite="A. Battle & Advance", bpm=116, bars=32, loop=True, key="D minor",
         tags="tension danger stealth"),
    dict(id="a5", file="a5_ember_campfire", title="Ember Campfire", sub="Camp / Rest",
         suite="A. Battle & Advance", bpm=96, bars=24, loop=True, key="F major",
         tags="camp calm warm"),
    dict(id="b1", file="b1_knights_advance", title="Knights Advance", sub="Player Phase March",
         suite="B. Knights' Banner", bpm=132, bars=32, loop=True, key="Bb major",
         tags="player phase march heroic"),
    dict(id="b2", file="b2_iron_tide", title="Iron Tide", sub="Enemy Assault",
         suite="B. Knights' Banner", bpm=115, bars=32, loop=True, key="C minor",
         tags="enemy phase midboss"),
    dict(id="b3", file="b3_dark_lord_throne", title="Throne of the Dark Lord", sub="Final Boss",
         suite="B. Knights' Banner", bpm=100, bars=24, loop=True, key="C minor",
         tags="final boss dark heavy"),
    dict(id="b4", file="b4_victory_fanfare", title="Victory Fanfare", sub="Stage Clear Stinger",
         suite="B. Knights' Banner", bpm=123, bars=4, loop=False, key="G major",
         tags="victory stinger fanfare"),
    dict(id="b5", file="b5_levelup_jingle", title="Level Up!", sub="Level-up Jingle",
         suite="B. Knights' Banner", bpm=96, bars=1, loop=False, key="C major",
         tags="jingle levelup short"),
    dict(id="c1", file="c1_desert_caravan", title="Desert Caravan", sub="Hijaz Adventure",
         suite="C. Exotic Journey", bpm=100, bars=24, loop=True, key="D Hijaz",
         tags="desert exotic maqsum"),
    dict(id="c2", file="c2_shrine_of_petals", title="Shrine of Petals", sub="Eastern Temple",
         suite="C. Exotic Journey", bpm=84, bars=24, loop=True, key="E Hirajoshi",
         tags="shrine japanese calm"),
    dict(id="c3", file="c3_ruins_forgotten", title="Ruins of the Forgotten", sub="Ancient Ruins",
         suite="C. Exotic Journey", bpm=72, bars=20, loop=True, key="C Double Harmonic",
         tags="ruins mysterious dark"),
    dict(id="c4", file="c4_oasis_festival", title="Oasis Festival", sub="Exotic Dance",
         suite="C. Exotic Journey", bpm=128, bars=32, loop=True, key="G Hijaz",
         tags="festival dance tresillo"),
    dict(id="c5", file="c5_harbor_bazaar", title="Harbor Bazaar", sub="Lively Town",
         suite="C. Exotic Journey", bpm=108, bars=24, loop=True, key="A minor pentatonic",
         tags="town lively cheerful"),
]

BUILDERS = {m["id"]: globals()[f"build_{m['id']}"] for m in META}


def main():
    import csv
    rows = []
    for m in META:
        S = Song(m["bpm"], m["bars"], tail_bars=1 if m["loop"] else 2)
        BUILDERS[m["id"]](S)
        info = S.finish(m["file"], loop=m["loop"], trim=not m["loop"])
        write_midi(OUT / f'{m["file"]}.mid', m["bpm"], S.ev)
        loop_sec = round(4 * m["bars"] * 60 / m["bpm"], 3) if m["loop"] else ""
        rows.append([m["id"], m["file"], m["title"], m["sub"], m["suite"],
                     m["bpm"], m["key"], m["bars"], "loop" if m["loop"] else "oneshot",
                     loop_sec, m["tags"]])
        extra = f' tail_rms {info.get("tail_rms_db", 0):6.1f}dB' if not m["loop"] else ""
        print(f'{m["file"]:24s} {info["dur"]:7.2f}s  rms {info["rms_db"]:6.1f}dB  '
              f'seam x{info["seam_ratio"]:.3f}{extra}')
    with open(OUT / "TRACKLIST.csv", "w", newline="", encoding="utf-8") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["id", "file", "title", "sub", "suite", "bpm", "key",
                       "bars", "type", "loop_sec", "tags"])
        wcsv.writerows(rows)
    print(f"DONE {len(rows)} tracks -> {OUT}")


if __name__ == "__main__":
    main()
