import React from "react";

/**
 * Tiny safe markdown renderer — handles the subset M3 typically uses.
 *
 * Supported:
 *   - **bold**         → <strong>
 *   - *italic*         → <em>
 *   - `code`           → <code>
 *   - # ## ### heading → <h1/2/3> (smaller sizes for in-chat context)
 *   - - list item      → <ul><li>
 *   - 1. ordered list  → <ol><li>
 *   - line break       → <br/>
 *   - ```code block``` → <pre><code>
 *   - blank line       → paragraph break
 *
 * NOT supported (kept as plain text):
 *   - links, images, tables, HTML
 *
 * We escape HTML first to avoid XSS — even if M3 is "trusted" we never
 * want unescaped HTML in the chat bubble.
 */

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

interface Block {
  kind: "p" | "h1" | "h2" | "h3" | "ul" | "ol" | "pre" | "br";
  lines: string[];
}

function parseBlocks(src: string): Block[] {
  const blocks: Block[] = [];
  const lines = src.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // fenced code block ```
    if (line.trim().startsWith("```")) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      blocks.push({ kind: "pre", lines: codeLines });
      continue;
    }

    // heading (^ only matches at the start of *this line*, not the start
    // of the source — important because streamingText concatenates chunks
    // without re-tokenizing)
    const h = line.match(/^\s{0,3}(#{1,3})\s+(.*)$/);
    if (h) {
      const level = h[1].length as 1 | 2 | 3;
      blocks.push({ kind: level === 1 ? "h1" : level === 2 ? "h2" : "h3", lines: [h[2]] });
      i++;
      continue;
    }

    // unordered list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push({ kind: "ul", lines: items });
      continue;
    }

    // ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ kind: "ol", lines: items });
      continue;
    }

    // blank line → paragraph break
    if (line.trim() === "") {
      i++;
      continue;
    }

    // paragraph: collect consecutive non-blank, non-special lines
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].trim().startsWith("```") &&
      !/^\s{0,3}#{1,3}\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length > 0) {
      blocks.push({ kind: "p", lines: paraLines });
    }
  }
  return blocks;
}

function renderInline(text: string): React.ReactNode[] {
  // First escape HTML, then convert markdown markers to placeholders,
  // then split-and-replace into React elements.
  const escaped = escapeHtml(text);
  const parts: React.ReactNode[] = [];
  // regex matches: **bold**, *italic*, `code`
  const re = /(\*\*([^*]+)\*\*)|(\*([^*]+)\*)|(`([^`]+)`)/g;
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(escaped)) !== null) {
    if (m.index > lastIndex) {
      parts.push(escaped.slice(lastIndex, m.index));
    }
    if (m[1] !== undefined) {
      // **bold**
      parts.push(<strong key={key++}>{m[2]}</strong>);
    } else if (m[3] !== undefined) {
      // *italic*
      parts.push(<em key={key++}>{m[4]}</em>);
    } else if (m[5] !== undefined) {
      // `code`
      parts.push(
        <code
          key={key++}
          className="px-1 py-0.5 rounded bg-black/10 text-[12px] font-mono"
        >
          {m[6]}
        </code>
      );
    }
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < escaped.length) {
    parts.push(escaped.slice(lastIndex));
  }
  return parts;
}

export const MiniMarkdown: React.FC<{ source: string }> = ({ source }) => {
  const blocks = parseBlocks(source);
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "h1":
            return (
              <h1 key={i} className="text-[15px] font-bold mt-2 mb-1">
                {renderInline(b.lines[0])}
              </h1>
            );
          case "h2":
            return (
              <h2 key={i} className="text-[14px] font-bold mt-2 mb-1">
                {renderInline(b.lines[0])}
              </h2>
            );
          case "h3":
            return (
              <h3 key={i} className="text-[13px] font-semibold mt-1.5 mb-0.5">
                {renderInline(b.lines[0])}
              </h3>
            );
          case "ul":
            return (
              <ul key={i} className="list-disc list-inside space-y-0.5 my-1">
                {b.lines.map((it, j) => (
                  <li key={j}>{renderInline(it)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i} className="list-decimal list-inside space-y-0.5 my-1">
                {b.lines.map((it, j) => (
                  <li key={j}>{renderInline(it)}</li>
                ))}
              </ol>
            );
          case "pre":
            return (
              <pre
                key={i}
                className="bg-black/10 rounded-lg p-2 my-1 text-[12px] font-mono overflow-x-auto"
              >
                <code>{b.lines.join("\n")}</code>
              </pre>
            );
          case "p":
            return (
              <p key={i} className="my-1 leading-relaxed">
                {b.lines.map((line, j) => (
                  <React.Fragment key={j}>
                    {renderInline(line)}
                    {j < b.lines.length - 1 && <br />}
                  </React.Fragment>
                ))}
              </p>
            );
          default:
            return null;
        }
      })}
    </>
  );
};
