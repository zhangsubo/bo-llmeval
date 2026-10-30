import React from 'react';

interface MarkdownViewerProps {
  content: string;
}

function renderInline(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*.*?\*\*|`[^`]+`)/g);
  return parts.map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={idx}
          style={{
            background: '#f1f5f9',
            color: '#0f172a',
            padding: '2px 6px',
            borderRadius: 4,
            fontSize: '0.9em',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
          }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

export const MarkdownViewer: React.FC<MarkdownViewerProps> = ({ content }) => {
  if (!content) return null;

  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const elements: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];
  let listItems: React.ReactNode[] = [];

  const flushList = (keyPrefix: string) => {
    if (listItems.length > 0) {
      elements.push(
        <ul key={`${keyPrefix}-list`} style={{ paddingLeft: 20, margin: '8px 0 12px' }}>
          {listItems}
        </ul>
      );
      listItems = [];
    }
  };

  lines.forEach((line, index) => {
    if (line.trim().startsWith('```')) {
      flushList(`pre-${index}`);
      if (!inCodeBlock) {
        inCodeBlock = true;
        codeLines = [];
      } else {
        inCodeBlock = false;
        elements.push(
          <pre
            key={`code-${index}`}
            style={{
              background: '#0f172a',
              color: '#f8fafc',
              padding: '12px 16px',
              borderRadius: 8,
              overflowX: 'auto',
              fontSize: 13,
              lineHeight: 1.5,
              margin: '10px 0',
            }}
          >
            <code>{codeLines.join('\n')}</code>
          </pre>
        );
        codeLines = [];
      }
      return;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    const trimmed = line.trim();
    if (!trimmed) {
      flushList(`empty-${index}`);
      return;
    }

    if (trimmed.startsWith('#### ')) {
      flushList(`h4-${index}`);
      elements.push(
        <h5 key={index} style={{ margin: '14px 0 6px', fontSize: 14, color: '#1e293b' }}>
          {renderInline(trimmed.slice(5))}
        </h5>
      );
    } else if (trimmed.startsWith('### ')) {
      flushList(`h3-${index}`);
      elements.push(
        <h4
          key={index}
          style={{
            margin: '16px 0 8px',
            fontSize: 15,
            fontWeight: 600,
            color: '#0f172a',
            borderLeft: '3px solid #1677ff',
            paddingLeft: 8,
          }}
        >
          {renderInline(trimmed.slice(4))}
        </h4>
      );
    } else if (trimmed.startsWith('## ')) {
      flushList(`h2-${index}`);
      elements.push(
        <h3
          key={index}
          style={{
            margin: '16px 0 10px',
            fontSize: 17,
            fontWeight: 600,
            color: '#0f172a',
          }}
        >
          {renderInline(trimmed.slice(3))}
        </h3>
      );
    } else if (trimmed.startsWith('# ')) {
      flushList(`h1-${index}`);
      elements.push(
        <h2 key={index} style={{ margin: '18px 0 10px', fontSize: 19, fontWeight: 700 }}>
          {renderInline(trimmed.slice(2))}
        </h2>
      );
    } else if (/^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
      const itemText = trimmed.replace(/^([-*]|\d+\.)\s+/, '');
      listItems.push(
        <li key={`li-${index}`} style={{ marginBottom: 6, lineHeight: 1.65, color: '#334155' }}>
          {renderInline(itemText)}
        </li>
      );
    } else {
      flushList(`p-${index}`);
      elements.push(
        <p key={index} style={{ margin: '8px 0', lineHeight: 1.7, color: '#334155' }}>
          {renderInline(trimmed)}
        </p>
      );
    }
  });

  flushList('end');
  if (inCodeBlock && codeLines.length > 0) {
    elements.push(
      <pre
        key="unclosed-code"
        style={{
          background: '#0f172a',
          color: '#f8fafc',
          padding: '12px 16px',
          borderRadius: 8,
          overflowX: 'auto',
          fontSize: 13,
        }}
      >
        <code>{codeLines.join('\n')}</code>
      </pre>
    );
  }

  return <div style={{ fontSize: 14 }}>{elements}</div>;
};
