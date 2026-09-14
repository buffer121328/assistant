export type MarkdownBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "unordered_list" | "ordered_list"; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "code"; language: string; text: string };

/** Return only externally safe link targets for assistant Markdown. */
export function safeAssistantLinkHref(value: string): string | null {
  try {
    const url = new URL(value);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

/** Parse the small, safe Markdown subset used by assistant answers. */
export function parseAssistantMarkdown(content: string): MarkdownBlock[] {
  const lines = content.replace(/\r\n?/g, "\n").trim().split("\n");
  if (lines.length === 1 && !lines[0]) return [];

  const blocks: MarkdownBlock[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ kind: "code", language: fence[1] || "", text: codeLines.join("\n") });
      continue;
    }

    const heading = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const kind = unordered ? "unordered_list" : "ordered_list";
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].match(
          kind === "unordered_list" ? /^\s*[-*+]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/
        );
        if (!item) break;
        items.push(item[1]);
        index += 1;
      }
      blocks.push({ kind, items });
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", text: quoteLines.join("\n") });
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim()) {
      if (
        /^\s*```/.test(lines[index]) ||
        /^\s*#{1,6}\s+/.test(lines[index]) ||
        /^\s*[-*+]\s+/.test(lines[index]) ||
        /^\s*\d+[.)]\s+/.test(lines[index]) ||
        /^\s*>\s?/.test(lines[index])
      ) break;
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: paragraphLines.join("\n") });
  }
  return blocks;
}
