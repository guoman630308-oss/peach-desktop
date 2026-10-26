/* ?edit 原位交互复用官网 edit-client.js；源码与演示草稿使用各自的存储入口。 */
(() => {
  const source = document.body.dataset.peachCopyMode === 'source', key = 'peach.copy-edits.v1';
  let edits = {}, active = true, editing = null, saving = false;
  try { edits = JSON.parse(localStorage.getItem(key) || '{}'); } catch {}
  const originals = new WeakMap(), skip = '[data-copy-editor],script,style,textarea,input,select,svg,[contenteditable]';
  const css = document.createElement('style'); css.dataset.copyEditor = '';
  css.textContent = `
    [data-copy-highlight]{position:fixed;pointer-events:none;z-index:2147483646;outline:1.5px dashed var(--tungsten);outline-offset:3px;border-radius:var(--badge-radius);display:none}
    [data-copy-inline]{outline:2px solid var(--tungsten);outline-offset:3px;border-radius:var(--badge-radius);background:var(--hover);user-select:text;cursor:text}
    [data-copy-input]{position:fixed;z-index:2147483647;padding:2px 6px;border:0;border-radius:var(--badge-radius);outline:2px solid var(--tungsten);background:var(--ground);color:var(--ink)}
    [data-copy-bar]>[role=status]{max-width:min(360px,calc(100vw - 64px));white-space:normal;overflow-wrap:anywhere}
    @media(max-width:600px){[data-copy-bar]>[role=status]{flex-basis:100%;text-align:center}}
    [data-copy-choice]{position:fixed;z-index:2147483647;background:var(--ground);color:var(--ink);border:1px solid var(--border-15);border-radius:var(--control-radius);padding:8px;max-width:calc(100vw - 32px)}
  `;
  document.head.append(css);
  const highlight = document.createElement('div'); highlight.dataset.copyEditor = ''; highlight.dataset.copyHighlight = '';
  const host = document.createElement('div'); host.className = 'peach-react'; host.dataset.copyEditor = ''; host.dataset.batchScope = '';
  const bar = document.createElement('div'); bar.dataset.copyEditor = ''; bar.dataset.copyBar = '';
  bar.dataset.selectionDock = ''; bar.dataset.glassPane = ''; bar.setAttribute('role', 'group'); bar.setAttribute('aria-label', '文案编辑');
  const hint = document.createElement('span'); hint.id = 'peach-copy-hint'; hint.hidden = true; hint.textContent = '点击文字修改，回车或点别处保存，Esc 取消；暂停编辑后可操作页面。';
  bar.setAttribute('aria-describedby', hint.id);
  const status = document.createElement('span'); status.setAttribute('role', 'status'); status.textContent = '编辑文案'; bar.append(status);
  const say = text => { status.textContent = text; };
  const button = (text, fn) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = text; b.onclick = fn; bar.append(b); return b; };
  const toggle = button('暂停编辑', () => {
    if (saving) return;
    commit(); active = !active; highlight.style.display = 'none'; toggle.textContent = active ? '暂停编辑' : '继续编辑';
  });
  button('导出修改', () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(edits, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = 'peach-copy-edits.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  button('退出编辑', () => { if (saving) return; commit(true); const url = new URL(location.href); url.searchParams.delete('edit'); location.href = url; });
  host.append(hint, bar); document.body.append(highlight, host);
  function apply() {
    if (editing || saving) return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.parentElement || node.parentElement.closest(skip)) continue;
      const original = originals.get(node) || node.data.trim();
      if (Object.hasOwn(edits, original) && original !== edits[original]) {
        originals.set(node, original);
        const next = /^\s*/.exec(node.data)[0] + edits[original] + /\s*$/.exec(node.data)[0];
        if (node.data !== next) node.data = next;
      }
    }
  }
  new MutationObserver(apply).observe(document.body, { childList: true, subtree: true, characterData: true }); apply();
  if (source) fetch('/dev/copy-edits').then(r => r.ok ? r.json() : {}).then(saved => { edits = { ...edits, ...saved }; apply(); }).catch(() => say('暂时无法读取修改记录'));
  const rectsOf = node => { const range = document.createRange(); range.selectNodeContents(node); return [...range.getClientRects()]; };
  function textAt(x, y) {
    const caret = document.caretPositionFromPoint?.(x, y), range = caret ? null : document.caretRangeFromPoint?.(x, y);
    const node = caret?.offsetNode || range?.startContainer;
    if (!node || node.nodeType !== 3 || !node.data.trim() || !node.parentElement || node.parentElement.closest(skip)) return null;
    return rectsOf(node).some(r => x >= r.left - 2 && x <= r.right + 2 && y >= r.top - 2 && y <= r.bottom + 2) ? node : null;
  }
  document.addEventListener('mousemove', event => {
    if (!active || editing || saving) return;
    const node = textAt(event.clientX, event.clientY);
    if (!node) { highlight.style.display = 'none'; return; }
    const rects = rectsOf(node), left = Math.min(...rects.map(r => r.left)), top = Math.min(...rects.map(r => r.top));
    Object.assign(highlight.style, { display: 'block', left: left + 'px', top: top + 'px', width: Math.max(...rects.map(r => r.right)) - left + 'px', height: Math.max(...rects.map(r => r.bottom)) - top + 'px' });
  }, { passive: true });
  addEventListener('scroll', () => { highlight.style.display = 'none'; }, true);
  document.addEventListener('click', event => {
    if (!active || saving || event.target.closest?.('[data-copy-editor]')) return;
    if (editing) { event.preventDefault(); event.stopImmediatePropagation(); if (!editing.box.contains(event.target)) commit(); return; }
    const node = textAt(event.clientX, event.clientY); if (!node) return;
    event.preventDefault(); event.stopImmediatePropagation(); begin(node);
  }, true);
  function begin(node) {
    const before = node.data, lead = /^\s*/.exec(before)[0], trail = /\s*$/.exec(before)[0], core = before.trim(); let box;
    if (node.parentElement.closest('button,a,[role=button]')) {
      const rect = rectsOf(node)[0], style = getComputedStyle(node.parentElement);
      box = document.createElement('input'); box.value = core; box.dataset.copyEditor = ''; box.dataset.copyInput = '';
      const width = Math.min(Math.max(140, rect.width + 60), innerWidth - 32);
      Object.assign(box.style, { left: Math.max(16, Math.min(rect.left - 6, innerWidth - width - 16)) + 'px', top: rect.top - 2 + 'px', width: width + 'px', font: style.font, letterSpacing: style.letterSpacing });
      (node.parentElement.closest('dialog[open]') || document.body).append(box);
    } else {
      box = document.createElement('span'); box.dataset.copyInline = ''; box.contentEditable = 'plaintext-only'; box.spellcheck = false; box.textContent = core;
      node.replaceWith(...[lead, box, trail].filter(Boolean));
    }
    box.setAttribute('aria-label', '修改文字'); editing = { node, before, lead, trail, core, box, original: originals.get(node) || core }; box.focus();
    if (box.tagName === 'INPUT') box.select();
    else { const range = document.createRange(); range.selectNodeContents(box); getSelection().removeAllRanges(); getSelection().addRange(range); }
    highlight.style.display = 'none'; say('Enter 保存 · Esc 取消');
    box.addEventListener('keydown', event => {
      event.stopPropagation(); if (event.isComposing) return;
      if (event.key === 'Enter') { event.preventDefault(); commit(); }
      else if (event.key === 'Escape') { event.preventDefault(); commit(true); }
    });
    box.addEventListener('blur', () => commit());
  }
  function putBack(ed) {
    if (ed.box.tagName === 'INPUT') { ed.box.remove(); return; }
    const a = ed.box.previousSibling, c = ed.box.nextSibling;
    if (ed.lead && a?.nodeType === 3) a.remove(); if (ed.trail && c?.nodeType === 3) c.remove(); ed.box.replaceWith(ed.node);
  }
  async function choose(candidates, rect) {
    const select = document.createElement('select'); select.dataset.copyEditor = ''; select.dataset.copyChoice = ''; select.setAttribute('aria-label', '文案来源');
    select.add(new Option('请选择文案来源', '')); candidates.forEach((x, i) => select.add(new Option(`${x.file}:${x.line}`, String(i))));
    Object.assign(select.style, { left: Math.max(16, Math.min(rect.left, innerWidth - 300)) + 'px', top: Math.max(16, Math.min(rect.bottom + 8, innerHeight - 60)) + 'px' });
    document.body.append(select); select.focus(); say('这段文案有多个来源，请选择要修改的位置');
    return new Promise(resolve => {
      const finish = value => { select.remove(); document.removeEventListener('keydown', cancel, true); resolve(value); };
      const cancel = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); finish(null); } };
      select.onchange = () => { if (select.value !== '') finish(candidates[Number(select.value)]); }; document.addEventListener('keydown', cancel, true);
    });
  }
  async function commit(cancel = false) {
    const ed = editing; if (!ed) return; editing = null;
    const replacement = (ed.box.tagName === 'INPUT' ? ed.box.value : ed.box.textContent).replace(/\s*\n\s*/g, ' ').trim();
    const connected = ed.node.isConnected || ed.box.isConnected, rect = ed.box.getBoundingClientRect(); putBack(ed);
    if (cancel || !connected) { say('已取消'); return; }
    if (replacement === ed.core) return;
    if (!replacement) { say('不能留空，没有保存'); return; }
    saving = true; bar.setAttribute('aria-busy', 'true'); say('保存中…');
    try {
      let message = '已保存草稿';
      if (source) {
        const response = await fetch('/dev/copy-candidates?text=' + encodeURIComponent(ed.core)), candidates = await response.json();
        if (!response.ok || !candidates.length) throw Error('未找到静态源文案');
        const candidate = candidates.length === 1 ? candidates[0] : await choose(candidates, rect);
        if (!candidate) { say('已取消'); return; }
        const saved = await fetch('/dev/copy-save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ original: ed.core, replacement, candidate }) });
        const result = await saved.json(); if (!saved.ok) throw Error(result.error || '保存失败');
        message = '已保存修改';
      }
      edits[ed.original] = replacement; originals.set(ed.node, ed.original); localStorage.setItem(key, JSON.stringify(edits)); ed.node.data = ed.lead + replacement + ed.trail;
      say(message);
    } catch (error) { ed.node.data = ed.before; say('没保存：' + error.message); }
    finally { saving = false; bar.removeAttribute('aria-busy'); apply(); }
  }
})();
