import type {ReactNode} from 'react';

function inline(source: string): ReactNode[] {
  return source.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      try {
        const url = new URL(link[2], location.origin);
        if (url.protocol === 'https:' || (url.protocol === 'http:' && url.origin === location.origin)) {
          return <a key={index} href={url.href} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
        }
      } catch { /* Invalid links remain readable text. */ }
    }
    return part;
  });
}

// Render the answer as text and a small set of Markdown blocks; never insert model HTML.
export function MessageText({text}: {text: string}) {
  const lines = text.split(/\r?\n/);
  const blocks: ReactNode[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    const key = index;
    if (line.startsWith('```')) {
      const code: string[] = [];
      index++;
      while (index < lines.length && !lines[index].startsWith('```')) code.push(lines[index++]);
      index++;
      blocks.push(<pre key={key}><code>{code.join('\n')}</code></pre>);
    } else if (/^#{1,6}\s/.test(line)) {
      blocks.push(<h3 key={key}>{inline(line.replace(/^#{1,6}\s+/, ''))}</h3>);
      index++;
    } else if (/^[-*]\s/.test(line) || /^\d+\.\s/.test(line)) {
      const numbered = /^\d+\.\s/.test(line);
      const pattern = numbered ? /^\d+\.\s+/ : /^[-*]\s+/;
      const items: ReactNode[] = [];
      while (index < lines.length && pattern.test(lines[index])) {
        items.push(<li key={index}>{inline(lines[index++].replace(pattern, ''))}</li>);
      }
      blocks.push(numbered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>);
    } else if (line.trim()) {
      const paragraph: string[] = [line];
      index++;
      while (index < lines.length && lines[index].trim() && !/^(#{1,6}\s|```|[-*]\s|\d+\.\s)/.test(lines[index])) paragraph.push(lines[index++]);
      blocks.push(<p key={key}>{inline(paragraph.join('\n'))}</p>);
    } else index++;
  }
  return <div className="agent-message-text">{blocks}</div>;
}
