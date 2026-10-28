from __future__ import annotations

import math
import random

AXES = ["L0", "L1", "L2", "R0", "R1", "R2"]
CENTER = 0.5

PRESET_INFO6 = {
    "sway":    ("左右摇摆", "L2 大幅横扫 + L0 慢冲程 + R0 反相扭转"),
    "orbit":   ("环游画圆", "L0/L1/L2 走椭圆轨迹，R1/R2 轻微跟随"),
    "roll":    ("翻滚",     "R1 连续大滚转 + L0 行程 + L2 摆动"),
    "pitch":   ("俯仰推拉", "R2 俯仰波 + L1 前后推拉 + L0 行程"),
    "figure8": ("八字回环", "L1/L2 走 8 字轨迹 + R0 扭转跟随"),
    "storm":   ("六轴乱流", "六轴各自 0.1-0.5 秒随机乱跳，无规律"),
    "tide":    ("潮汐",     "极慢的六轴漂移，幅度小、周期长，几乎不停顿"),
    "coil":    ("缠绕",     "R0/R1 画圆拧转 + L0 长行程慢推"),
    "pulse":   ("脉冲",     "L0 短促抽动后停顿，L1/L2 轻微跟随"),
    "weave":   ("编织",     "L1/L2 走 2:3 李萨如曲线，R0 轻扭"),
}

GENTLE = {"tide", "coil", "weave"}


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def home() -> dict:
    out = {a: CENTER for a in AXES}
    out["V0"] = 0.0
    return out


def _scale(v, intensity, center=CENTER):
    return clamp(center + (v - center) * intensity)


class Runner6:
    def __init__(self, preset, seed=None, intensity=1.0):
        if preset not in PRESET_INFO6:
            raise ValueError(f"没有这个六轴预设：{preset}")
        self.preset = preset
        self.rng = random.Random(seed)
        self.intensity = max(0.1, float(intensity))

        self.next_event = self.rng.uniform(3.0, 7.0)
        self.pause_until = -1.0
        self.burst_until = -1.0

        self.stage = 1.0
        self.next_stage = self.rng.uniform(40, 90)

        self.next_step = 0.0
        self.step_len = 0.2
        self.cur = home()

        self.cycle = self.rng.uniform(6.0, 12.0)
        self.phase = self.rng.uniform(0, math.tau)

    def _gap(self) -> float:
        if self.preset in GENTLE:
            return self.rng.uniform(15.0, 35.0)
        return self.rng.uniform(3.5, 9.0)

    def _events(self, t):
        if t > self.next_stage:
            self.stage = self.rng.choice([0.75, 0.9, 1.0, 1.1, 1.2])
            self.next_stage = t + self.rng.uniform(40, 90)

        if t <= self.next_event:
            return
        r = self.rng.random()
        if self.preset == "storm":
            if r < 0.30:
                self.pause_until = t + self.rng.uniform(0.4, 1.6)
            elif r < 0.55:
                self.burst_until = t + self.rng.uniform(0.3, 1.0)
        elif self.preset in GENTLE:
            if r < 0.35:
                self.pause_until = t + self.rng.uniform(1.0, 2.5)
        else:
            if r < 0.30:
                self.pause_until = t + self.rng.uniform(0.6, 2.2)
            elif r < 0.48:
                self.burst_until = t + self.rng.uniform(0.3, 1.0)
        self.next_event = t + self._gap()

    def _gen(self, t, I) -> dict:
        p = self.preset
        rng = self.rng
        w = math.tau / self.cycle
        a = self.phase

        if p == "sway":
            sway = 0.5 + 0.26 * math.sin(t * w * 0.8 + a)
            stroke = 0.5 + 0.34 * math.sin(t * w * 1.6 + a * 0.5)
            stroke += rng.uniform(-0.04, 0.04)
            return {
                "L0": _scale(stroke, I),
                "L1": _scale(0.5 + 0.10 * math.sin(t * w * 0.8 + a + 1.0), I),
                "L2": _scale(sway, I),
                "R0": _scale(0.5 - 0.22 * math.sin(t * w * 0.8 + a), I),
                "R1": _scale(0.5 + 0.12 * math.sin(t * w * 1.6 + a), I),
                "R2": _scale(0.5 + 0.08 * math.sin(t * w * 0.5 + a), I),
                "V0": clamp(0.25 + 0.2 * math.sin(t * 0.7)),
            }

        if p == "orbit":
            e = w * 0.9
            return {
                "L0": _scale(0.5 + 0.30 * math.sin(t * e + a), I),
                "L1": _scale(0.5 + 0.24 * math.sin(t * e + a + 2.094), I),
                "L2": _scale(0.5 + 0.24 * math.sin(t * e + a + 4.189), I),
                "R0": _scale(0.5 + 0.14 * math.sin(t * e * 0.5 + a), I),
                "R1": _scale(0.5 + 0.16 * math.sin(t * e + a + 3.1416), I),
                "R2": _scale(0.5 + 0.16 * math.sin(t * e + a + 1.047), I),
                "V0": clamp(0.2 + 0.15 * math.sin(t * 0.5 + a)),
            }

        if p == "roll":
            roll = 0.5 + 0.36 * math.sin(t * w * 1.2 + a)
            return {
                "L0": _scale(0.5 + 0.30 * math.sin(t * w * 1.2 + a + 1.2), I),
                "L1": _scale(0.5 + 0.10 * math.sin(t * w * 0.6 + a), I),
                "L2": _scale(0.5 + 0.18 * math.sin(t * w * 0.6 + a + 0.8), I),
                "R0": _scale(0.5 + 0.14 * math.sin(t * w + a), I),
                "R1": _scale(roll, I),
                "R2": _scale(0.5 + 0.12 * math.sin(t * w * 1.2 + a + 3.14), I),
                "V0": clamp(0.3 + 0.2 * math.sin(t * 1.1)),
            }

        if p == "pitch":
            pitch = 0.5 + 0.30 * math.sin(t * w * 1.1 + a)
            return {
                "L0": _scale(0.5 + 0.28 * math.sin(t * w * 1.1 + a + 0.7), I),
                "L1": _scale(0.5 + 0.28 * math.sin(t * w * 1.1 + a), I),
                "L2": _scale(0.5 + 0.08 * math.sin(t * w * 0.5 + a), I),
                "R0": _scale(0.5 + 0.10 * math.sin(t * w * 0.55 + a), I),
                "R1": _scale(0.5 + 0.10 * math.sin(t * w * 1.1 + a + 3.14), I),
                "R2": _scale(pitch, I),
                "V0": clamp(0.25 + 0.2 * math.sin(t * 0.9 + a)),
            }

        if p == "figure8":
            return {
                "L0": _scale(0.5 + 0.32 * math.sin(t * w * 1.5 + a), I),
                "L1": _scale(0.5 + 0.28 * math.sin(t * w + a), I),
                "L2": _scale(0.5 + 0.26 * math.sin(t * w * 2 + a), I),
                "R0": _scale(0.5 + 0.20 * math.sin(t * w + a + 1.57), I),
                "R1": _scale(0.5 + 0.14 * math.sin(t * w * 2 + a + 3.14), I),
                "R2": _scale(0.5 + 0.12 * math.sin(t * w + a + 3.14), I),
                "V0": clamp(0.2 + 0.2 * math.sin(t * 1.3 + a)),
            }

        if p == "tide":
            slow = w * 0.22
            return {
                "L0": _scale(0.5 + 0.20 * math.sin(t * slow + a), I),
                "L1": _scale(0.5 + 0.13 * math.sin(t * slow + a + 1.0), I),
                "L2": _scale(0.5 + 0.11 * math.sin(t * slow * 1.3 + a + 2.1), I),
                "R0": _scale(0.5 + 0.10 * math.sin(t * slow * 0.8 + a + 0.5), I),
                "R1": _scale(0.5 + 0.07 * math.sin(t * slow * 0.6 + a + 1.7), I),
                "R2": _scale(0.5 + 0.08 * math.sin(t * slow * 0.9 + a + 3.0), I),
                "V0": clamp(0.08 + 0.07 * math.sin(t * 0.25 + a)),
            }

        if p == "coil":
            c = w * 0.55
            return {
                "L0": _scale(0.5 + 0.24 * math.sin(t * c * 0.5 + a), I),
                "L1": _scale(0.5 + 0.12 * math.sin(t * c + a + 0.9), I),
                "L2": _scale(0.5 + 0.12 * math.sin(t * c + a + 2.4), I),
                "R0": _scale(0.5 + 0.30 * math.sin(t * c + a), I),
                "R1": _scale(0.5 + 0.28 * math.sin(t * c + a + 1.5708), I),
                "R2": _scale(0.5 + 0.10 * math.sin(t * c * 0.5 + a + 1.2), I),
                "V0": clamp(0.12 + 0.10 * math.sin(t * 0.4 + a)),
            }

        if p == "pulse":
            period = 2.4
            frac = (t / period) % 1.0
            env = max(0.0, math.sin(math.pi * frac)) ** 3
            return {
                "L0": _scale(0.5 + 0.34 * env, I),
                "L1": _scale(0.5 + 0.12 * env, I),
                "L2": _scale(0.5 + 0.08 * math.sin(t * w * 0.5 + a) * (0.3 + 0.7 * env), I),
                "R0": _scale(0.5 + 0.16 * env * math.sin(t * 0.9 + a), I),
                "R1": _scale(0.5 + 0.10 * env, I),
                "R2": _scale(0.5 + 0.09 * env, I),
                "V0": clamp(0.55 * env),
            }

        if p == "weave":
            return {
                "L0": _scale(0.5 + 0.16 * math.sin(t * w * 0.5 + a), I),
                "L1": _scale(0.5 + 0.22 * math.sin(t * w * 2.0 + a), I),
                "L2": _scale(0.5 + 0.22 * math.sin(t * w * 3.0 + a + 1.2), I),
                "R0": _scale(0.5 + 0.14 * math.sin(t * w * 1.0 + a + 1.57), I),
                "R1": _scale(0.5 + 0.09 * math.sin(t * w * 2.0 + a + 0.6), I),
                "R2": _scale(0.5 + 0.07 * math.sin(t * w * 0.5 + a + 2.6), I),
                "V0": clamp(0.10 + 0.08 * math.sin(t * 0.6 + a)),
            }

        if t > self.next_step:
            self.step_len = rng.uniform(0.1, 0.5)
            self.next_step = t + self.step_len
            I2 = I * self.stage
            self.cur = {
                "L0": clamp(0.5 + rng.uniform(-0.48, 0.48) * I2),
                "L1": clamp(0.5 + rng.uniform(-0.40, 0.40) * I2),
                "L2": clamp(0.5 + rng.uniform(-0.40, 0.40) * I2),
                "R0": clamp(0.5 + rng.uniform(-0.45, 0.45) * I2),
                "R1": clamp(0.5 + rng.uniform(-0.45, 0.45) * I2),
                "R2": clamp(0.5 + rng.uniform(-0.35, 0.35) * I2),
                "V0": rng.uniform(0.0, 0.9),
            }
        return dict(self.cur)

    def step(self, t) -> dict:
        self._events(t)
        I = self.intensity * self.stage

        if t < self.pause_until:
            return home()

        if t < self.burst_until:
            rng = self.rng
            return {
                "L0": clamp(0.5 + rng.uniform(0.3, 0.48)),
                "L1": clamp(0.5 + rng.uniform(-0.4, 0.4)),
                "L2": clamp(0.5 + rng.uniform(-0.4, 0.4)),
                "R0": clamp(0.5 + rng.uniform(-0.48, 0.48)),
                "R1": clamp(0.5 + rng.uniform(-0.48, 0.48)),
                "R2": clamp(0.5 + rng.uniform(-0.35, 0.35)),
                "V0": rng.uniform(0.6, 1.0),
            }

        vals = self._gen(t, I)
        return {k: clamp(v) for k, v in vals.items()}


def format_line(t: float, axes: dict) -> str:
    parts = [f"{t:6.2f}s"]
    for a in AXES:
        parts.append(f"{a}={axes.get(a, CENTER):.2f}")
    if "V0" in axes:
        parts.append(f"V0={axes['V0']:.2f}")
    return "  ".join(parts)
