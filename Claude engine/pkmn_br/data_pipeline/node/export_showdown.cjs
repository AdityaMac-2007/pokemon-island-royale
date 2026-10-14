'use strict';
// Usage: node export_showdown.cjs <showdown_checkout_dir> <out_dir>
// Transpiles data/<file>.ts with sucrase (types stripped, nothing else changed), evaluates the module in an
// isolated vm context with a `require` that THROWS (data files must be self-contained), and writes
// deterministic JSON (sorted keys). Function values are dropped, but each top-level entry that contained any
// gets a `_functions` array listing the dotted paths, so scripted behaviour stays detectable downstream.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

let sucrase, sucraseVersion = 'unknown';
try {
  sucrase = require('sucrase');
  try { sucraseVersion = require('sucrase/package.json').version; }
  catch (_) { sucraseVersion = JSON.parse(fs.readFileSync(path.join(path.dirname(require.resolve('sucrase')), '..', 'package.json'), 'utf8')).version; }
} catch (e) {
  console.error('ERROR: cannot load "sucrase". Run `npm install` in data_pipeline/node (needs network once), or set NODE_PATH.');
  process.exit(2);
}

const FILES = [
  ['pokedex', 'Pokedex'], ['formats-data', 'FormatsData'], ['moves', 'Moves'], ['abilities', 'Abilities'],
  ['items', 'Items'], ['learnsets', 'Learnsets'], ['typechart', 'TypeChart'], ['natures', 'Natures'],
];

const [checkout, outDir] = process.argv.slice(2);
if (!checkout || !outDir) { console.error('usage: export_showdown.cjs <checkout> <out_dir>'); process.exit(2); }
fs.mkdirSync(outDir, { recursive: true });

function fnPaths(v, prefix, acc) {
  if (typeof v === 'function') { acc.push(prefix); return; }
  if (v && typeof v === 'object') {
    for (const k of Object.keys(v)) fnPaths(v[k], prefix ? prefix + '.' + k : k, acc);
  }
}
function plain(v, st) {
  if (typeof v === 'function') { st.dropped++; return undefined; }
  if (typeof v === 'number' && !Number.isFinite(v)) throw new Error('non-finite number in data');
  if (v === null || typeof v !== 'object') return v;
  if (Array.isArray(v)) return v.map(x => { const r = plain(x, st); return r === undefined ? null : r; });
  const o = {};
  for (const k of Object.keys(v).sort()) { const r = plain(v[k], st); if (r !== undefined) o[k] = r; }
  return o;
}
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const report = { node_version: process.version, sucrase_version: sucraseVersion, files: {} };

for (const [file, exportName] of FILES) {
  const src = path.join(checkout, 'data', file + '.ts');
  if (!fs.existsSync(src)) { console.error('ERROR: missing ' + src); process.exit(3); }
  const text = fs.readFileSync(src);
  const code = sucrase.transform(text.toString('utf8'), { transforms: ['typescript', 'imports'], filePath: src }).code;
  const sandbox = { exports: {}, module: { exports: {} },
    require: (m) => { throw new Error(`unexpected require(${JSON.stringify(m)}) in data/${file}.ts: data files must be self-contained`); } };
  sandbox.module.exports = sandbox.exports;
  vm.runInNewContext(code, sandbox, { filename: src, timeout: 120000 });
  const table = sandbox.exports[exportName];
  if (!table || typeof table !== 'object') {
    console.error(`ERROR: data/${file}.ts does not export "${exportName}" (exports: ${Object.keys(sandbox.exports).join(', ')})`);
    process.exit(4);
  }
  const st = { dropped: 0 };
  const out = {};
  for (const k of Object.keys(table).sort()) {
    const entry = table[k];
    const paths = []; fnPaths(entry, '', paths);
    const p = plain(entry, st);
    if (paths.length && p && typeof p === 'object' && !Array.isArray(p)) p._functions = paths.sort();
    out[k] = p;
  }
  const body = JSON.stringify(out, null, 1) + '\n';
  fs.writeFileSync(path.join(outDir, file + '.json'), body);
  report.files[file] = { source_path: 'data/' + file + '.ts', source_sha256: sha(text), entries: Object.keys(out).length,
    function_values_dropped: st.dropped, output_sha256: sha(Buffer.from(body)) };
}
fs.writeFileSync(path.join(outDir, 'export_report.json'), JSON.stringify(report, null, 1) + '\n');
console.log('exported ' + FILES.length + ' files to ' + outDir);
