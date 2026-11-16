/*
 * inpage_measure.js — HTML スライドの可読性の基準をページの中で実測し、判定する。
 *
 * 実測と判定はすべてこのファイルの中にある。check_html.mjs はページを組み立てて
 * このファイルをページ内で実行するだけ。
 *
 * ■ Node や Playwright が無いときの使い方
 *   スライドを開いたブラウザのタブで、このファイルの中身をそのまま実行する
 *   （DevTools のコンソール、またはエージェントのページ内 JavaScript 実行のツール）。
 *   ファイル全体が 1 つの式で、結果は Promise<結果オブジェクト> になる。
 *   グローバル変数は作らない。ツールが Promise を待たない場合は、
 *   先頭に「window.__r = 」を付けて実行し、あとで JSON.stringify(window.__r) を読む
 *   （この場合だけ __r がグローバルに残る）。
 *
 * ■ 引数の渡し方
 *   ファイル末尾の「/*OPTIONS*\/{}」の {} をオブジェクトに書き換える。例:
 *     })(/*OPTIONS*\/{ width: 1920, height: 1080, slideSelector: "section" });
 *   主な引数（すべて省略可）:
 *     slideSelector     スライド要素（既定 "section"）
 *     footnoteSelector  脚注の役割（既定 ".src, .pg, [data-role=\"footnote\"], [data-role=\"page\"]"）
 *     headlineSelector  見出し（既定 "h1, h2"）
 *     width, height     スライドのサイズ px（省略時は各スライド要素の大きさ）
 *     marginIn          左右の余白 in（既定 0.6）
 *     marginVIn         上下の余白 in（既定 0.4）
 *     thresholds        閾値の上書き（下の DEFAULT_THRESHOLDS と同じ名前、単位は pt）
 *     scroll            false にすると各スライドへスクロールしない（既定 true）
 *   check_html.mjs は「/*OPTIONS*\/{}」を JSON で置き換えて実行する。
 *
 * ■ 単位
 *   閾値は「高さ 7.5 in のスライドでの pt」。スライドの高さ px を 540 pt とみなして換算する。
 *   座標は各スライドの左上を原点にした CSS px（スライドが縮小表示されていても戻して数える）。
 */
(async (options) => {
  'use strict';

  const VERSION = '1.0.0';
  const DEFAULT_THRESHOLDS = {
    bodyPt: 18,            // G1 本文の最小
    footnotePt: 12,        // G1 脚注の役割の最小
    contrastBody: 7.0,     // G2 本文
    contrastLarge: 4.5,    // G2 大きい文字と脚注の役割
    largePt: 24,           // G2 大きい文字とみなす大きさ
    largeBoldPt: 18,       // G2 太字なら大きい文字とみなす大きさ
    overflowPt: 1,         // G3/G4 はみ出し・あふれの許容
    overlapPt: 1,          // G5 重なりの許容
    headlineMaxLines: 2,   // G6 見出しの行数の上限
    marginTolPt: 0.5,      // I1 余白の許容
    rightGapPt: 12,        // I2 枠の右端までの空きの下限
    orphanChars: 2,        // I2 最後の行の文字数がこれ以下なら報告
  };
  const opt = Object.assign({
    slideSelector: 'section',
    footnoteSelector: '.src, .pg, [data-role="footnote"], [data-role="page"]',
    headlineSelector: 'h1, h2',
    width: null,
    height: null,
    marginIn: 0.6,
    marginVIn: 0.4,
    scroll: true,
  }, options || {});
  const th = Object.assign({}, DEFAULT_THRESHOLDS, opt.thresholds || {});

  const r1 = (x) => Math.round(x * 10) / 10;
  const r2 = (x) => Math.round(x * 100) / 100;
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  const nextFrame = () => Promise.race([
    new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res))),
    sleep(150),
  ]);

  // ---- Web フォントの読み込み完了を待つ ----
  void document.body.offsetHeight;
  await document.fonts.ready;
  await nextFrame();
  await document.fonts.ready;

  const warnings = [];

  // ---- 色 ----
  const colorCache = new Map();
  let colorCtx = null;
  function parseColor(str) {
    if (colorCache.has(str)) return colorCache.get(str);
    let c = null;
    const m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/.exec(str);
    if (m) {
      let a = m[4] === undefined ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
      c = { r: +m[1], g: +m[2], b: +m[3], a };
    } else if (str === 'transparent') {
      c = { r: 0, g: 0, b: 0, a: 0 };
    } else {
      // oklch() や color() などは 1 画素に塗って読み戻す
      if (!colorCtx) {
        const cv = document.createElement('canvas');
        cv.width = cv.height = 1;
        colorCtx = cv.getContext('2d', { willReadFrequently: true });
      }
      colorCtx.clearRect(0, 0, 1, 1);
      colorCtx.fillStyle = '#000';
      colorCtx.fillStyle = str;
      colorCtx.fillRect(0, 0, 1, 1);
      const d = colorCtx.getImageData(0, 0, 1, 1).data;
      c = { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
    }
    colorCache.set(str, c);
    return c;
  }
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
  const blend = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  function luminance(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function contrast(a, b) {
    const la = luminance(a), lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }

  // ---- 要素の補助 ----
  const csCache = new Map();
  const cs = (el) => { let s = csCache.get(el); if (!s) { s = getComputedStyle(el); csCache.set(el, s); } return s; };
  const MEDIA_TAGS = new Set(['IMG', 'VIDEO', 'CANVAS', 'IFRAME', 'OBJECT', 'EMBED', 'PICTURE']);
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TITLE']);
  const isSvgRoot = (el) => el instanceof SVGSVGElement && !(el.parentElement instanceof SVGElement);
  const isMedia = (el) => MEDIA_TAGS.has(el.tagName) || isSvgRoot(el);

  function splitSelector(sel) {
    // 括弧の外のカンマで分ける
    const out = []; let depth = 0, cur = '';
    for (const ch of sel) {
      if (ch === '(' || ch === '[') depth++;
      if (ch === ')' || ch === ']') depth--;
      if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }
  const footParts = splitSelector(opt.footnoteSelector);

  function shortText(el) {
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const chars = [...t];
    return chars.length > 20 ? chars.slice(0, 20).join('') + '…' : t;
  }

  function cssPath(el, root) {
    const segs = [];
    let e = el;
    while (e && e.nodeType === 1) {
      let s = e.tagName.toLowerCase();
      if (e.id) s += '#' + e.id;
      else {
        const cls = typeof e.className === 'string' ? e.className.trim().split(/\s+/).filter(Boolean) : [];
        if (cls.length) s += '.' + cls.join('.');
        const p = e.parentElement;
        if (p) {
          const same = [...p.children].filter((c) => c.tagName === e.tagName);
          if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(e) + 1) + ')';
        }
      }
      segs.unshift(s);
      if (e === root) break;
      e = e.parentElement;
    }
    return segs.join(' > ');
  }

  // ---- スライドを集める ----
  const slides = [...document.querySelectorAll(opt.slideSelector)]
    .filter((s) => !(s.parentElement && s.parentElement.closest(opt.slideSelector)));
  if (!slides.length) {
    return { tool: 'inpage_measure', version: VERSION, error: 'スライドの要素が見つからない（セレクタ: ' + opt.slideSelector + '）', slides: [] };
  }

  // ---- フォントの確認 ----
  const GENERIC = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui', 'ui-serif', 'ui-sans-serif', 'ui-monospace', 'ui-rounded', 'emoji', 'math', 'fangsong', '-apple-system', 'blinkmacsystemfont']);
  const normFam = (f) => f.trim().replace(/^["']|["']$/g, '').toLowerCase();
  function localFontAvailable(fam) {
    const cv = document.createElement('canvas');
    const ctx = cv.getContext('2d');
    const sample = 'mmmmmmmmmmlli WQ@ 永あア漢字';
    for (const base of ['monospace', 'serif', 'sans-serif']) {
      ctx.font = '40px ' + base;
      const w0 = ctx.measureText(sample).width;
      ctx.font = '40px "' + fam.replace(/"/g, '') + '", ' + base;
      if (Math.abs(ctx.measureText(sample).width - w0) > 0.5) return true;
    }
    return false;
  }
  const famUse = new Map(); // 正規化名 → { name, count }

  // ---- 1 枚ずつ実測 ----
  const results = [];
  const footnoteRecords = [];
  for (let si = 0; si < slides.length; si++) {
    const slide = slides[si];
    if (opt.scroll !== false) {
      slide.scrollIntoView({ block: 'start', inline: 'start' });
      await nextFrame();
    }
    csCache.clear();
    const sr = slide.getBoundingClientRect();
    const scale = slide.offsetWidth ? sr.width / slide.offsetWidth : 1;
    const W = opt.width || slide.offsetWidth;
    const H = opt.height || slide.offsetHeight;
    const ptPerPx = 540 / H;
    const pxPerPt = 1 / ptPerPx;
    const pxPerIn = H / 7.5;
    const toS = (r) => ({ l: (r.left - sr.left) / scale, t: (r.top - sr.top) / scale, r: (r.right - sr.left) / scale, b: (r.bottom - sr.top) / scale });
    const rnd = (b) => ({ l: Math.round(b.l), t: Math.round(b.t), r: Math.round(b.r), b: Math.round(b.b) });

    const inAside = (el) => { const a = el.closest('aside'); return !!a && slide.contains(a); };
    const hidden = (el) => {
      if (inAside(el)) return true;
      if (el.checkVisibility) return !el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true });
      const s = cs(el); return s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0';
    };
    const isFoot = (el) => { const f = el.closest(opt.footnoteSelector); return !!f && slide.contains(f); };
    const desc = (el) => {
      const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).filter(Boolean) : [];
      return {
        tag: el.tagName.toLowerCase(), id: el.id || undefined, classes: cls, text: shortText(el),
        path: cssPath(el, slide), rect: rnd(toS(el.getBoundingClientRect())),
      };
    };

    // 要素の拡大率（transform や SVG の viewBox を含む。スライド自体の縮小は除く）
    const scaleCache = new Map();
    function elemScale(el) {
      if (scaleCache.has(el)) return scaleCache.get(el);
      let k = 1;
      if (el instanceof SVGElement && el.getScreenCTM) {
        const m = el.getScreenCTM();
        if (m) k = Math.sqrt(Math.abs(m.a * m.d - m.b * m.c)) / scale;
      } else {
        let e = el;
        while (e && e !== slide && !(e.offsetWidth > 0)) e = e.parentElement;
        if (e && e !== slide && e.offsetWidth > 0) k = e.getBoundingClientRect().width / e.offsetWidth / scale;
      }
      if (!isFinite(k) || k <= 0) k = 1;
      scaleCache.set(el, k);
      return k;
    }

    // 文字のまとまり（行を作る枠）= 表示が inline でない最も近い祖先
    function blockOf(el) {
      let e = el;
      while (e && e !== slide) {
        if (e instanceof SVGElement) {
          if (e.tagName.toLowerCase() === 'text') return e;
          e = e.parentElement; continue;
        }
        const d = cs(e).display;
        if (d === 'inline' || d === 'contents' || d.startsWith('ruby')) { e = e.parentElement; continue; }
        return e;
      }
      return slide;
    }

    // ---- 文字を 1 字ずつ実測する ----
    const blocks = new Map(); // block → { el, chars:[], textEls:Set, breaks }
    const textEls = new Map(); // 文字を直接持つ要素 → { el, chars:[] }
    const range = document.createRange();
    const walker = document.createTreeWalker(slide, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const p = node.parentElement;
      if (!p || SKIP_TAGS.has(p.tagName) || !node.data.trim()) continue;
      if (hidden(p)) continue;
      const blk = blockOf(p);
      const fs = parseFloat(cs(p).fontSize) * elemScale(p);
      let B = blocks.get(blk);
      if (!B) { B = { el: blk, chars: [], breaks: 0 }; blocks.set(blk, B); }
      let T = textEls.get(p);
      if (!T) { T = { el: p, chars: [], block: blk, fs }; textEls.set(p, T); }
      const ws = cs(p).whiteSpace;
      const keepsNewline = /pre|break-spaces/.test(ws);
      const data = node.data;
      for (let i = 0; i < data.length; i++) {
        let n = 1;
        const code = data.charCodeAt(i);
        if (code >= 0xd800 && code <= 0xdbff && i + 1 < data.length) n = 2;
        const ch = data.slice(i, i + n);
        if (ch === '\n' && keepsNewline) B.breaks++;
        if (/\s/.test(ch)) { i += n - 1; continue; }
        range.setStart(node, i); range.setEnd(node, i + n);
        const rs = range.getClientRects();
        i += n - 1;
        let rr = null;
        for (const x of rs) { if (x.width > 0 || x.height > 0) { rr = x; break; } }
        if (!rr) continue;
        const b = toS(rr);
        const h = b.b - b.t;
        const cy = (b.t + b.b) / 2;
        const eh = Math.min(h, fs); // 字面の高さの近似（行の内容領域と em のうち小さい方）
        const c = { ...b, emT: cy - eh / 2, emB: cy + eh / 2, fs, ch, el: p };
        B.chars.push(c); T.chars.push(c);
      }
      // フォントの使用を記録
      const fam = cs(p).fontFamily.split(',')[0];
      const nf = normFam(fam);
      if (nf && !GENERIC.has(nf)) {
        const u = famUse.get(nf) || { name: fam.trim().replace(/^["']|["']$/g, ''), count: 0 };
        u.count++; famUse.set(nf, u);
      }
    }
    // <br> を数える
    for (const br of slide.querySelectorAll('br')) {
      if (inAside(br)) continue;
      const B = blocks.get(blockOf(br.parentElement));
      if (B) B.breaks++;
    }

    // 行を作る: 縦の重なりが小さい方の高さの半分を超える字を同じ行にまとめる
    function buildLines(chars) {
      const sorted = [...chars].sort((a, b) => a.t - b.t || a.l - b.l);
      const lines = [];
      for (const c of sorted) {
        const h = c.b - c.t;
        let L = null;
        for (const x of lines) {
          const ov = Math.min(x.b0, c.b) - Math.max(x.t0, c.t);
          if (ov > 0.5 * Math.min(x.b0 - x.t0, h)) { L = x; break; }
        }
        if (!L) { L = { t0: c.t, b0: c.b, l: c.l, r: c.r, t: c.t, b: c.b, emT: c.emT, emB: c.emB, chars: [] }; lines.push(L); }
        L.l = Math.min(L.l, c.l); L.r = Math.max(L.r, c.r); L.t = Math.min(L.t, c.t); L.b = Math.max(L.b, c.b);
        L.emT = Math.min(L.emT, c.emT); L.emB = Math.max(L.emB, c.emB);
        L.chars.push(c);
      }
      lines.sort((a, b) => a.t - b.t);
      for (const L of lines) { L.chars.sort((a, b) => a.l - b.l); L.text = L.chars.map((c) => c.ch).join(''); }
      return lines;
    }
    for (const B of blocks.values()) B.lines = buildLines(B.chars);
    const blockList = [...blocks.values()].filter((B) => B.lines.length);

    const gates = [];
    const undetermined = [];
    const infos = [];
    const addGate = (id, el, measured, threshold, unit, hint, extra) =>
      gates.push(Object.assign({ id, element: desc(el), measured: r1(measured), threshold: r1(threshold), unit, hint }, extra || {}));

    // ---- G1 文字の大きさ ----
    for (const T of textEls.values()) {
      if (!T.chars.length) continue;
      const foot = isFoot(T.el);
      const thr = foot ? th.footnotePt : th.bodyPt;
      const pt = T.fs * ptPerPx;
      if (pt < thr - 0.05) {
        const needCss = Math.ceil(thr * pxPerPt / elemScale(T.el));
        addGate('G1', T.el, pt, thr, 'pt', `font-size を ${needCss}px 以上に（今 ${r1(parseFloat(cs(T.el).fontSize))}px${foot ? '、脚注の役割' : ''}）`, { role: foot ? 'footnote' : 'body' });
      }
    }

    // ---- G2 コントラスト比 ----
    function cumulativeOpacity(el) {
      let o = 1;
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(cs(e).opacity);
      return o;
    }
    function canvasColor() {
      const h = parseColor(cs(document.documentElement).backgroundColor);
      if (h.a > 0) return blend(h, { r: 255, g: 255, b: 255, a: 1 });
      const b = parseColor(cs(document.body).backgroundColor);
      if (b.a > 0) return blend(b, { r: 255, g: 255, b: 255, a: 1 });
      return { r: 255, g: 255, b: 255, a: 1 };
    }
    // 背後の要素を上から順に見て、最初の不透明な背景まで重ねる
    function backgroundBehind(el, x, y) {
      let stack = null;
      const vx = sr.left + x * scale, vy = sr.top + y * scale;
      if (vx >= 0 && vy >= 0 && vx < innerWidth && vy < innerHeight) {
        const all = document.elementsFromPoint(vx, vy);
        const i = all.indexOf(el);
        if (i >= 0) stack = all.slice(i);
      }
      if (!stack) { stack = []; for (let e = el; e; e = e.parentElement) stack.push(e); }
      const layers = [];
      let opaque = false;
      for (const e of stack) {
        if (e !== el && (isMedia(e) || (e instanceof SVGElement && !(e instanceof SVGTextContentElement)))) {
          return { undetermined: '画像や図の上にある（' + e.tagName.toLowerCase() + '）' };
        }
        const s = cs(e);
        if (s.backgroundImage && s.backgroundImage !== 'none') return { undetermined: '背景が画像かグラデーション（' + cssPath(e, slide).split(' > ').pop() + '）' };
        if ((s.backdropFilter && s.backdropFilter !== 'none') || (s.mixBlendMode && s.mixBlendMode !== 'normal')) return { undetermined: '背景にフィルタか合成モードがある' };
        const c = parseColor(s.backgroundColor);
        if (c.a > 0) {
          const op = (e === document.documentElement || e === document.body) ? 1 : parseFloat(s.opacity);
          layers.push({ ...c, a: c.a * op });
          if (c.a * op >= 0.999) { opaque = true; break; }
        }
      }
      let bg = opaque ? null : canvasColor();
      for (let i = layers.length - 1; i >= 0; i--) bg = bg ? blend(layers[i], bg) : { ...layers[i], a: 1 };
      return { bg };
    }
    for (const T of textEls.values()) {
      if (!T.chars.length) continue;
      const s = cs(T.el);
      const fg0 = parseColor(s.color);
      const op = cumulativeOpacity(T.el);
      const pts = [T.chars[0], T.chars[T.chars.length - 1]];
      let worst = null, und = null, bgHex = null;
      for (const c of pts) {
        const res = backgroundBehind(T.el, (c.l + c.r) / 2, (c.t + c.b) / 2);
        if (res.undetermined) { und = res.undetermined; break; }
        const fg = blend({ ...fg0, a: fg0.a * op }, res.bg);
        const ratio = contrast(fg, res.bg);
        if (worst === null || ratio < worst) { worst = ratio; bgHex = hex(res.bg); }
      }
      const foot = isFoot(T.el);
      const pt = T.fs * ptPerPx;
      const bold = parseInt(s.fontWeight, 10) >= 700;
      const large = pt >= th.largePt - 0.05 || (bold && pt >= th.largeBoldPt - 0.05);
      const thr = (large || foot) ? th.contrastLarge : th.contrastBody;
      if (und) { undetermined.push({ id: 'G2', element: desc(T.el), reason: und, threshold: thr, hint: '画像を見て背景と文字の差を確認する' }); continue; }
      T.contrast = worst;
      if (worst < thr - 0.005) {
        addGate('G2', T.el, worst, thr, ':1', `文字色 ${hex(fg0)} を背景 ${bgHex} からもっと離す（${large || foot ? '大きい文字・脚注' : '本文'}の基準）`, { color: hex(fg0), background: bgHex });
        gates[gates.length - 1].measured = r2(worst);
      }
    }

    // ---- 描かれるもの（背景・枠・影・画像）と文字の行 ----
    const painted = [];
    for (const el of slide.querySelectorAll('*')) {
      if (SKIP_TAGS.has(el.tagName) || hidden(el)) continue;
      if (el instanceof SVGElement && !isSvgRoot(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 0.5 || r.height < 0.5) continue;
      const s = cs(el);
      let p = isMedia(el);
      if (!p && parseColor(s.backgroundColor).a > 0) p = true;
      if (!p && s.backgroundImage !== 'none') p = true;
      if (!p && s.boxShadow !== 'none') p = true;
      if (!p) {
        for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
          if (parseFloat(s['border' + side + 'Width']) > 0 && s['border' + side + 'Style'] !== 'none' && s['border' + side + 'Style'] !== 'hidden' && parseColor(s['border' + side + 'Color']).a > 0) { p = true; break; }
        }
      }
      if (p) painted.push({ el, box: toS(r) });
    }
    // 文字の行は字面の高さ（emT/emB）で数える
    const textItems = [];
    for (const B of blockList) for (const L of B.lines) textItems.push({ el: B.el, box: { l: L.l, t: L.emT, r: L.r, b: L.emB } });

    // 祖先がすでに報告されている要素は省く
    function outermost(cands) {
      const els = new Set(cands.map((c) => c.el));
      return cands.filter((c) => { for (let e = c.el.parentElement; e && e !== slide; e = e.parentElement) if (els.has(e)) return false; return true; });
    }
    function mergeByEl(cands) {
      const m = new Map();
      for (const c of cands) {
        const o = m.get(c.el);
        if (!o || c.over > o.over) m.set(c.el, c);
      }
      return [...m.values()];
    }
    const sideJa = { l: '左', t: '上', r: '右', b: '下' };

    // ---- G3 スライドからのはみ出し ----
    {
      const tol = th.overflowPt * pxPerPt;
      const cands = [];
      for (const it of [...painted, ...textItems]) {
        const o = { l: -it.box.l, t: -it.box.t, r: it.box.r - W, b: it.box.b - H };
        const sides = Object.keys(o).filter((k) => o[k] > tol);
        if (sides.length) cands.push({ el: it.el, over: Math.max(...sides.map((k) => o[k])), sides, o });
      }
      for (const c of outermost(mergeByEl(cands))) {
        const det = c.sides.map((k) => `${sideJa[k]}へ ${Math.round(c.o[k])}px`).join('、');
        addGate('G3', c.el, c.over * ptPerPx, th.overflowPt, 'pt', `幅か位置を詰めてスライドに収める（${det} 出ている）`);
      }
    }

    // ---- G4 テキストの枠からのあふれ ----
    {
      const tol = th.overflowPt * pxPerPt;
      const padBox = (el) => {
        const s = cs(el); const b = toS(el.getBoundingClientRect());
        return { l: b.l + parseFloat(s.borderLeftWidth), t: b.t + parseFloat(s.borderTopWidth), r: b.r - parseFloat(s.borderRightWidth), b: b.b - parseFloat(s.borderBottomWidth) };
      };
      const exceed = (lines, box, axes) => {
        let best = { over: 0 };
        for (const L of lines) {
          const o = {};
          if (axes.x) { o.l = box.l - L.l; o.r = L.r - box.r; }
          if (axes.y) { o.t = box.t - L.emT; o.b = L.emB - box.b; }
          for (const k in o) if (o[k] > best.over) best = { over: o[k], side: k };
        }
        return best;
      };
      for (const B of blockList) {
        if (B.el === slide || B.el instanceof SVGElement) continue;
        const own = exceed(B.lines, padBox(B.el), { x: true, y: true });
        let clip = { over: 0 }, clipEl = null;
        for (let a = B.el.parentElement; a && a !== slide && slide.contains(a); a = a.parentElement) {
          const s = cs(a);
          const ax = s.overflowX !== 'visible', ay = s.overflowY !== 'visible';
          if (!ax && !ay) continue;
          const e = exceed(B.lines, padBox(a), { x: ax, y: ay });
          if (e.over > clip.over) { clip = e; clipEl = a; }
        }
        const selfClips = cs(B.el).overflowX !== 'visible' || cs(B.el).overflowY !== 'visible';
        if (own.over > tol || clip.over > tol) {
          const useClip = clip.over > tol && clip.over >= own.over;
          const e = useClip ? clip : own;
          const where = useClip ? `祖先 ${cssPath(clipEl, slide).split(' > ').pop()} で切れる` : (selfClips ? '自分の枠で切れる' : '自分の枠から出る');
          const fix = (e.side === 'l' || e.side === 'r') ? '幅を広げるか文字を減らす／折り返しを許す' : '高さを広げるか行を減らす';
          addGate('G4', B.el, e.over * ptPerPx, th.overflowPt, 'pt', `${fix}（${sideJa[e.side]}へ ${Math.round(e.over)}px、${where}）`);
        }
      }
    }

    // ---- G5 文字どうしの重なり ----
    {
      const tol = th.overlapPt * pxPerPt;
      for (let i = 0; i < blockList.length; i++) {
        for (let j = i + 1; j < blockList.length; j++) {
          const A = blockList[i], Bk = blockList[j];
          if (A.el.contains(Bk.el) || Bk.el.contains(A.el)) continue;
          let best = 0;
          for (const la of A.lines) for (const lb of Bk.lines) {
            const ix = Math.min(la.r, lb.r) - Math.max(la.l, lb.l);
            const iy = Math.min(la.emB, lb.emB) - Math.max(la.emT, lb.emT);
            if (ix > tol && iy > tol) best = Math.max(best, Math.min(ix, iy));
          }
          if (best > 0) {
            const other = desc(Bk.el);
            addGate('G5', A.el, best * ptPerPx, th.overlapPt, 'pt', `${other.path.split(' > ').pop()}「${other.text}」と重なる。位置か行間を離す`, { other });
          }
        }
      }
    }

    // ---- G6 見出しの行数 ----
    const headlines = [...slide.querySelectorAll(opt.headlineSelector)].filter((h) => !hidden(h));
    const headlineLines = [];
    for (const h of headlines) {
      const chars = [];
      for (const B of blockList) if (h.contains(B.el) || B.el === h) chars.push(...B.chars);
      const lines = buildLines(chars);
      headlineLines.push({ element: desc(h), lines: lines.length });
      if (lines.length > th.headlineMaxLines) {
        addGate('G6', h, lines.length, th.headlineMaxLines, '行', `見出しを短くするか文字を小さくして ${th.headlineMaxLines} 行以内に`);
      }
    }

    // ---- I1 余白 ----
    const contentItems = [...painted, ...textItems].filter((it) => it.el !== slide && !isFoot(it.el));
    {
      // 左右と上下は別の余白で見る（既定のテーマは見出しを上端近くに置くため、上下は狭い）
      const m = opt.marginIn * pxPerIn;
      const mv = opt.marginVIn * pxPerIn;
      const lineIn = { l: opt.marginIn, r: opt.marginIn, t: opt.marginVIn, b: opt.marginVIn };
      const tol = th.marginTolPt * pxPerPt;
      const cands = [];
      for (const it of contentItems) {
        const o = { l: m - it.box.l, t: mv - it.box.t, r: it.box.r - (W - m), b: it.box.b - (H - mv) };
        const sides = Object.keys(o).filter((k) => o[k] > tol);
        if (sides.length) cands.push({ el: it.el, over: Math.max(...sides.map((k) => o[k])), sides, o });
      }
      for (const c of outermost(mergeByEl(cands))) {
        infos.push({ id: 'I1', kind: 'margin', element: desc(c.el), measured: r1(c.over * ptPerPx), unit: 'pt',
          detail: c.sides.map((k) => `${sideJa[k]}の余白 ${lineIn[k]}in の線を ${Math.round(c.o[k])}px 越える`).join('、') });
      }
    }
    // 脚注の役割の位置（全体の比較は最後に行う）
    for (const el of slide.querySelectorAll(opt.footnoteSelector)) {
      if (hidden(el) || (el.parentElement && el.parentElement.closest(opt.footnoteSelector) && slide.contains(el.parentElement.closest(opt.footnoteSelector)))) continue;
      const key = footParts.find((p) => { try { return el.matches(p); } catch (e) { return false; } }) || opt.footnoteSelector;
      const b = toS(el.getBoundingClientRect());
      footnoteRecords.push({ slide: si + 1, id: slide.id || null, key, l: Math.round(b.l), t: Math.round(b.t), r: Math.round(b.r), b: Math.round(b.b) });
    }

    // 枠の幅が中身で決まるかを確認する: 高さ 0 の幅 100px の小片を 60 個（間で折り返せる）追加して、
    // 枠が広がれば中身で決まる枠。最小幅は 100px しか増えないので、flex の最小幅の規則では広がらない。
    // 確認したら小片はすぐ取り除く（ファイルは変えない）。
    function contentSized(el) {
      const before = el.getBoundingClientRect().width;
      const probe = document.createElement('span');
      probe.setAttribute('aria-hidden', 'true');
      probe.style.cssText = 'display:inline;white-space:normal;font-size:0;line-height:0';
      for (let i = 0; i < 60; i++) {
        const piece = document.createElement('span');
        piece.style.cssText = 'display:inline-block;width:100px;height:0;vertical-align:top';
        probe.appendChild(piece);
        probe.appendChild(document.createTextNode(' '));
      }
      el.appendChild(probe);
      const after = el.getBoundingClientRect().width;
      probe.remove();
      return after > before + 1;
    }

    // ---- I2 行の折り返し ----
    {
      const gapTol = th.rightGapPt * pxPerPt;
      const list = [];
      for (const B of blockList) {
        if (B.el instanceof SVGElement) continue;
        const s = cs(B.el);
        const expected = B.breaks + 1;
        const n = B.lines.length;
        const d = desc(B.el);
        list.push({ path: d.path, text: d.text, lines: n, expected });
        if (n !== expected) {
          infos.push({ id: 'I2', kind: 'wrap', element: d, measured: n, expected,
            detail: n > expected ? `${n} 行（<br> からは ${expected} 行のはず。意図しない折り返し）` : `${n} 行（<br> からは ${expected} 行のはず）` });
        }
        if (n >= 2) {
          const last = B.lines[n - 1];
          if (last.chars.length <= th.orphanChars) {
            infos.push({ id: 'I2', kind: 'orphan', element: d, measured: last.chars.length, detail: `最後の行が「${last.text}」の ${last.chars.length} 字だけ` });
          }
        }
        const align = s.textAlign;
        const rtl = s.direction === 'rtl';
        const rightAligned = align === 'right' || (align === 'end' && !rtl) || (align === 'start' && rtl);
        if (!rightAligned) {
          const b = toS(B.el.getBoundingClientRect());
          const inner = b.r - parseFloat(s.borderRightWidth) - parseFloat(s.paddingRight);
          const tightLines = B.lines.map((L, k) => ({ L, k, gap: inner - L.r })).filter((x) => x.gap < gapTol);
          // 幅が中身で決まる枠（flex の子、inline-block、幅の無い絶対配置など）は、右端が行と揃うのが普通なので数えない
          if (tightLines.length && !contentSized(B.el)) tightLines.forEach(({ L, k, gap }) => {
            {
              infos.push({ id: 'I2', kind: 'tight', element: d, line: k + 1, measured: r1(gap * ptPerPx), unit: 'pt',
                detail: `${k + 1} 行目の右端から枠の内側の右端まで ${Math.round(gap)}px（${r1(gap * ptPerPx)}pt）` });
            }
          });
        }
      }
      infos.push({ id: 'I2', kind: 'lines', data: list, headlines: headlineLines });
    }

    // ---- I3 内容の重心 ----
    let bbox = null;
    for (const it of contentItems) {
      const b = it.box;
      bbox = bbox ? { l: Math.min(bbox.l, b.l), t: Math.min(bbox.t, b.t), r: Math.max(bbox.r, b.r), b: Math.max(bbox.b, b.b) } : { ...b };
    }
    if (bbox) {
      infos.push({ id: 'I3', kind: 'balance', bbox: rnd(bbox),
        gaps: { top: Math.round(bbox.t), left: Math.round(bbox.l), right: Math.round(W - bbox.r), bottom: Math.round(H - bbox.b) },
        centerOffset: { x: Math.round((bbox.l + bbox.r) / 2 - W / 2), y: Math.round((bbox.t + bbox.b) / 2 - H / 2) },
        detail: `内容の外接矩形 ${Math.round(bbox.l)},${Math.round(bbox.t)}–${Math.round(bbox.r)},${Math.round(bbox.b)} px。空き 上 ${Math.round(bbox.t)} / 左 ${Math.round(bbox.l)} / 右 ${Math.round(W - bbox.r)} / 下 ${Math.round(H - bbox.b)} px` });
    }

    // ---- I4 色 ----
    const colors = {};
    for (const T of textEls.values()) {
      if (!T.chars.length) continue;
      const k = hex(parseColor(cs(T.el).color));
      colors[k] = (colors[k] || 0) + 1;
    }
    infos.push({ id: 'I4', kind: 'colors', colors, count: Object.keys(colors).length,
      detail: `文字色 ${Object.keys(colors).length} 色: ` + Object.entries(colors).map(([c, n]) => `${c}×${n}`).join(' ') });

    // ---- I5 構成の分類 ----
    // 見出しと脚注の役割を除いた本体で、次の順に決める。
    //   figure  画像や svg がスライドの 15% 以上
    //   diagram 枠で囲んだ枠が 2 つ以上あり、矢印か線でつながっている
    //   rows    表、横に並んだ組が 3 行以上、または区切りや li で並んだ項目が 3 つ以上
    //   code    本体の文字の 40% 以上が等幅
    //   mixed   どれにも決めきれない（枠の枠だけ、小さめの画像、等幅が 25〜40%）
    //   text    文字だけ
    // 枠線や区切り線が 1〜3 辺だけの要素は枠と数えない。
    const ARROW_RE = /^[→←↑↓↔↕⇒⇐⇑⇓⇔➔➜➝➞➡⬅⬆⬇▶▷►▼▽◀◁◄▲△⟶⟵⟹]+$/u;
    const MONO_RE = /monospace|\bmono\b|consolas|courier|menlo|monaco|\bcode\b|ms gothic|等幅/i;
    const inHeadline = (el) => headlines.some((h) => h.contains(el));
    const bodyBlocks = blockList.filter((B) => !isFoot(B.el) && !inHeadline(B.el));
    const slideBg = hex(parseColor(cs(slide).backgroundColor));
    const area = (b) => Math.max(0, b.r - b.l) * Math.max(0, b.b - b.t);
    const bodyPainted = painted.filter((p) => p.el !== slide && !isFoot(p.el) && !inHeadline(p.el));
    // 画像
    const mediaItems = bodyPainted.filter((p) => isMedia(p.el));
    const mediaPct = mediaItems.reduce((n, p) => n + area(p.box), 0) / (W * H) * 100;
    // 枠: 4 辺とも枠があるか、地と違う背景か、影がある要素で、本体の文字を含むもの
    const fullBorder = (s) => ['Top', 'Right', 'Bottom', 'Left'].every((side) =>
      parseFloat(s['border' + side + 'Width']) > 0 && !['none', 'hidden'].includes(s['border' + side + 'Style']) && parseColor(s['border' + side + 'Color']).a > 0);
    const boxEls = bodyPainted.filter((p) => {
      if (isMedia(p.el)) return false;
      if (!bodyBlocks.some((B) => p.el.contains(B.el))) return false;
      const s = cs(p.el);
      const bg = parseColor(s.backgroundColor);
      return fullBorder(s) || s.boxShadow !== 'none' || (bg.a > 0.1 && hex(bg) !== slideBg);
    });
    // つなぎ: 矢印だけの文字のまとまり、小さな svg、細長い線の要素
    let connectors = 0;
    for (const B of bodyBlocks) { const t = B.chars.map((c) => c.ch).join(''); if (t.length <= 3 && ARROW_RE.test(t)) connectors++; }
    for (const p of mediaItems) {
      if (area(p.box) < W * H * 0.02 && p.el instanceof SVGSVGElement && p.el.querySelector('line, path, polygon, polyline')) connectors++;
    }
    for (const p of bodyPainted) {
      if (isMedia(p.el) || p.el.tagName === 'HR' || bodyBlocks.some((B) => p.el.contains(B.el))) continue;
      const w = p.box.r - p.box.l, h = p.box.b - p.box.t;
      if (Math.min(w, h) <= 8 && Math.max(w, h) >= 30) connectors++;
    }
    // 表と行の並び
    const hasTable = [...slide.querySelectorAll('table')].some((t) => !hidden(t));
    let rowsKind = hasTable ? 'table' : null;
    if (!rowsKind) {
      const paintedSet = new Set(painted.map((p) => p.el));
      const blocksIn = (k) => bodyBlocks.filter((B) => k.contains(B.el));
      for (const el of slide.querySelectorAll('*')) {
        if (hidden(el) || isFoot(el) || inHeadline(el)) continue;
        const kids = [...el.children].filter((k) => !hidden(k) && k.tagName !== 'ASIDE');
        if (kids.length < 3) continue;
        const sig = (k) => k.tagName + '.' + (typeof k.className === 'string' ? k.className : '');
        const groups = {};
        for (const k of kids) (groups[sig(k)] = groups[sig(k)] || []).push(k);
        for (const g of Object.values(groups)) {
          if (g.length < 3) continue;
          // 各子の中で、文字のまとまりが 2 つ以上横に並んでいるか
          const horiz = g.filter((k) => {
            const bs = blocksIn(k).filter((B) => B.el !== k).map((B) => toS(B.el.getBoundingClientRect()));
            for (let a = 0; a < bs.length; a++) for (let b = a + 1; b < bs.length; b++) {
              const vOv = Math.min(bs[a].b, bs[b].b) - Math.max(bs[a].t, bs[b].t);
              if (vOv > 0 && (bs[a].r <= bs[b].l + 1 || bs[b].r <= bs[a].l + 1)) return true;
            }
            return false;
          });
          if (horiz.length >= 3) { rowsKind = 'grid'; break; }
          // 縦に積んだ項目: li、区切りのある項目、または中に文字のまとまりを 2 つ以上持つ項目
          const rects = g.map((k) => toS(k.getBoundingClientRect()));
          const stacked = rects.every((r, i) => i === 0 || r.t >= rects[i - 1].b - 1);
          const itemLike = g.filter((k) => blocksIn(k).length && (k.tagName === 'LI' || paintedSet.has(k) || blocksIn(k).length >= 2));
          if (stacked && itemLike.length >= 3) { rowsKind = 'list'; break; }
        }
        if (rowsKind) break;
      }
    }
    // 等幅の文字の割合
    let monoChars = 0, bodyChars = 0;
    for (const B of bodyBlocks) for (const c of B.chars) { bodyChars++; if (MONO_RE.test(cs(c.el).fontFamily)) monoChars++; }
    const monoPct = bodyChars ? monoChars / bodyChars * 100 : 0;

    let layout, reason;
    if (mediaPct >= 15) { layout = 'figure'; reason = `画像や svg がスライドの ${Math.round(mediaPct)}%`; }
    else if (boxEls.length >= 2 && connectors >= 1) { layout = 'diagram'; reason = `枠 ${boxEls.length} 個とつなぎ ${connectors} 個`; }
    else if (rowsKind) { layout = 'rows'; reason = { table: '表がある', grid: '横に並んだ組が 3 行以上', list: '項目が 3 つ以上縦に並ぶ' }[rowsKind]; }
    else if (monoPct >= 40) { layout = 'code'; reason = `本体の文字の ${Math.round(monoPct)}% が等幅`; }
    else if (boxEls.length >= 2 || mediaPct >= 5 || monoPct >= 25) {
      layout = 'mixed';
      reason = [boxEls.length >= 2 ? `つなぎの無い枠 ${boxEls.length} 個` : '', mediaPct >= 5 ? `画像 ${Math.round(mediaPct)}%` : '', monoPct >= 25 ? `等幅 ${Math.round(monoPct)}%` : ''].filter(Boolean).join('、');
    } else { layout = 'text'; reason = '文字だけ'; }
    infos.push({ id: 'I5', kind: 'layout', layout, reason,
      features: { mediaPct: r1(mediaPct), boxes: boxEls.length, connectors, rows: rowsKind, monoPct: r1(monoPct) } });

    results.push({ index: si + 1, id: slide.id || null, canvas: { width: W, height: H, scale: r2(scale) }, gates, undetermined, infos });
  }

  // ---- 全体の参考情報 ----
  const deckInfos = [];
  {
    const byKey = {};
    for (const f of footnoteRecords) (byKey[f.key] = byKey[f.key] || []).push(f);
    for (const [key, list] of Object.entries(byKey)) {
      if (list.length < 2) continue;
      const ts = list.map((f) => f.t), ls = list.map((f) => f.l);
      const dt = Math.max(...ts) - Math.min(...ts), dl = Math.max(...ls) - Math.min(...ls);
      deckInfos.push({ id: 'I1', kind: 'footnote-position', key, consistent: dt <= 1 && dl <= 1, positions: list,
        detail: dt <= 1 && dl <= 1 ? `${key} の位置は全 ${list.length} 枚で同じ（top ${ts[0]}px, left ${ls[0]}px）`
          : `${key} の位置がスライドごとに違う（top の差 ${dt}px、left の差 ${dl}px）` });
    }
    const all = {};
    for (const s of results) for (const i of s.infos) if (i.id === 'I4') for (const [c, n] of Object.entries(i.colors)) all[c] = (all[c] || 0) + n;
    deckInfos.push({ id: 'I4', kind: 'colors', colors: all, count: Object.keys(all).length,
      detail: `全体の文字色 ${Object.keys(all).length} 色: ` + Object.entries(all).sort((a, b) => b[1] - a[1]).map(([c, n]) => `${c}×${n}`).join(' ') });
    const seq = results.map((s) => s.infos.find((i) => i.id === 'I5').layout);
    const runs = [];
    for (let i = 0; i < seq.length; i++) {
      if (runs.length && runs[runs.length - 1].layout === seq[i]) runs[runs.length - 1].count++;
      else runs.push({ layout: seq[i], from: i + 1, count: 1 });
    }
    const counts = {};
    for (const l of seq) counts[l] = (counts[l] || 0) + 1;
    const longest = runs.reduce((a, b) => (b.count > a.count ? b : a), runs[0]);
    deckInfos.push({ id: 'I5', kind: 'layout-runs', sequence: seq, runs, counts, longestRun: longest,
      detail: `構成の並び: ${seq.join(' → ')}。最長の連続は ${longest.layout} が ${longest.from} 枚目から ${longest.count} 枚` });
  }

  // ---- フォントの警告 ----
  const fonts = [];
  const faces = [...document.fonts];
  for (const [nf, u] of famUse) {
    const mine = faces.filter((f) => normFam(f.family) === nf);
    let status;
    if (mine.length) {
      const loaded = mine.some((f) => f.status === 'loaded');
      const errored = mine.filter((f) => f.status === 'error').length;
      status = loaded ? (errored ? 'partial' : 'loaded') : (errored ? 'error' : 'not-loaded');
    } else {
      status = localFontAvailable(u.name) ? 'local' : 'missing';
    }
    fonts.push({ family: u.name, status, textElements: u.count });
    if (status === 'error' || status === 'not-loaded' || status === 'missing') {
      warnings.push({ id: 'W-font', family: u.name, status,
        detail: `フォント「${u.name}」が読み込めず代替フォントで測った（幅と行数が発表当日と違う可能性がある）。ネットワークかフォントの指定を確認して測り直す` });
    } else if (status === 'partial') {
      warnings.push({ id: 'W-font', family: u.name, status,
        detail: `フォント「${u.name}」の一部のファイルが読み込めなかった。代替フォントで描かれた文字がある可能性がある` });
    }
  }

  return { tool: 'inpage_measure', version: VERSION, thresholds: th, options: { slideSelector: opt.slideSelector, footnoteSelector: opt.footnoteSelector, headlineSelector: opt.headlineSelector, marginIn: opt.marginIn, marginVIn: opt.marginVIn }, fonts, warnings, slides: results, deck: { infos: deckInfos } };
})(/*OPTIONS*/{});
