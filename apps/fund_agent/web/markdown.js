const escapeHtml = value => String(value ?? "").replace(
  /[&<>"']/g,
  char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
);

function renderInline(value) {
  const code = [];
  let text = escapeHtml(value).replace(/`([^`\n]+)`/g, (_, content) => {
    const token = `\u0000CODE${code.length}\u0000`;
    code.push(`<code>${content}</code>`);
    return token;
  });

  text = text
    .replace(/\[([^\]]+)]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
    .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");

  return text.replace(/\u0000CODE(\d+)\u0000/g, (_, index) => code[Number(index)]);
}

const tableDivider = line => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);

function tableCells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map(cell => cell.trim());
}

function isBlockStart(lines, index) {
  const line = lines[index] || "";
  return /^\s*(```|#{1,6}\s|>|[-+*]\s+|\d+[.)]\s+|---+\s*$)/.test(line)
    || (line.includes("|") && tableDivider(lines[index + 1] || ""));
}

export function renderMarkdown(value) {
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  const output = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    const fence = line.match(/^\s*```([^\s`]*)\s*$/);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) body.push(lines[index++]);
      if (index < lines.length) index += 1;
      const language = fence[1] ? ` data-language="${escapeHtml(fence[1])}"` : "";
      output.push(`<pre><code${language}>${escapeHtml(body.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*---+\s*$/.test(line)) {
      output.push("<hr>");
      index += 1;
      continue;
    }

    if (line.includes("|") && tableDivider(lines[index + 1] || "")) {
      const headings = tableCells(line);
      const alignment = tableCells(lines[index + 1]).map(cell => {
        if (/^:-+:$/.test(cell)) return "center";
        if (/^-+:$/.test(cell)) return "right";
        return "left";
      });
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(tableCells(lines[index++]));
      }
      const head = headings.map((cell, cellIndex) => `<th style="text-align:${alignment[cellIndex] || "left"}">${renderInline(cell)}</th>`).join("");
      const body = rows.map(row => `<tr>${headings.map((_, cellIndex) => `<td style="text-align:${alignment[cellIndex] || "left"}">${renderInline(row[cellIndex] || "")}</td>`).join("")}</tr>`).join("");
      output.push(`<div class="markdown-table"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      output.push(`<blockquote>${quote.map(renderInline).join("<br>")}</blockquote>`);
      continue;
    }

    const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const tag = ordered ? "ol" : "ul";
      const pattern = ordered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-+*]\s+(.+)$/;
      const items = [];
      while (index < lines.length) {
        const item = lines[index].match(pattern);
        if (!item) break;
        items.push(`<li>${renderInline(item[1])}</li>`);
        index += 1;
      }
      output.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    output.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
  }

  return output.join("");
}
