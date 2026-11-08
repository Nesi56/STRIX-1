import { Fragment, type ReactNode } from "react";

// Answers are plain sentences. Older/other models may still emit claim tags like [DETECTED] or "SUGGEST:";
// they are hidden instead of being shown as jargon.
const TAGS = /\s*\[(?:MEASURED|DETECTED|SEEN|REMEMBERED|DERIVED|INTERPRETATION|UNAVAILABLE|HEALTH)\]\s*/g;
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

export function plain(text: string): string {
  return text.replace(TAGS, " ").replace(/\bSUGGEST:\s*/g, "Tip: ").replace(/ {2,}/g, " ").trim();
}

function inline(text: string): ReactNode[] {
  return plain(text).split(INLINE).filter(Boolean).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded bg-white/5 px-1">{part.slice(1, -1)}</code>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

/** Safe minimal rich text: paragraphs, bullet lists, bold, code. No HTML injection. */
export function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={blocks.length} className="ml-4 list-disc space-y-0.5">{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>);
      list = [];
    }
  };
  for (const line of text.split("\n")) {
    const b = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (b) {
      list.push(b[1]);
      continue;
    }
    flush();
    if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line)}</p>);
  }
  flush();
  return <div className="space-y-1 leading-relaxed">{blocks}</div>;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  return ok;
}
