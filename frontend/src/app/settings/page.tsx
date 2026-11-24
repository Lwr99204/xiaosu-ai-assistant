"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ComponentStatus, formatDate, HealthResponse } from "@/lib/api";

interface ModelInfo { id: string; name: string; provider: string }
interface ModelSettings { current: string; available: ModelInfo[] }

function StatusCard({ title, status, detail }: { title: string; status: ComponentStatus; detail: string }) {
  return <div className="panel service-card"><div><span className="metric-label">{title}</span><span className={`badge ${status}`}>{status}</span></div><p>{detail}</p></div>;
}

export default function SettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [models, setModels] = useState<ModelSettings | null>(null);
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [healthData, modelData] = await Promise.all([
        api<HealthResponse>("/api/health"), api<ModelSettings>("/api/settings/model"),
      ]);
      setHealth(healthData); setModels(modelData); setSelected(modelData.current); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "设置加载失败"); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function saveModel() {
    if (!models || selected === models.current) return;
    setSaving(true); setNotice("");
    try {
      await api<{ ok: boolean; current: string }>("/api/settings/model", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: selected }),
      });
      setNotice("模型已切换，新收到的消息将使用该模型。"); await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "模型切换失败"); }
    finally { setSaving(false); }
  }

  const wecom = health?.im.wecom;
  return (
    <>
      <header className="page-head"><div><h1>系统设置</h1><p>查看运行组件和企业微信连接状态，切换对话模型。</p></div><button className="button secondary" onClick={() => void load()}>刷新状态</button></header>
      {error && <div className="alert" role="alert">{error}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}

      <section className="settings-grid">
        <div className="panel settings-section">
          <div className="panel-head"><h2>对话模型</h2><span className="safe-label">凭证不会返回前端</span></div>
          <div className="panel-body">
            <label className="field-label" htmlFor="model">当前模型</label>
            <select id="model" className="select model-select" value={selected} onChange={(event) => setSelected(event.target.value)}>
              {models?.available.map((model) => <option value={model.id} key={model.id}>{model.name} · {model.provider}</option>)}
            </select>
            <p className="field-help">切换只影响后续新消息；历史日志保留当时使用的模型名称。</p>
            <button className="button" disabled={!models || selected === models.current || saving} onClick={() => void saveModel()}>{saving ? "正在切换…" : "应用模型"}</button>
          </div>
        </div>

        <div className="panel settings-section">
          <div className="panel-head"><h2>企业微信</h2><span className={`badge ${wecom?.connected ? "ok" : "not_wired"}`}>{wecom?.connected ? "已连接" : "未连接"}</span></div>
          <div className="panel-body connection-info">
            <div><span>是否启用</span><b>{wecom?.enabled ? "是" : "否"}</b></div>
            <div><span>连接状态</span><b>{wecom?.connected ? "WebSocket 在线" : "当前离线"}</b></div>
            <div><span>最后心跳</span><b>{formatDate(wecom?.last_heartbeat ?? null)}</b></div>
          </div>
        </div>
      </section>

      <h2 className="section-title">运行组件</h2>
      <section className="service-grid">
        {health ? <><StatusCard title="Embedding" status={health.embed.status} detail={health.embed.detail} /><StatusCard title="向量库" status={health.vector_store.status} detail={health.vector_store.detail} /><StatusCard title="SQLite" status={health.db.status} detail={health.db.detail} /><StatusCard title="LLM" status={health.llm.status} detail={health.llm.detail} /></> : <div className="loading">正在读取健康状态…</div>}
      </section>
    </>
  );
}
