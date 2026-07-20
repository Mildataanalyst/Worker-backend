'use client';

import { useEffect, useState } from 'react';
import { BACKEND, safeJSON } from '@/lib/backendClient';

type Entry = {
  id?: string;
  label?: string;
  action?: string;
  created_at?: string;
  region?: string;
};

type UndoStatus = {
  can_undo?: boolean;
  can_redo?: boolean;
  undo?: Entry | null;
  redo?: Entry | null;
};

export default function AdminUndoRedo({
  region = 'Karnataka',
  context = 'Admin recovery',
  onRestored,
}: {
  region?: string;
  context?: string;
  onRestored?: () => void;
}) {
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState<UndoStatus>({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function refresh() {
    if (!BACKEND) return;
    const r = await safeJSON(`${BACKEND}/admin/undo-redo/status?region=${encodeURIComponent(region || '')}`);
    if (r.ok) setStatus(r.data || {});
  }

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [region]);

  async function restore(kind: 'undo' | 'redo') {
    if (!BACKEND) { setMessage('Backend URL is not configured.'); return; }
    if (!password.trim()) { setMessage('Enter admin password first.'); return; }
    const target = kind === 'undo' ? status.undo : status.redo;
    const label = target?.label || target?.action || kind;
    const ok = window.confirm(`${kind === 'undo' ? 'Undo' : 'Redo'}: ${label}?`);
    if (!ok) return;
    setBusy(true);
    setMessage(kind === 'undo' ? 'Undoing…' : 'Redoing…');
    const r = await safeJSON(`${BACKEND}/admin/${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, region }),
    });
    setBusy(false);
    if (!r.ok) { setMessage(r.error || `Could not ${kind}.`); return; }
    setStatus(r.data || {});
    setMessage(`${kind === 'undo' ? 'Undone' : 'Redone'}: ${label}`);
    onRestored?.();
  }

  const undoLabel = status.undo?.label || status.undo?.action || 'Nothing to undo';
  const redoLabel = status.redo?.label || status.redo?.action || 'Nothing to redo';

  return <div className="admin-undo-redo">
    <div>
      <b>{context}</b>
      <small>Undo/redo protected admin moves.</small>
    </div>
    <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Admin password" />
    <button className="quiet-btn" disabled={busy || !status.can_undo} title={undoLabel} onClick={() => restore('undo')}>↶ Undo</button>
    <button className="quiet-btn" disabled={busy || !status.can_redo} title={redoLabel} onClick={() => restore('redo')}>↷ Redo</button>
    <button className="quiet-btn" disabled={busy} onClick={refresh}>Refresh</button>
    <span>{message || (status.can_undo ? `Last: ${undoLabel}` : 'No admin actions yet.')}</span>
  </div>;
}
