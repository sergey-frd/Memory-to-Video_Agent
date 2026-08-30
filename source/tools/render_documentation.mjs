// Render the checked-in Markdown guides using marked (no network access).
// node tools/render_documentation.mjs [--marked-module /path/to/marked/lib/marked.esm.js]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--marked-module')) {
  throw new Error('Usage: node tools/render_documentation.mjs [--marked-module PATH]');
}
const { Marked } = await import(args.length ? pathToFileURL(path.resolve(args[1])).href : 'marked');
const files = ['USER_GUIDE_RU', 'USER_GUIDE_EN', 'PROJECT_STRUCTURE'];
const escapeHtml = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;').replaceAll('"', '&quot;');

for (const name of files) {
  const markdownPath = path.join(root, 'docs', `${name}.md`);
  const htmlPath = path.join(root, 'docs', `${name}.html`);
  const previous = fs.readFileSync(htmlPath, 'utf8');
  const css = previous.match(/<style>([\s\S]*?)<\/style>/)?.[1];
  if (!css) throw new Error(`Missing existing CSS: ${htmlPath}`);
  const source = fs.readFileSync(markdownPath, 'utf8').replace(/^\uFEFF/, '')
    .replace(/^<\/?main>\s*$/gm, '');
  const headings = [];
  const ids = new Map([...source.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], 1]));
  const marked = new Marked({ gfm: true });
  marked.use({ renderer: {
    heading({ tokens, depth }) {
      const label = this.parser.parseInline(tokens);
      const plain = label.replace(/<[^>]+>/g, '');
      const base = plain.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '')
        .trim().replace(/[\s_]+/g, '-') || 'section';
      const count = ids.get(base) || 0;
      ids.set(base, count + 1);
      const id = count ? `${base}-${count}` : base;
      if (depth === 2 || depth === 3) headings.push({ depth, label, id });
      return `<h${depth} id="${id}">${label}</h${depth}>\n`;
    }
  }});
  let body = marked.parse(source);
  for (const target of files) {
    body = body.replaceAll(`href="${target}.md`, `href="${target}.html`);
  }
  const title = source.match(/^# (.+)$/m)?.[1] || name;
  const nav = headings.map(h => `<a class="level-${h.depth}" href="#${h.id}">${h.label}</a>`).join('\n');
  const lang = name.endsWith('_EN') ? 'en' : 'ru';
  const html = `<!doctype html>
<html lang="${lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="source" content="docs/${name}.md">
  <title>Memory-to-Video Agent | ${escapeHtml(title)}</title>
  <style>${css}</style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar"><nav>${nav}</nav></aside>
    <main><article class="content">
${body}
      <p class="source-note">Source: <a href="${name}.md">docs/${name}.md</a>.</p>
    </article></main>
  </div>
</body>
</html>
`;
  fs.writeFileSync(htmlPath, html, 'utf8');
  console.log(`Rendered docs/${name}.html`);
}
