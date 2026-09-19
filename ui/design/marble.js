/* Procedural polished marble. No image assets, no grain: veins are ridged,
   domain-warped noise, rendered small and upscaled so the face stays smooth.

   Marble.paint(canvas, { stone: "statuario" | "nero", veining: 0..1, seed: int }) */
(function () {
  const STONES = {
    statuario: { base: [246, 245, 242], cloud: [224, 224, 223], vein: [138, 140, 146], deep: [44, 46, 50] },
    nero: { base: [7, 7, 8], cloud: [24, 24, 27], vein: [206, 205, 200], deep: [255, 255, 252] },
  };

  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function perlin(seed) {
    const r = rng(seed);
    const p = new Uint8Array(512);
    const gx = new Float32Array(256);
    const gy = new Float32Array(256);
    for (let i = 0; i < 256; i++) {
      p[i] = i;
      const a = r() * Math.PI * 2;
      gx[i] = Math.cos(a);
      gy[i] = Math.sin(a);
    }
    for (let i = 255; i > 0; i--) {
      const j = (r() * (i + 1)) | 0;
      const t = p[i]; p[i] = p[j]; p[j] = t;
    }
    for (let i = 0; i < 256; i++) p[i + 256] = p[i];
    const fade = (t) => t * t * t * (t * (t * 6 - 15) + 10);
    return function (x, y) {
      const xi = Math.floor(x), yi = Math.floor(y);
      const xf = x - xi, yf = y - yi;
      const X = xi & 255, Y = yi & 255;
      const aa = p[p[X] + Y], ab = p[p[X] + Y + 1], ba = p[p[X + 1] + Y], bb = p[p[X + 1] + Y + 1];
      const u = fade(xf), v = fade(yf);
      const n00 = gx[aa] * xf + gy[aa] * yf;
      const n10 = gx[ba] * (xf - 1) + gy[ba] * yf;
      const n01 = gx[ab] * xf + gy[ab] * (yf - 1);
      const n11 = gx[bb] * (xf - 1) + gy[bb] * (yf - 1);
      const a = n00 + u * (n10 - n00);
      return a + v * (n01 + u * (n11 - n01) - a);
    };
  }

  const smooth = (a, b, x) => {
    const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
    return t * t * (3 - 2 * t);
  };

  function paint(canvas, opts) {
    const o = Object.assign({ stone: "statuario", veining: 0.7, seed: 11 }, opts);
    const S = STONES[o.stone] || STONES.statuario;
    const cw = canvas.clientWidth || 1200, ch = canvas.clientHeight || 800;
    const W = Math.min(640, Math.max(240, Math.round(cw / 2)));
    const H = Math.max(160, Math.round((W * ch) / cw));
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(W, H);
    const d = img.data;
    const n = perlin(o.seed);
    const fbm = (x, y, oct) => {
      let s = 0, a = 0.5, f = 1;
      for (let i = 0; i < oct; i++) { s += a * n(x * f, y * f); a *= 0.5; f *= 2.03; }
      return s;
    };
    const r = rng(o.seed * 7 + 3);
    const ang = 0.5 + r() * 0.7;
    const dx = Math.cos(ang), dy = Math.sin(ang);
    const k = o.veining;

    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const u = (x / W) * (W / H) * 1.4, v = (y / H) * 1.4;
        const qx = fbm(u * 0.9, v * 0.9, 3), qy = fbm(u * 0.9 + 5.2, v * 0.9 + 1.3, 3);
        const w = fbm(u * 1.1 + 1.2 * qx, v * 1.1 + 1.2 * qy, 4);
        const jitter = fbm(u * 5.1 + 40, v * 5.1 + 17, 2) * 0.1;

        // main veins: the run direction dominates the warp, so they travel across
        // the slab instead of closing into loops; width and presence vary along the run
        const t1 = (u * dx + v * dy) * 1.8 + w * 0.9 + jitter;
        const ridge1 = 1 - Math.abs(Math.sin(t1 * Math.PI));
        const mask1 = smooth(-0.1, 0.2, fbm(u * 0.7 + 9.1, v * 0.7 + 4.4, 2));
        const width = 14 + 50 * smooth(-0.3, 0.3, fbm(u * 1.7 + 3.3, v * 1.7 + 8.8, 2));
        const main = Math.pow(ridge1, width) * mask1;
        const halo = Math.pow(ridge1, 6) * mask1 * 0.1;

        // hairlines crossing at a second angle
        const t2 = (u * dy - v * dx) * 3.2 + w * 1.4 + jitter * 2 + 0.37;
        const ridge2 = 1 - Math.abs(Math.sin(t2 * Math.PI));
        const mask2 = smooth(0.04, 0.3, fbm(u * 1.1 + 31.7, v * 1.1 + 12.9, 2));
        const hair = Math.pow(ridge2, 50) * mask2 * 0.4;

        const cloud = smooth(-0.35, 0.45, fbm(u * 0.8 + 20, v * 0.8 + 7, 3)) * 0.55;
        const veinAmt = Math.min(1, (main * 0.85 + hair + halo) * k);
        const deepAmt = Math.min(1, Math.pow(ridge1, width * 3) * mask1 * k * 0.6);

        const i = (y * W + x) * 4;
        for (let c = 0; c < 3; c++) {
          let val = S.base[c] + (S.cloud[c] - S.base[c]) * cloud * (0.4 + 0.6 * k);
          val += (S.vein[c] - val) * veinAmt;
          val += (S.deep[c] - val) * deepAmt;
          d[i + c] = val;
        }
        d[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  window.Marble = { paint, STONES };
})();
