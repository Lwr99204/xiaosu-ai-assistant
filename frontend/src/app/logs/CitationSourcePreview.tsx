"use client";

import { useEffect, useMemo, useRef } from "react";
import type { Citation } from "@/lib/api";

interface CitationSourcePreviewProps {
  citation: Citation;
  content: string;
  onClose: () => void;
}

export default function CitationSourcePreview({
  citation,
  content,
  onClose,
}: CitationSourcePreviewProps) {
  const highlightRef = useRef<HTMLElement>(null);
  const range = useMemo(() => {
    const start = Math.max(0, Math.min(citation.char_start, content.length));
    const end = Math.max(start, Math.min(citation.char_end, content.length));
    return { start, end, valid: end > start };
  }, [citation.char_end, citation.char_start, content.length]);

  useEffect(() => {
    highlightRef.current?.scrollIntoView({ block: "center" });
    highlightRef.current?.focus({ preventScroll: true });
  }, []);

  return (
    <div
      className="modal-backdrop citation-source-backdrop"
      role="presentation"
      onMouseDown={onClose}
    >
      <section
        className="preview-drawer citation-source-preview"
        role="dialog"
        aria-modal="true"
        aria-label={`${citation.filename} 引用原文`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="panel-head">
          <div>
            <h2>{citation.filename}</h2>
            <span className="subtext">
              引用 [{citation.ref_no}] · 偏移 {citation.char_start}:{citation.char_end}
            </span>
          </div>
          <button className="icon-button" onClick={onClose}>关闭</button>
        </div>
        {!range.valid && (
          <div className="alert" role="alert">
            当前文档版本与日志中的引用偏移不一致，已展示现有原文。
          </div>
        )}
        <pre>
          {range.valid ? (
            <>
              {content.slice(0, range.start)}
              <mark ref={highlightRef} className="citation-highlight" tabIndex={-1}>
                {content.slice(range.start, range.end)}
              </mark>
              {content.slice(range.end)}
            </>
          ) : content}
        </pre>
      </section>
    </div>
  );
}
