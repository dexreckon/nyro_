"use strict";
/* Tiny, safe markdown subset for assistant replies. parse() is pure (testable in Node); render() builds DOM
   nodes with createElement/textContent only. No HTML is ever parsed, and links are never made clickable. */
(function (root, factory) {
  const md = factory();
  if (typeof module === "object" && module.exports) module.exports = md; else root.NyroMD = md;
})(typeof self !== "undefined" ? self : this, function () {
  const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)/g;

  function inline(text) {
    const out = []; let last = 0, m;
    INLINE.lastIndex = 0;
    while ((m = INLINE.exec(text))) {
      if (m.index > last) out.push({ t: "text", v: text.slice(last, m.index) });
      const s = m[0];
      if (m[1]) out.push({ t: "code", v: s.slice(1, -1) });
      else if (m[2]) out.push({ t: "b", v: s.slice(2, -2) });
      else out.push({ t: "i", v: s.slice(1, -1) });
      last = m.index + s.length;
    }
    if (last < text.length) out.push({ t: "text", v: text.slice(last) });
    return out;
  }

  function parse(src) {
    const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = []; let para = [], list = null, i = 0;
    const flushPara = () => { if (para.length) { const parts = []; para.forEach((l, k) => { if (k) parts.push({ t: "br" }); parts.push(...inline(l)); }); blocks.push({ t: "p", inl: parts }); para = []; } };
    const flushList = () => { if (list) { blocks.push(list); list = null; } };
    while (i < lines.length) {
      const line = lines[i];
      const fence = /^```\s*([\w+-]*)\s*$/.exec(line);
      if (fence) {
        flushPara(); flushList();
        const body = []; i++;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
        i++; // closing fence (or EOF while streaming)
        blocks.push({ t: "code", lang: fence[1], text: body.join("\n") });
        continue;
      }
      const h = /^#{1,6}\s+(.*)$/.exec(line);
      const ul = /^\s*[-*•]\s+(.*)$/.exec(line);
      const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (h) { flushPara(); flushList(); blocks.push({ t: "h", inl: inline(h[1]) }); }
      else if (ul || ol) {
        flushPara();
        const kind = ul ? "ul" : "ol";
        if (!list || list.t !== kind) { flushList(); list = { t: kind, items: [] }; }
        list.items.push(inline((ul || ol)[1]));
      } else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line); }
      i++;
    }
    flushPara(); flushList();
    return blocks;
  }

  function renderInline(doc, parent, inl) {
    inl.forEach((n) => {
      if (n.t === "br") parent.appendChild(doc.createElement("br"));
      else if (n.t === "text") parent.appendChild(doc.createTextNode(n.v));
      else { const el = doc.createElement(n.t === "b" ? "strong" : n.t === "i" ? "em" : "code"); el.textContent = n.v; parent.appendChild(el); }
    });
  }

  function render(doc, container, src) {
    container.textContent = "";
    parse(src).forEach((b) => {
      let el;
      if (b.t === "p") { el = doc.createElement("p"); renderInline(doc, el, b.inl); }
      else if (b.t === "h") { el = doc.createElement("p"); el.className = "md-h"; renderInline(doc, el, b.inl); }
      else if (b.t === "code") {
        el = doc.createElement("pre"); const c = doc.createElement("code"); c.textContent = b.text; el.appendChild(c);
      } else { el = doc.createElement(b.t); b.items.forEach((it) => { const li = doc.createElement("li"); renderInline(doc, li, it); el.appendChild(li); }); }
      el.dir = "auto";
      container.appendChild(el);
    });
  }

  return { parse, inline, render };
});
