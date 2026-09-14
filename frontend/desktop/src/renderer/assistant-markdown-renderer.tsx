import { type JSX, type ReactNode } from "react";

import { parseAssistantMarkdown, safeAssistantLinkHref } from "./assistant-markdown.ts";
function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*|__)(.+?)\1|(?<!\*)\*([^*\n]+?)\*(?!\*)|(?<!_)_([^_\n]+?)_(?!_)|`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)\)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  let part = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    const key = `${keyPrefix}-${part}`;
    if (match[1]) nodes.push(<strong key={key}>{match[2]}</strong>);
    else if (match[3] || match[4]) nodes.push(<em key={key}>{match[3] || match[4]}</em>);
    else if (match[5]) nodes.push(<code className="assistant-inline-code" key={key}>{match[5]}</code>);
    else {
      const href = safeAssistantLinkHref(match[7]);
      nodes.push(
        href ? (
          <a href={href} target="_blank" rel="noreferrer" key={key}>{match[6]}</a>
        ) : (
          text.slice(match.index, pattern.lastIndex)
        )
      );
    }
    cursor = pattern.lastIndex;
    part += 1;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

/** Render assistant Markdown as React nodes without injecting HTML. */
export function AssistantMarkdown({ content }: { content: string }): JSX.Element {
  const blocks = parseAssistantMarkdown(content);
  return (
    <div className="assistant-markdown">
      {blocks.map((block, index) => {
        const key = `assistant-block-${index}`;
        if (block.kind === "heading") {
          const Heading = `h${block.level}` as keyof JSX.IntrinsicElements;
          return <Heading key={key}>{renderInlineMarkdown(block.text, key)}</Heading>;
        }
        if (block.kind === "code") {
          return <pre className="assistant-code-block" data-language={block.language || undefined} key={key}><code>{block.text}</code></pre>;
        }
        if (block.kind === "unordered_list" || block.kind === "ordered_list") {
          const List = block.kind === "unordered_list" ? "ul" : "ol";
          return <List key={key}>{block.items.map((item, itemIndex) => <li key={`${key}-${itemIndex}`}>{renderInlineMarkdown(item, `${key}-${itemIndex}`)}</li>)}</List>;
        }
        if (block.kind === "quote") {
          return <blockquote key={key}>{renderInlineMarkdown(block.text, key)}</blockquote>;
        }
        if (block.kind === "paragraph") {
          return <p key={key}>{renderInlineMarkdown(block.text, key)}</p>;
        }
        return null;
      })}
    </div>
  );
}
