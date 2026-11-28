"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Citation, formatDate, LogDetail, LogItem, LogStats } from "@/lib/api";
import CitationSourcePreview from "./CitationSourcePreview";

interface LogList { items: LogItem[]; total: number; page: number; size: number }
interface DocumentContent { doc_id: string; filename: string; content: string }
interface CitationPreview { citation: Citation; content: string }

export default function LogsPage() {
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [stats, setStats] = useState<LogStats | null>(null);
  const [detail, setDetail] = useState<LogDetail | null>(null);
  const [citationPreview, setCitationPreview] = useState<CitationPreview | null>(null);
  const [citationLoading, setCitationLoading] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [platform, setPlatform] = useState("");
  const [userId, setUserId] = useState("");
  const [tools, setTools] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const size = 20;

  const load = useCallback(async (targetPage = page) => {
    setLoading(true);
    const query = new URLSearchParams({ page: String(targetPage), size: String(size) });
    if (platform) query.set("platform", platform);
    if (userId.trim()) query.set("user_id", userId.trim());
    if (tools) query.set("has_tool_call", tools);
    try {
      const [list, summary] = await Promise.all([
        api<LogList>(`/api/logs?${query}`), api<LogStats>("/api/logs/stats"),
      ]);
      setLogs(list.items); setTotal(list.total); setStats(summary); setPage(targetPage); setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "日志加载失败");
    } finally { setLoading(false); }
  }, [page, platform, userId, tools]);

  useEffect(() => { void load(1); }, []); // 首次加载；后续筛选由表单提交触发

  function submit(event: FormEvent) { event.preventDefault(); void load(1); }

  async function openDetail(id: number) {
    try { setDetail(await api<LogDetail>(`/api/logs/${id}`)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "日志详情加载失败"); }
  }

  async function openCitation(citation: Citation) {
    setCitationLoading(citation.ref_no);
    try {
      const document = await api<DocumentContent>(
        `/api/documents/${encodeURIComponent(citation.doc_id)}/content`,
      );
      setCitationPreview({ citation, content: document.content });
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "引用原文加载失败");
    } finally {
      setCitationLoading(null);
    }
  }

  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <>
      <header className="page-head">
        <div><h1>对话日志</h1><p>审计企业微信中的每次问答、工具调用和模型消耗。</p></div>
        <button className="button secondary" onClick={() => void load(page)}>刷新日志</button>
      </header>

      <section className="grid-cards">
        <div className="panel metric"><span className="metric-label">今日问答</span><div className="metric-value">{stats?.today.count ?? "—"}</div><div className="metric-note">所有平台的已落库对话</div></div>
        <div className="panel metric"><span className="metric-label">今日 Token</span><div className="metric-value">{stats?.today.tokens.toLocaleString() ?? "—"}</div><div className="metric-note">Prompt 与 Completion 合计</div></div>
        <div className="panel metric"><span className="metric-label">累计问答</span><div className="metric-value">{stats?.total.count ?? "—"}</div><div className="metric-note">累计成本 ¥{stats?.total.cost_cny.toFixed(4) ?? "—"}</div></div>
      </section>

      {error && <div className="alert" role="alert">{error}</div>}
      <section className="panel">
        <div className="panel-head">
          <h2>问答记录</h2>
          <form className="filters" onSubmit={submit}>
            <select className="select" value={platform} onChange={(event) => setPlatform(event.target.value)} aria-label="平台筛选"><option value="">全部平台</option><option value="wecom">企业微信</option><option value="web">Web</option></select>
            <input className="input" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="用户 ID" aria-label="用户筛选" />
            <select className="select" value={tools} onChange={(event) => setTools(event.target.value)} aria-label="工具筛选"><option value="">全部问答</option><option value="true">调用过工具</option><option value="false">未调用工具</option></select>
            <button className="button" type="submit">筛选</button>
          </form>
        </div>
        <div className="table-wrap">
          {loading ? <div className="empty loading">正在读取对话日志…</div> : logs.length === 0 ? <div className="empty"><strong>没有符合条件的日志</strong>员工在企业微信中的问答完成后会出现在这里。</div> : (
            <table><thead><tr><th>时间 / 用户</th><th>问题与回答</th><th>状态</th><th>工具</th><th>Token / 耗时</th><th /></tr></thead>
              <tbody>{logs.map((item) => <tr key={item.id}>
                <td>{formatDate(item.created_at)}<span className="subtext">{item.platform} · {item.user_name || item.user_id}</span></td>
                <td className="question-cell">{item.question}<span className="subtext answer-preview">{item.answer || "尚无回答"}</span></td>
                <td><span className={`badge ${item.status}`}>{item.status}</span></td>
                <td>{item.tool_count ? `${item.tool_count} 次` : "—"}</td>
                <td>{item.total_tokens.toLocaleString()}<span className="subtext">{item.latency_ms} ms</span></td>
                <td><button className="icon-button" onClick={() => void openDetail(item.id)}>查看详情</button></td>
              </tr>)}</tbody>
            </table>
          )}
        </div>
        <div className="pagination"><span>共 {total} 条 · 第 {page}/{pages} 页</span><div><button className="icon-button" disabled={page <= 1} onClick={() => void load(page - 1)}>上一页</button><button className="icon-button" disabled={page >= pages} onClick={() => void load(page + 1)}>下一页</button></div></div>
      </section>

      {detail && <div className="modal-backdrop" role="presentation" onMouseDown={() => setDetail(null)}><section className="preview-drawer log-detail" role="dialog" aria-modal="true" aria-label="对话日志详情" onMouseDown={(event) => event.stopPropagation()}>
        <div className="panel-head"><div><h2>对话详情</h2><span className="subtext">{detail.request_id} · {formatDate(detail.created_at)}</span></div><button className="icon-button" onClick={() => setDetail(null)}>关闭</button></div>
        <div className="detail-body">
          <div className="detail-meta"><span><b>平台</b>{detail.platform}</span><span><b>用户</b>{detail.user_name || detail.user_id}</span><span><b>状态</b>{detail.status}</span><span><b>耗时</b>{detail.latency_ms} ms</span></div>
          <article className="message-block user-message"><small>员工提问</small><p>{detail.question}</p></article>
          <article className="message-block assistant-message"><small>小苏回答</small><p>{detail.answer || "没有生成回答"}</p></article>
          <h3>工具调用链 <span>{detail.tool_calls.length}</span></h3>
          {detail.tool_calls.length === 0 ? <p className="muted">本轮没有调用工具。</p> : <div className="timeline">{detail.tool_calls.map((call, index) => <div className="timeline-item" key={`${call.round}-${call.tool_name}-${index}`}><i /><div><strong>{call.tool_name}</strong><span className={`badge ${call.ok ? "ok" : "error"}`}>{call.ok ? "成功" : "失败"}</span><pre>{JSON.stringify(call.arguments, null, 2)}</pre><p>{call.result_summary}</p><small>{call.latency_ms} ms</small></div></div>)}</div>}
          <h3>引用来源 <span>{detail.citations.length}</span></h3>
          {detail.citations.length === 0 ? <p className="muted">本轮没有文档引用。</p> : detail.citations.map((citation) => <article className="citation-card" key={`${citation.ref_no}-${citation.filename}`}><b>[{citation.ref_no}] {citation.filename}</b><span>{Array.isArray(citation.heading_path) ? citation.heading_path.join(" › ") : citation.heading_path}</span><p>{citation.snippet}</p><small>偏移 {citation.char_start}:{citation.char_end} · score {citation.score.toFixed(4)}</small><button className="icon-button citation-locate" disabled={citationLoading === citation.ref_no} onClick={() => void openCitation(citation)}>{citationLoading === citation.ref_no ? "正在加载…" : "定位并高亮原文"}</button></article>)}
          <h3>模型用量</h3><div className="usage-row"><span>模型 <b>{detail.usage.model || "—"}</b></span><span>Prompt <b>{detail.usage.prompt_tokens}</b></span><span>Completion <b>{detail.usage.completion_tokens}</b></span><span>总计 <b>{detail.usage.total_tokens}</b></span><span>成本 <b>¥{detail.usage.cost_cny.toFixed(4)}</b></span></div>
        </div>
      </section></div>}
      {citationPreview && <CitationSourcePreview citation={citationPreview.citation} content={citationPreview.content} onClose={() => setCitationPreview(null)} />}
    </>
  );
}
