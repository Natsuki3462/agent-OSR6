const fs = require("fs");
const src = fs.readFileSync("webui.html", "utf8");

const start = src.indexOf("const ISO = Math.PI / 6;");
const end = src.indexOf("const LIGHT =");
if (start < 0 || end < 0) { console.error("找不到投影代码段"); process.exit(1); }
const chunk = src.slice(start, end);

let mod;
try {
  mod = new Function(chunk + "\nreturn { CAM, CAM_DEF, PITCH_MAX, ZOOM_MIN, ZOOM_MAX, camP, projCam, proj };")();
} catch (e) {
  console.error("[FAIL] 语法错误:", e.message);
  process.exit(1);
}
console.log("[OK] 投影代码段语法正确");

const { CAM, camP, projCam, proj } = mod;
const o = { cx: 300, cy: 400, s: 1.0 };
let pass = 0, fail = 0;
function ck(cond, msg, detail) {
  if (cond) { pass++; } else { fail++; console.log("  [FAIL]", msg, detail === undefined ? "" : detail); }
}

let q = proj({ x: 0, y: 0, z: 0 }, o);
ck(Math.abs(q[0] - o.cx) < 1e-9 && Math.abs(q[1] - o.cy) < 1e-9,
   "原点应投到 (cx,cy)", q);

const pts = [
  { x: 30, y: -12, z: 7 }, { x: -55, y: 88, z: 21 },
  { x: 0, y: 140, z: 0 }, { x: 78, y: 7, z: -78 },
];
for (const yaw of [0, 0.4, 1.1, -2.2, 3.0]) {
  for (const pitch of [0, -0.7, 0.9, 1.15]) {
    CAM.yaw = yaw; CAM.pitch = pitch; CAM.zoom = 1;
    for (const p of pts) {
      const r = camP(p);
      const L0 = Math.hypot(p.x, p.y, p.z);
      const L1 = Math.hypot(r.x, r.y, r.z);
      ck(Math.abs(L0 - L1) < 1e-9, "camP 应保长（纯旋转）",
         { yaw, pitch, L0, L1 });
    }
  }
}

CAM.yaw = 0.5; CAM.pitch = 0.3;
CAM.zoom = 1;
const a1 = proj({ x: 40, y: 20, z: -10 }, o);
CAM.zoom = 2;
const a2 = proj({ x: 40, y: 20, z: -10 }, o);
ck(Math.abs((a2[0] - o.cx) - 2 * (a1[0] - o.cx)) < 1e-9 &&
   Math.abs((a2[1] - o.cy) - 2 * (a1[1] - o.cy)) < 1e-9,
   "zoom=2 应把偏移正好翻倍", { a1, a2 });

CAM.yaw = Math.PI / 2; CAM.pitch = 0; CAM.zoom = 1;
const r90 = camP({ x: 10, y: 0, z: 0 });
ck(Math.abs(r90.x) < 1e-9 && Math.abs(r90.z + 10) < 1e-9,
   "yaw=90° 应把 +x 转到 -z", r90);

CAM.yaw = 0; CAM.pitch = Math.PI / 2;
const rp = camP({ x: 0, y: 10, z: 0 });
ck(Math.abs(rp.y) < 1e-9 && Math.abs(rp.z - 10) < 1e-9,
   "pitch=90° 应把 +y 转到 +z", rp);

CAM.yaw = 0; CAM.pitch = 0; CAM.zoom = 1;
for (const p of pts) {
  const q1 = proj(p, o);
  const iso = (p.x - p.z) * Math.cos(Math.PI / 6);
  const isy = (p.x + p.z) * Math.sin(Math.PI / 6) - p.y;
  ck(Math.abs(q1[0] - (o.cx + iso)) < 1e-9 &&
     Math.abs(q1[1] - (o.cy + isy)) < 1e-9,
     "复位后应回到原始等轴投影", { p, q1 });
}

ck(Math.abs(100 * 0.007 - 0.7) < 1e-9, "拖 100px ≈ 0.7rad(40°)");

const dStart = src.indexOf("const ISO = Math.PI / 6;");
const dEnd = src.indexOf("function bindCam()");
if (dStart < 0 || dEnd < 0) { console.error("[FAIL] 找不到绘制代码段"); process.exit(1); }
const drawChunk = src.slice(dStart, dEnd);

function frame(yaw, pitch, zoom, axes) {
  const calls = Object.create(null);
  const ctx = new Proxy({}, {
    get(_, k) {
      if (k === "createRadialGradient") return () => ({ addColorStop() {} });
      if (k === "canvas") return {};
      return () => { calls[k] = (calls[k] || 0) + 1; };
    },
    set() { return true; }
  });
  const C = Object.assign({}, axes);
  const T = Object.assign({}, axes);
  const doc = { body: { classList: { contains: () => false } } };
  const body = drawChunk
    + `\nCAM.yaw=${yaw};CAM.pitch=${pitch};CAM.zoom=${zoom};draw();return calls;`;
  const fn = new Function(
    "ctx", "C", "T", "AXES_ALL", "VW", "VH", "document",
    "requestAnimationFrame", "resizeCanvas", "calls", body);
  fn(ctx, C, T, ["L0", "L1", "L2", "R0", "R1", "R2", "V0"],
     800, 600, doc, () => 0, () => {}, calls);
  return calls;
}

const AX = { L0: 60, L1: 40, L2: 70, R0: 30, R1: 80, R2: 50, V0: 20 };
const views = [
  ["默认视角", 0, 0, 1],
  ["左转 90°", Math.PI / 2, 0, 1],
  ["俯视到极限", 0, 1.15, 1],
  ["仰视到极限", 0, -1.15, 1],
  ["放大 2.8 倍", 0.6, 0.4, 2.8],
  ["缩小到 0.45", -1.2, -0.5, 0.45],
];
const BASE = { lineTo: 73, fill: 19 };
for (const [name, yaw, pitch, zoom] of views) {
  let calls;
  try {
    calls = frame(yaw, pitch, zoom, AX);
  } catch (e) {
    fail++; console.log("  [FAIL] 渲染抛异常:", name, e.message);
    continue;
  }
  ck((calls.lineTo || 0) === BASE.lineTo,
     `${name} 线段数应不变（几何结构没丢）`,
     { got: calls.lineTo, want: BASE.lineTo });
  ck((calls.fill || 0) === BASE.fill,
     `${name} 填充数应不变（每个面都还在）`,
     { got: calls.fill, want: BASE.fill });
  ck((calls.beginPath || 0) >= BASE.fill, `${name} 每条路径都应闭合`, calls.beginPath);
}

const fStart = src.indexOf("function feedEmu3D()");
const fEnd = src.indexOf("function paintEmu3D(");
if (fStart < 0 || fEnd < 0) {
  console.error("[FAIL] 找不到 feedEmu3D");
  process.exit(1);
}
const feedFn = new Function("S", "AXES", "EMU3D",
  src.slice(fStart, fEnd) + "\nreturn feedEmu3D;");

function feed(out, real) {
  const written = [];
  const EMU3D = { inst: { write: (t) => written.push(t) }, ok: true, sig: "" };
  const S = { out: out, real: !!real };
  feedFn(S, ["L0", "L1", "L2", "R0", "R1", "R2"], EMU3D)();
  return written;
}

let w = feed({ L0: 50, L1: 50, L2: 50, R0: 50, R1: 50, R2: 50 });
ck(w.length === 1, "中位应写一条指令", w);
ck(w[0] === "L05000 L15000 L25000 R05000 R15000 R25000\n",
   "中位应编码成 5000", w[0]);

w = feed({ L0: 0, L1: 100, L2: 0, R0: 100, R1: 0, R2: 100 });
ck(w[0].startsWith("L00000 L19999 L20000 R09999 R10000 R29999"),
   "0/100 两端应编码成 0000/9999", w[0]);

w = feed({ L0: 5, L1: 37, L2: 62, R0: 8, R1: 99, R2: 1 });
ck(w[0].startsWith("L00500 L13700 L26200 R00800 R19900 R20100"),
   "中间值前导零不能丢", w[0]);

w = feed({ L0: 80, L1: 20, L2: 50, R0: 50, R1: 50, R2: 50 }, true);
ck(w.length === 0, "真机模式不应写 TCode（视图冻结）", w);

{
  const written = [];
  const EMU3D = { inst: { write: (t) => written.push(t) }, ok: true, sig: "" };
  const S = { out: { L0: 60 }, real: false };
  const f = feedFn(S, ["L0"], EMU3D);
  f(); f(); f();
  ck(written.length === 1, "同值不应重复下发", written);
  S.out = { L0: 61 };
  f();
  ck(written.length === 2, "值变了应再发一条", written);
}

w = feed({ L0: 50 });
ck(w[0] === "L05000\n", "缺的轴应跳过", w[0]);

{
  const vpath = "vendor/osr-emu.dist.min.js";
  if (!fs.existsSync(vpath)) {
    console.log(`  [warn] 没有 ${vpath}（页面会退回手绘视图，不算错）`);
  } else {
    const code = fs.readFileSync(vpath, "utf8");
    const vm = require("vm");
    const win = {};
    const noop = () => {};
    const c = {
      window: win, self: win, console,
      performance: { now: () => Date.now() },
      requestAnimationFrame: () => 0, cancelAnimationFrame: noop,
      setTimeout, clearTimeout, setInterval, clearInterval,
      document: {
        createElement: () => ({ getContext: () => null, style: {}, setAttribute: noop }),
        createElementNS: () => ({ getContext: () => null, style: {}, setAttribute: noop }),
        querySelector: () => null, addEventListener: noop,
        body: { appendChild: noop },
      },
      navigator: { userAgent: "node" },
      devicePixelRatio: 1,
    };
    c.globalThis = c;
    win.document = c.document;
    win.navigator = c.navigator;
    try {
      vm.createContext(c);
      vm.runInContext(code, c, { timeout: 30000 });
      ck(typeof win.OSREmulator === "function",
         "vendor 库应暴露 window.OSREmulator", typeof win.OSREmulator);
      ck(typeof win.OSREmulator.prototype.write === "function",
         "OSREmulator 应有 write()");
      ck(typeof win.OSREmulator.prototype.destroy === "function",
         "OSREmulator 应有 destroy()");
    } catch (e) {
      ck(false, "vendor 库应能加载", e.message);
    }
  }
}

console.log(`[OK] 渲染冒烟：${views.length} 个视角各跑一帧，无异常`);
console.log("[OK] osr-emu 接线：TCode 换算 / 真机冻结 / 去重 全部通过");

console.log(fail === 0
  ? `[OK] 相机数学 ${pass} 项断言全部通过`
  : `[FAIL] ${fail} 项失败 / ${pass} 通过`);
process.exit(fail === 0 ? 0 : 1);
