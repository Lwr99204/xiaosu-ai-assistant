"use client";

import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, DocumentMeta, formatBytes, formatDate, UploadResult } from "@/lib/api";

interface DocumentList { items: DocumentMeta[]; total: number }
interface UploadResponse { results: UploadResult[] }
interface DocumentContent { doc_id: string; filename: string; content: string }

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<DocumentContent | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadDocuments = useCallback(async () => {
    try {
      const data = await api<DocumentList>("/api/documents?size=100");
      setDocuments(data.items);
      setError("");
      return data.items;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "文档列表加载失败");
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadDocuments(); }, [loadDocuments]);
  useEffect(() => {
    if (!documents.some((item) => item.status === "pending")) return;
    const timer = window.setInterval(() => { void loadDocuments(); }, 1500);
    return () => window.clearInterval(timer);
  }, [documents, loadDocuments]);

  async function upload(files: FileList | File[]) {
    const selected = Array.from(files);
    if (!selected.length) return;
    const form = new FormData();
    selected.forEach((file) => form.append("files", file));
    setUploading(true); setError(""); setNotice("");
    try {
      const result = await api<UploadResponse>("/api/documents", { method: "POST", body: form });
      const summary = result.results.map((item) => `${item.filename}：${item.action}`).join("，");
      setNotice(`已提交 ${selected.length} 个文件。${summary}`);
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上传失败");
    } finally { setUploading(false); }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void upload(event.target.files);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files);
  }

  async function remove(item: DocumentMeta) {
    if (!window.confirm(`确认删除「${item.filename}」？删除后将立即停止参与问答。`)) return;
    try {
      await api<{ deleted_chunks: number }>(`/api/documents/${encodeURIComponent(item.doc_id)}`, { method: "DELETE" });
      setNotice(`已删除 ${item.filename}`); await loadDocuments();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "删除失败"); }
  }

  async function reindex(item: DocumentMeta) {
    try {
      await api<DocumentMeta>(`/api/documents/${encodeURIComponent(item.doc_id)}/reindex`, { method: "POST" });
      setNotice(`已提交 ${item.filename} 重建任务`); await loadDocuments();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "重建失败"); }
  }

  async function showPreview(item: DocumentMeta) {
    try {
      setPreview(await api<DocumentContent>(`/api/documents/${encodeURIComponent(item.doc_id)}/content`));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "原文加载失败"); }
  }

  const indexed = documents.filter((item) => item.status === "indexed").length;
  const chunks = documents.reduce((sum, item) => sum + item.chunk_count, 0);

  return (
    <>
      <header className="page-head">
        <div><h1>文档管理</h1><p>维护小苏用于回答公司制度问题的知识来源。</p></div>
        <button className="button" onClick={() => inputRef.current?.click()} disabled={uploading}>
          {uploading ? "正在上传…" : "上传文档"}
        </button>
        <input ref={inputRef} hidden type="file" multiple accept=".md,.txt,.pdf,.docx" onChange={onFileChange} />
      </header>

      <section className="grid-cards" aria-label="文档统计">
        <div className="panel metric"><span className="metric-label">文档总数</span><div className="metric-value">{documents.length}</div><div className="metric-note">支持 MD / TXT / PDF / DOCX</div></div>
        <div className="panel metric"><span className="metric-label">已完成索引</span><div className="metric-value">{indexed}</div><div className="metric-note">完成后立即参与企业微信问答</div></div>
        <div className="panel metric"><span className="metric-label">知识分块</span><div className="metric-value">{chunks}</div><div className="metric-note">保留章节与原文字符偏移</div></div>
      </section>

      {error && <div className="alert" role="alert">{error}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}
      <div className={`upload-zone ${dragging ? "dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <strong>拖拽文件到这里上传</strong><span>单文件不超过 20 MB，单次最多 10 个</span>
      </div>

      <section className="panel">
        <div className="panel-head"><h2>知识库文件</h2><button className="icon-button" onClick={() => void loadDocuments()}>刷新</button></div>
        <div className="table-wrap">
          {loading ? <div className="empty loading">正在读取文档列表…</div> : documents.length === 0 ? <div className="empty"><strong>知识库还是空的</strong>上传第一份公司制度文档开始构建知识库。</div> : (
            <table><thead><tr><th>文件</th><th>状态</th><th>分块</th><th>版本</th><th>更新时间</th><th /></tr></thead>
              <tbody>{documents.map((item) => <tr key={item.doc_id}>
                <td className="file-name">{item.filename}<span className="subtext">{item.ext.toUpperCase()} · {formatBytes(item.byte_size)}</span></td>
                <td><span className={`badge ${item.status}`}>{item.status}</span>{item.error_message && <span className="subtext">{item.error_message}</span>}</td>
                <td>{item.chunk_count}</td><td>v{item.version}</td><td>{formatDate(item.updated_at)}</td>
                <td><div className="actions"><button className="icon-button" onClick={() => void showPreview(item)}>预览</button><button className="icon-button" disabled={item.status === "pending"} onClick={() => void reindex(item)}>重建</button><button className="icon-button danger-text" disabled={item.status === "pending"} onClick={() => void remove(item)}>删除</button></div></td>
              </tr>)}</tbody>
            </table>
          )}
        </div>
      </section>

      {preview && <div className="modal-backdrop" role="presentation" onMouseDown={() => setPreview(null)}><section className="preview-drawer" role="dialog" aria-modal="true" aria-label="文档原文" onMouseDown={(event) => event.stopPropagation()}><div className="panel-head"><div><h2>{preview.filename}</h2><span className="subtext">原文预览与引用高亮基准</span></div><button className="icon-button" onClick={() => setPreview(null)}>关闭</button></div><pre>{preview.content}</pre></section></div>}
    </>
  );
}
