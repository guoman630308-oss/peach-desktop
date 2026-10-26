import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSync } from 'oxc-parser';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const rules = JSON.parse(readFileSync(join(root, 'scripts/copy-rules.json'), 'utf8')).prose;
const marker = 'copy-lint-disable-line';

export function scanSource(path, source) {
  const parsed = parseSync(path, source);
  const lines = source.split('\n');
  const found = parsed.errors.map(error => ({ path, line: 1, severity: 'error', message: error.message }));
  const scan = (value, start) => {
    let line = source.slice(0, start).split('\n').length;
    for (const part of value.split('\n')) {
      if (!lines[line - 1]?.includes(marker)) {
        const hits = rules.flatMap(([pattern, message, severity]) =>
          [...part.matchAll(new RegExp(pattern, 'gi'))].map(match => ({ path, line, term: match[0], message, severity })));
        const errors = hits.filter(hit => hit.severity === 'error');
        found.push(...(errors.length ? errors : hits));
      }
      line++;
    }
  };
  const walk = (node, parent, key) => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(item => walk(item, parent, key)); return; }
    const machine = (parent?.type === 'ImportDeclaration' || parent?.type?.startsWith('Export')) && key === 'source'
      || key === 'key' && !parent?.computed;
    if (!machine && node.type === 'Literal' && typeof node.value === 'string') scan(node.value, node.start);
    if (node.type === 'TemplateElement') scan(node.value.cooked ?? node.value.raw, node.start);
    if (node.type === 'JSXText') scan(node.value, node.start);
    for (const [childKey, child] of Object.entries(node)) {
      if (childKey !== 'comments') walk(child, node, childKey);
    }
  };
  walk(parsed.program);
  for (const comment of parsed.comments) scan(comment.value, comment.start);
  return found;
}

export function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : /\.[jt]sx?$/.test(path) ? [path] : [];
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const files = sourceFiles(join(root, 'frontend/src'));
  const findings = files.flatMap(path => scanSource(path, readFileSync(path, 'utf8')));
  for (const item of findings) console.error(`${item.path}:${item.line}: ${item.severity}: ${item.message}`);
  console.log(`前端文案检查：${files.length} 个源码文件。`);
  process.exitCode = Number(findings.some(item => item.severity === 'error'));
}
