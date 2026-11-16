#!/usr/bin/env node
/*
 * check_html.mjs — HTML で作成したスライドの可読性の基準を実測して検査する。
 *
 * 実測と判定は inpage_measure.js がページの中で行う。このファイルはページを組み立て、
 * ブラウザで開き、inpage_measure.js を実行して、結果を要約・保存するだけ。
 *
 * 使い方:
 *   node check_html.mjs --deck <dir>        <dir>/project/deck.json の order の順に検査
 *   node check_html.mjs --html <file>       任意の HTML 1 枚（スライドは --slide-selector）
 *   node check_html.mjs --selftest          合成した HTML で G1〜G6 の検出を確認する
 * 主な引数:
 *   --size 1920x1080          スライドのサイズ px（高さを 540 pt とみなす）
 *   --shots <dir>             各スライドを PNG で保存（NN-<id>.png）
 *   --pdf <file>              全スライドを 1 枚 1 ページの PDF に（13.333 x 7.5 in の比）
 *   --json <file>             全結果を JSON で保存
 *   --verbose                 参考情報を全部出す
 *   --slide-selector <css>    既定 section
 *   --footnote-selector <css> 既定 .src, .pg, [data-role="footnote"], [data-role="page"]
 *   --headline-selector <css> 既定 h1, h2
 *   --margin-in <in>          左右の余白（既定 0.6）
 *   --margin-v-in <in>        上下の余白（既定 0.4）
 *   --body-pt --footnote-pt --contrast-body --contrast-large --large-pt --large-bold-pt
 *   --overflow-pt --overlap-pt --headline-max-lines --right-gap-pt --orphan-chars
 *                             閾値の上書き（単位は pt。高さ 7.5 in のスライドでの値）
 *   --no-reset                --deck のとき見出しと段落の既定の余白を削除しない
 *   --browser <path>          使うブラウザの実行ファイル
 *   --keep-preview            組み立てた一時 HTML を削除せずに残す
 * 終了コード: 0 = 違反 0 件、1 = 違反あり、2 = 実行できなかった
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const TOOL = 'check_html';
const VERSION = '1.0.0';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FOOTNOTE_DEFAULT = '.src, .pg, [data-role="footnote"], [data-role="page"]';

const THRESHOLD_FLAGS = {
  'body-pt': 'bodyPt', 'footnote-pt': 'footnotePt', 'contrast-body': 'contrastBody',
  'contrast-large': 'contrastLarge', 'large-pt': 'largePt', 'large-bold-pt': 'largeBoldPt',
  'overflow-pt': 'overflowPt', 'overlap-pt': 'overlapPt', 'headline-max-lines': 'headlineMaxLines',
  'right-gap-pt': 'rightGapPt', 'orphan-chars': 'orphanChars', 'margin-tol-pt': 'marginTolPt',
};
const BOOL_FLAGS = new Set(['selftest', 'verbose', 'keep-preview', 'no-reset', 'help']);

function fail(msg) {
  process.stderr.write(msg.trim() + '\n');
  process.exit(2);
}

function parseArgs(argv) {
  const a = { thresholds: {} };
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (!t.startsWith('--')) fail(`引数「${t}」を解釈できない。--help で使い方を確認する`);
    const [k, inline] = t.slice(2).split(/=(.*)/s);
    if (BOOL_FLAGS.has(k)) { a[k] = true; continue; }
    const v = inline !== undefined ? inline : argv[++i];
    if (v === undefined) fail(`--${k} に値が要る`);
    if (THRESHOLD_FLAGS[k]) {
      const n = Number(v);
      if (!isFinite(n)) fail(`--${k} は数で指定する`);
      a.thresholds[THRESHOLD_FLAGS[k]] = n;
    } else a[k] = v;
  }
  return a;
}

const args = parseArgs(process.argv.slice(2));
if (args.help || (!args.deck && !args.html && !args.selftest)) {
  const head = fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0];
  process.stdout.write(head.replace(/^#!.*\n\/\*\n?/, '').replace(/^ \* ?/gm, '') + '\n');
  process.exit(args.help ? 0 : 2);
}

const size = /^(\d+)x(\d+)$/.exec(args.size || '1920x1080');
if (!size) fail('--size は 1920x1080 の形で指定する');
const W = +size[1], H = +size[2];
const slideSelector = args['slide-selector'] || 'section';
const footnoteSelector = args['footnote-selector'] || FOOTNOTE_DEFAULT;
const headlineSelector = args['headline-selector'] || 'h1, h2';
const marginIn = args['margin-in'] !== undefined ? Number(args['margin-in']) : 0.6;
if (!isFinite(marginIn)) fail('--margin-in は数で指定する');
const marginVIn = args['margin-v-in'] !== undefined ? Number(args['margin-v-in']) : 0.4;
if (!isFinite(marginVIn)) fail('--margin-v-in は数で指定する');

// ---- Playwright とブラウザ ----
let chromium;
try {
  ({ chromium } = await import('playwright-core'));
} catch {
  fail(`playwright-core が見つからない。このスクリプトのフォルダで npm install を実行する（${HERE}）`);
}

function playwrightCacheDirs() {
  const dirs = [];
  if (process.env.PLAYWRIGHT_BROWSERS_PATH && process.env.PLAYWRIGHT_BROWSERS_PATH !== '0') dirs.push(process.env.PLAYWRIGHT_BROWSERS_PATH);
  const home = os.homedir();
  if (process.platform === 'win32') dirs.push(path.join(process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), 'ms-playwright'));
  else if (process.platform === 'darwin') dirs.push(path.join(home, 'Library', 'Caches', 'ms-playwright'));
  else dirs.push(path.join(process.env.XDG_CACHE_HOME || path.join(home, '.cache'), 'ms-playwright'));
  return dirs;
}
function playwrightCachedBrowsers() {
  const rel = {
    win32: [['chrome-win64', 'chrome.exe'], ['chrome-win', 'chrome.exe'], ['chrome-headless-shell-win64', 'chrome-headless-shell.exe']],
    darwin: [['chrome-mac-arm64', 'Chromium.app/Contents/MacOS/Chromium'], ['chrome-mac', 'Chromium.app/Contents/MacOS/Chromium'], ['chrome-mac-x64', 'Chromium.app/Contents/MacOS/Chromium'],
      ['chrome-headless-shell-mac-arm64', 'chrome-headless-shell'], ['chrome-headless-shell-mac-x64', 'chrome-headless-shell']],
    linux: [['chrome-linux64', 'chrome'], ['chrome-linux', 'chrome'], ['chrome-headless-shell-linux64', 'chrome-headless-shell']],
  }[process.platform] || [];
  const found = [];
  for (const d of playwrightCacheDirs()) {
    let entries = [];
    try { entries = fs.readdirSync(d); } catch { continue; }
    const cands = entries.filter((e) => /^chromium(_headless_shell)?-\d+$/.test(e))
      .sort((a, b) => Number(b.split('-').pop()) - Number(a.split('-').pop()) || (a.includes('headless') ? 1 : -1));
    for (const c of cands) for (const [sub, exe] of rel) {
      const p = path.join(d, c, sub, exe);
      if (fs.existsSync(p)) found.push(p);
    }
  }
  return found;
}

async function launchBrowser() {
  const tries = [];
  if (args.browser) tries.push({ label: `--browser ${args.browser}`, opts: { executablePath: args.browser } });
  tries.push({ label: 'chrome', opts: { channel: 'chrome' } });
  tries.push({ label: 'msedge', opts: { channel: 'msedge' } });
  for (const p of playwrightCachedBrowsers()) tries.push({ label: `Playwright の取得済み ${path.basename(path.dirname(path.dirname(p)))}`, opts: { executablePath: p } });
  for (const k of ['SLIDE_CHECK_BROWSER', 'CHROME_PATH']) if (process.env[k]) tries.push({ label: `環境変数 ${k}`, opts: { executablePath: process.env[k] } });
  for (const t of tries) {
    try {
      const b = await chromium.launch({ headless: true, ...t.opts });
      return { browser: b, label: t.label };
    } catch { /* 次を試す */ }
  }
  fail('Chrome か Edge が見つからない。どちらかを入れるか、Chromium 系ブラウザの実行ファイルを --browser <path> か環境変数 SLIDE_CHECK_BROWSER で指定する。');
}

// ---- ページの組み立て ----
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

function canvasCss(sel, { reset }) {
  return `
html, body { margin: 0; padding: 0; }
body { width: ${W}px; }
${reset ? 'h1, h2, h3, h4, h5, h6, p { margin: 0; }' : ''}
${sel} { position: relative !important; box-sizing: border-box !important; width: ${W}px !important; height: ${H}px !important;
  min-width: 0 !important; min-height: 0 !important; max-width: none !important; max-height: none !important;
  margin: 0 !important; overflow: hidden; transform: none !important; flex: none; }
${sel} aside { display: none !important; }
@media print {
  @page { margin: 0; }
  ${sel} { break-before: page; break-inside: avoid; }
}`;
}

function buildDeckPage(dir) {
  const proj = path.join(dir, 'project');
  const deckJsonPath = path.join(proj, 'deck.json');
  if (!fs.existsSync(deckJsonPath)) fail(`${deckJsonPath} が無い。--deck には project/deck.json を含むフォルダを渡す`);
  let deck;
  try { deck = JSON.parse(fs.readFileSync(deckJsonPath, 'utf8')); } catch (e) { fail(`deck.json を読めない: ${e.message}`); }
  const order = Array.isArray(deck.order) ? deck.order : [];
  if (!order.length) fail('deck.json の order が空か、無い');
  const warnings = [];
  const parts = [];
  for (const id of order) {
    const f = path.join(proj, 'slides', `${id}.html`);
    if (!fs.existsSync(f)) { warnings.push({ id: 'W-missing', detail: `slides/${id}.html が無い（order にはある）` }); continue; }
    parts.push(fs.readFileSync(f, 'utf8'));
  }
  const faces = deck.faces && typeof deck.faces === 'object' ? Object.values(deck.faces) : [];
  const links = faces.filter((f) => f && f.href).map((f) => `<link rel="stylesheet" href="${esc(f.href)}">`).join('\n');
  const css = fs.existsSync(path.join(proj, 'deck.css')) ? '<link rel="stylesheet" href="deck.css">' : '';
  if (!css) warnings.push({ id: 'W-missing', detail: 'project/deck.css が無い' });
  const html = `<!doctype html>
<html lang="${esc(deck.lang || 'ja')}"><head><meta charset="utf-8">
<base href="${pathToFileURL(proj + path.sep).href}">
${links}
${css}
<style>${canvasCss(slideSelector, { reset: !args['no-reset'] })}</style>
</head><body>
${parts.join('\n')}
</body></html>`;
  return { html, warnings, families: faces.map((f) => f.family).filter(Boolean) };
}

function buildHtmlPage(file) {
  if (!fs.existsSync(file)) fail(`${file} が無い`);
  let src = fs.readFileSync(file, 'utf8');
  const baseTag = `<base href="${pathToFileURL(path.dirname(path.resolve(file)) + path.sep).href}">`;
  const style = `<style>${canvasCss(slideSelector, { reset: false })}</style>`;
  if (/<head[^>]*>/i.test(src)) src = src.replace(/<head[^>]*>/i, (m) => `${m}\n${baseTag}`);
  else src = baseTag + src;
  if (/<\/head>/i.test(src)) src = src.replace(/<\/head>/i, `${style}\n</head>`);
  else if (/<body[^>]*>/i.test(src)) src = src.replace(/<body[^>]*>/i, (m) => `${style}\n${m}`);
  else src = style + src;
  return { html: src, warnings: [], families: [] };
}

// ---- 実測 ----
const measureSrc = fs.readFileSync(path.join(HERE, 'inpage_measure.js'), 'utf8');
function measureExpr(options) {
  const marker = '/*OPTIONS*/{}';
  const at = measureSrc.lastIndexOf(marker);
  if (at < 0) fail('inpage_measure.js の末尾に /*OPTIONS*/{} が無い');
  return measureSrc.slice(0, at) + JSON.stringify(options) + measureSrc.slice(at + marker.length);
}

async function runOnHtml(browser, html, { shots, pdf, keep, thresholds } = {}) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'slide-check-'));
  const file = path.join(tmp, 'preview.html');
  fs.writeFileSync(file, html);
  const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const extraWarnings = [];
  try {
    await page.goto(pathToFileURL(file).href, { waitUntil: 'load', timeout: 60000 });
    // display:none で隠されたスライドを表示する（1 枚ずつ見せる作りの HTML 向け）
    const unhidden = await page.evaluate((sel) => {
      let n = 0;
      for (const s of document.querySelectorAll(sel)) {
        if (getComputedStyle(s).display === 'none') { s.style.setProperty('display', 'block', 'important'); n++; }
        if (getComputedStyle(s).visibility === 'hidden') { s.style.setProperty('visibility', 'visible', 'important'); n++; }
      }
      return n;
    }, slideSelector);
    if (unhidden) extraWarnings.push({ id: 'W-hidden', detail: `非表示だったスライド ${unhidden} 枚を表示して測った` });
    const res = await page.evaluate(measureExpr({
      slideSelector, footnoteSelector, headlineSelector, width: W, height: H, marginIn, marginVIn, thresholds: thresholds || {},
    }));
    res.warnings = [...extraWarnings, ...(res.warnings || [])];
    const saved = [];
    if (shots && res.slides && res.slides.length) {
      fs.mkdirSync(shots, { recursive: true });
      const loc = page.locator(slideSelector);
      const all = await loc.count();
      // 入れ子のスライドは測っていないので、測った枚数と同じ順に撮る
      for (let i = 0, k = 0; i < all && k < res.slides.length; i++) {
        const nested = await loc.nth(i).evaluate((el, sel) => !!(el.parentElement && el.parentElement.closest(sel)), slideSelector);
        if (nested) continue;
        const s = res.slides[k++];
        const name = `${String(s.index).padStart(2, '0')}-${(s.id || 'slide').replace(/[^\w.-]+/g, '_')}.png`;
        const out = path.join(shots, name);
        await loc.nth(i).screenshot({ path: out, animations: 'disabled' });
        saved.push(out);
      }
    }
    if (pdf && res.slides && res.slides.length) {
      fs.mkdirSync(path.dirname(path.resolve(pdf)), { recursive: true });
      // 高さ 7.5 in のページに縮める（PDF の検査と同じ pt になるように）
      const heightIn = 7.5, widthIn = 7.5 * W / H;
      await page.pdf({ path: pdf, width: `${widthIn}in`, height: `${heightIn}in`, scale: (heightIn * 96) / H, printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } });
      const buf = fs.readFileSync(pdf, 'latin1');
      const pages = (buf.match(/\/Type\s*\/Page(?![s\w])/g) || []).length;
      saved.push(pdf);
      if (pages !== res.slides.length) res.warnings.push({ id: 'W-pdf', detail: `PDF のページ数 ${pages} がスライドの枚数 ${res.slides.length} と違う` });
      res.pdfPages = pages;
    }
    res.saved = saved;
    return res;
  } finally {
    await context.close();
    if (keep) process.stderr.write(`一時 HTML を残した: ${file}\n`);
    else fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---- 要約 ----
const GATE_NAMES = { G1: '文字の大きさ', G2: 'コントラスト比', G3: 'スライドからのはみ出し', G4: 'テキストの枠からのあふれ', G5: '文字どうしの重なり', G6: '見出しの行数' };
const elLabel = (e) => {
  const sel = e.tag + (e.id ? '#' + e.id : '') + (e.classes && e.classes.length ? '.' + e.classes.join('.') : '');
  return `${sel}「${e.text}」`;
};
function fmtGate(g) {
  const cmp = g.id === 'G6' ? `${g.measured} 行 > ${g.threshold} 行`
    : g.id === 'G2' ? `${g.measured}:1 < ${g.threshold}:1`
      : g.id === 'G1' ? `${g.measured} pt < ${g.threshold} pt`
        : `${g.measured} pt > ${g.threshold} pt`;
  return `  ${g.id} ${elLabel(g.element)} ${cmp} → ${g.hint}\n      場所: ${g.element.path}`;
}

function summarize(result, { verbose, label, elapsed }) {
  const L = [];
  const slides = result.slides || [];
  const gates = slides.flatMap((s) => s.gates);
  const und = slides.flatMap((s) => s.undetermined);
  const byId = {};
  for (const g of gates) byId[g.id] = (byId[g.id] || 0) + 1;
  const infoCount = slides.reduce((n, s) => n + s.infos.filter((i) => !['lines', 'balance', 'colors', 'layout'].includes(i.kind)).length, 0)
    + (result.deck ? result.deck.infos.filter((i) => i.kind === 'footnote-position' && !i.consistent).length : 0);
  L.push(`違反 ${gates.length} 件` + (gates.length ? `（${Object.entries(byId).map(([k, v]) => `${k} ${GATE_NAMES[k]} ${v}`).join('、')}）` : '')
    + `／判定できない ${und.length} 件／警告 ${result.warnings.length} 件／参考 ${infoCount} 件`);
  L.push(`スライド ${W}x${H} px（1 pt = ${(H / 540).toFixed(2).replace(/\.?0+$/, '')} px）、${slides.length} 枚、ブラウザ ${label}` + (elapsed ? `、${elapsed.toFixed(1)} 秒` : ''));
  if (result.fonts && result.fonts.length) L.push('フォント: ' + result.fonts.map((f) => `${f.family}=${f.status}`).join('、'));
  for (const w of result.warnings) L.push(`警告: ${w.detail}`);
  L.push('');
  for (const s of slides) {
    const name = `[${s.index}] ${s.id || '(id なし)'}`;
    if (!s.gates.length) {
      L.push(`${name} 違反 0` + (s.undetermined.length ? `（判定できない ${s.undetermined.length}）` : ''));
    } else {
      L.push(`${name} 違反 ${s.gates.length}`);
      for (const g of s.gates) L.push(fmtGate(g));
    }
    for (const u of s.undetermined) L.push(`  G2? ${elLabel(u.element)} 判定できない: ${u.reason} → ${u.hint}`);
  }
  // 参考情報
  L.push('');
  L.push('参考情報' + (verbose ? '（全部）' : '（件数と重いもの。全部は --verbose）'));
  const count = (id, kind) => slides.reduce((n, s) => n + s.infos.filter((i) => i.id === id && (!kind || i.kind === kind)).length, 0);
  L.push(`  I1 余白（左右 ${marginIn} in、上下 ${marginVIn} in）の外にある内容 ${count('I1', 'margin')} 件`);
  const foot = result.deck.infos.filter((i) => i.kind === 'footnote-position');
  for (const f of foot) L.push(`  I1 ${f.detail}`);
  L.push(`  I2 意図しない折り返し ${count('I2', 'wrap')} 件、最後の行が ${result.thresholds.orphanChars} 字以下 ${count('I2', 'orphan')} 件、右端まで ${result.thresholds.rightGapPt} pt 未満の行 ${count('I2', 'tight')} 件`);
  for (const s of slides) {
    for (const i of s.infos) {
      const heavy = i.kind === 'wrap' || i.kind === 'orphan';
      const show = verbose ? !['lines', 'balance', 'colors', 'layout'].includes(i.kind) : heavy;
      if (show) L.push(`    [${s.index}] ${i.id} ${elLabel(i.element)} ${i.detail}`);
    }
    if (verbose) {
      const lines = s.infos.find((i) => i.kind === 'lines');
      if (lines) {
        L.push(`    [${s.index}] I2 行数: ` + lines.data.map((d) => `${d.path.split(' > ').pop()} ${d.lines}/${d.expected}`).join('、'));
        if (lines.headlines.length) L.push(`    [${s.index}] I2 見出しの行数: ` + lines.headlines.map((h) => `${h.element.tag} ${h.lines} 行`).join('、'));
      }
      const b = s.infos.find((i) => i.kind === 'balance');
      if (b) L.push(`    [${s.index}] I3 ${b.detail}、中心からのずれ x ${b.centerOffset.x} / y ${b.centerOffset.y} px`);
      const c = s.infos.find((i) => i.kind === 'colors');
      if (c) L.push(`    [${s.index}] I4 ${c.detail}`);
      const ly = s.infos.find((i) => i.kind === 'layout');
      if (ly) L.push(`    [${s.index}] I5 構成 ${ly.layout}（${ly.reason}）`);
    }
  }
  if (!verbose) {
    const bal = slides.map((s) => s.infos.find((i) => i.kind === 'balance')).filter(Boolean);
    if (bal.length) {
      const maxBottom = Math.max(...bal.map((b) => b.gaps.bottom));
      const s = slides.find((x) => { const b = x.infos.find((i) => i.kind === 'balance'); return b && b.gaps.bottom === maxBottom; });
      L.push(`  I3 内容の下の空きが最も大きいのは [${s.index}] ${s.id || ''} の ${maxBottom}px（各スライドの外接矩形は --verbose）`);
    }
  }
  const col = result.deck.infos.find((i) => i.id === 'I4');
  if (col) L.push(`  I4 ${col.detail}`);
  const runs = result.deck.infos.find((i) => i.id === 'I5');
  if (runs) L.push(`  I5 ${runs.detail}`);
  if (result.saved && result.saved.length) {
    L.push('');
    L.push('保存したファイル:');
    for (const f of result.saved) L.push(`  ${f}`);
  }
  L.push('');
  L.push('測れる条件の検査はここまで。見た目の良し悪しはこの検査では判定していないので、次に --shots の画像を 1 枚ずつ開き、配置のバランスや強調の効果を目視で確認する。');
  return L.join('\n');
}

// ---- 自己検査 ----
async function selftest(browser) {
  const base = `
<style>
section { padding: 100px; background: #ffffff; color: #111111; font-family: Arial, Helvetica, sans-serif; }
p { margin: 0 0 30px; font-size: 40px; line-height: 1.5; }
h2 { margin: 0 0 40px; font-size: 56px; line-height: 1.3; }
</style>`;
  const ok = '<h2>見出し</h2><p>本文の段落はここに置く</p>';
  const cases = {
    G1: { bad: '<h2>見出し</h2><p style="font-size:30px">小さすぎる文字</p>', good: ok },
    G2: { bad: '<h2>見出し</h2><p style="color:#888888">薄すぎる文字</p>', good: '<h2>見出し</h2><p style="color:#333333">十分に濃い文字</p>' },
    G3: { bad: ok + '<div style="position:absolute;left:1800px;top:600px;width:300px;height:100px;background:#cccccc"></div>',
      good: ok + '<div style="position:absolute;left:1500px;top:600px;width:300px;height:100px;background:#cccccc"></div>' },
    G4: { bad: '<h2>見出し</h2><p style="width:300px;white-space:nowrap">この一行は枠の幅より長い</p>',
      good: '<h2>見出し</h2><p style="width:900px;white-space:nowrap">この一行は枠の幅より短い</p>' },
    G5: { bad: '<p style="position:absolute;left:100px;top:300px">重なる文字その一</p><p style="position:absolute;left:130px;top:315px">重なる文字その二</p>',
      good: '<p style="position:absolute;left:100px;top:300px">離れた文字その一</p><p style="position:absolute;left:100px;top:400px">離れた文字その二</p>' },
    G6: { bad: '<h2 style="width:520px">見出しが長すぎて三行以上に折り返してしまう例</h2><p>本文</p>',
      good: '<h2>見出しは短く収める</h2><p>本文</p>' },
  };
  const body = Object.entries(cases).map(([id, c]) => `<section id="${id}-bad">${c.bad}</section>\n<section id="${id}-good">${c.good}</section>`).join('\n');
  const html = `<!doctype html><html lang="ja"><head><meta charset="utf-8">${base}<style>${canvasCss('section', { reset: false })}</style></head><body>${body}</body></html>`;
  const res = await runOnHtml(browser, html, {});
  const L = [];
  let allPass = true;
  for (const id of Object.keys(cases)) {
    const bad = res.slides.find((s) => s.id === `${id}-bad`);
    const good = res.slides.find((s) => s.id === `${id}-good`);
    const hit = bad.gates.filter((g) => g.id === id).length;
    const others = bad.gates.filter((g) => g.id !== id);
    const pass = hit === 1 && others.length === 0 && good.gates.length === 0;
    if (!pass) allPass = false;
    L.push(`${pass ? 'PASS' : 'FAIL'} ${id} ${GATE_NAMES[id]}: 違反のスライドで ${id} を ${hit} 件検出` + (others.length ? `、ほかの違反 ${others.map((g) => g.id).join(',')}` : '')
      + `／違反のないスライドで違反 ${good.gates.length} 件` + (pass ? '' : '\n' + [...bad.gates, ...good.gates].map(fmtGate).join('\n')));
  }
  L.push(allPass ? '自己検査: 全項目 PASS' : '自己検査: FAIL あり');
  return { text: L.join('\n'), pass: allPass };
}

// ---- 本体 ----
const t0 = Date.now();
const { browser, label } = await launchBrowser();
let exitCode = 2;
try {
  if (args.selftest) {
    const r = await selftest(browser);
    process.stdout.write(`自己検査（ブラウザ ${label}）\n` + r.text + '\n');
    exitCode = r.pass ? 0 : 1;
  } else {
    const built = args.deck ? buildDeckPage(path.resolve(args.deck)) : buildHtmlPage(path.resolve(args.html));
    const res = await runOnHtml(browser, built.html, {
      shots: args.shots && path.resolve(args.shots), pdf: args.pdf && path.resolve(args.pdf),
      keep: !!args['keep-preview'], thresholds: args.thresholds,
    });
    if (res.error || !res.slides || !res.slides.length) fail(`スライドを測れなかった: ${res.error || '0 枚'}。--slide-selector を確認する`);
    res.warnings = [...built.warnings, ...res.warnings];
    const elapsed = (Date.now() - t0) / 1000;
    const gates = res.slides.reduce((n, s) => n + s.gates.length, 0);
    const out = {
      tool: TOOL, version: VERSION,
      input: args.deck ? { kind: 'deck', path: path.resolve(args.deck) } : { kind: 'html', path: path.resolve(args.html), slideSelector },
      canvas: { width: W, height: H, unit: 'px', ptPerPx: 540 / H, pxPerIn: H / 7.5, heightIn: 7.5 },
      thresholds: { ...res.thresholds, marginIn, marginVIn, footnoteSelector, headlineSelector },
      summary: {
        gates, infos: res.slides.reduce((n, s) => n + s.infos.length, 0) + res.deck.infos.length,
        warnings: res.warnings.length, undetermined: res.slides.reduce((n, s) => n + s.undetermined.length, 0),
        byGate: res.slides.flatMap((s) => s.gates).reduce((m, g) => ((m[g.id] = (m[g.id] || 0) + 1), m), {}),
      },
      warnings: res.warnings, fonts: res.fonts, browser: label, elapsedSec: elapsed,
      slides: res.slides.map((s) => ({ index: s.index, id: s.id, gates: s.gates, undetermined: s.undetermined, infos: s.infos })),
      deck: res.deck, saved: res.saved,
    };
    if (args.json) {
      fs.mkdirSync(path.dirname(path.resolve(args.json)), { recursive: true });
      fs.writeFileSync(path.resolve(args.json), JSON.stringify(out, null, 2));
      res.saved.push(path.resolve(args.json));
    }
    process.stdout.write(summarize(res, { verbose: !!args.verbose, label, elapsed }) + '\n');
    exitCode = gates ? 1 : 0;
  }
} catch (e) {
  process.stderr.write(`実行できなかった: ${e && e.message ? e.message : e}\n`);
  exitCode = 2;
} finally {
  await browser.close();
}
process.exit(exitCode);
