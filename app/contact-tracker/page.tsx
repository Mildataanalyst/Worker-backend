'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Header from '@/components/Header';
import AdminUndoRedo from '@/components/AdminUndoRedo';
import { BACKEND, safeJSON } from '@/lib/backendClient';
import { safeExternalUrl } from '@/lib/urlSafety';

type Row = Record<string, any>;
type Filter = 'all' | 'ready' | 'needs_review' | 'linkedin_only' | 'no_contact' | 'not_started' | 'connected' | 'meeting_scheduled' | 'meeting_done' | 'follow_up_needed' | 'on_hold';
type Settings = Record<string, any>;

const statusOptions = [
  ['not_started', 'Not Started'],
  ['contacted', 'Contacted'],
  ['connected', 'Connected'],
  ['not_connected', 'Not Connected'],
  ['meeting_scheduled', 'Meeting Scheduled'],
  ['meeting_done', 'Meeting Done'],
  ['follow_up_needed', 'Follow-up Needed'],
  ['not_interested', 'Not Interested'],
  ['on_hold', 'On Hold'],
];

const states = ['Karnataka', 'Tamil Nadu', 'Telangana', 'Andhra Pradesh', 'Maharashtra'];
const labelOf = (value: any) => statusOptions.find(([k]) => k === String(value || ''))?.[1] || 'Not Started';
const safeUrl = (value: any) => safeExternalUrl(value);
const defaultSettings: Settings = {
  sender_name: 'Milan',
  template_name: 'default',
  max_first_wave_emails: 3,
  email_subject_template: 'Quick Feeding India conversation',
  email_body_template: `Hi [contact_name_or_team],

I’m [sender_name] from Feeding India, by Eternal Foundation.

Your organization stood out in our Karnataka review. We went through your public work, especially [website_detail]. [reviewer_line]

Feeding India’s Daily Feeding Program supports child-focused institutions through daily nutritious meals or ration support.

Could we speak for 5 minutes to understand your work and see if there may be a fit? You can also reply here with your current food/ration support needs.

Regards,
[sender_name]`,
  linkedin_template: 'Hi [contact_name_or_team], I’m [sender_name] from Feeding India, by Eternal Foundation. We came across [ngo_name] during our Karnataka review and wanted to understand your work better. Would you be open to a quick 5-minute conversation on possible food/ration support?',
  feeding_india_website: '',
  annual_report_link: '',
  social_links: '',
};

function splitMulti(value: any): string[] {
  return String(value || '')
    .split('|')
    .map(x => x.trim())
    .filter(Boolean);
}
function hasAny(value: any) { return splitMulti(value).length > 0; }
function isTrue(value: any) { return ['1', 'true', 'yes'].includes(String(value || '').toLowerCase()); }
function isReady(row: Row) { return hasAny(row.selected_to_emails || row.all_emails) && !isTrue(row.manual_review_needed); }
function isLinkedinOnly(row: Row) { return !hasAny(row.selected_to_emails || row.all_emails) && (hasAny(row.linkedin_org_urls) || hasAny(row.linkedin_people_urls)); }
function noContact(row: Row) { return !hasAny(row.all_emails) && !hasAny(row.all_phones) && !hasAny(row.linkedin_org_urls) && !hasAny(row.linkedin_people_urls) && !hasAny(row.contact_form_urls); }
function contactCounts(row: Row) {
  return {
    emails: splitMulti(row.all_emails).length,
    phones: splitMulti(row.all_phones).length,
    linkedin: splitMulti(row.linkedin_org_urls).length + splitMulti(row.linkedin_people_urls).length,
    forms: splitMulti(row.contact_form_urls).length,
  };
}

function Stat({ label, value }: { label: string; value: any }) {
  return <div className="tracker-stat"><strong>{value ?? 0}</strong><span>{label}</span></div>;
}

function ContactChips({ row }: { row: Row }) {
  const c = contactCounts(row);
  return <div className="contact-chip-row">
    <span className={c.emails ? 'contact-chip good' : 'contact-chip'}>{c.emails} email{c.emails === 1 ? '' : 's'}</span>
    <span className={c.phones ? 'contact-chip good' : 'contact-chip'}>{c.phones} phone{c.phones === 1 ? '' : 's'}</span>
    <span className={c.linkedin ? 'contact-chip good' : 'contact-chip'}>{c.linkedin} LinkedIn</span>
    <span className={c.forms ? 'contact-chip good' : 'contact-chip'}>{c.forms} form{c.forms === 1 ? '' : 's'}</span>
  </div>;
}

function TextListEditor({ label, value, onChange, hint }: { label: string; value: any; onChange: (v: string) => void; hint?: string }) {
  return <label className="tracker-wide"><span>{label}</span><textarea value={value || ''} onChange={e => onChange(e.target.value)} placeholder={hint || 'Use | between multiple values'} /></label>;
}

function RowDetails({ row, onSave, onGenerate, saving, generating }: { row: Row; onSave: (patch: Row) => void; onGenerate: () => void; saving: boolean; generating: boolean }) {
  const [draft, setDraft] = useState<Row>({});
  useEffect(() => { setDraft({
    poc_name: row.poc_name || '',
    contact_number: row.contact_number || '',
    outreach_owner: row.outreach_owner || '',
    selected_to_emails: row.selected_to_emails || '',
    selected_cc_emails: row.selected_cc_emails || '',
    all_emails: row.all_emails || '',
    selected_phone: row.selected_phone || '',
    all_phones: row.all_phones || row.contact_number || '',
    linkedin_org_urls: row.linkedin_org_urls || '',
    linkedin_people_urls: row.linkedin_people_urls || '',
    contact_form_urls: row.contact_form_urls || '',
    contact_source_urls: row.contact_source_urls || '',
    best_contact_route: row.best_contact_route || '',
    contact_confidence: row.contact_confidence || '',
    contact_confidence_reason: row.contact_confidence_reason || '',
    email_subject: row.email_subject || 'Quick Feeding India conversation',
    email_body: row.email_body || '',
    linkedin_message: row.linkedin_message || '',
    website_detail: row.website_detail || '',
    reviewer_line: row.reviewer_line || '',
    outreach_locked: row.outreach_locked || '',
    manual_review_needed: row.manual_review_needed || '',
    meeting_date: row.meeting_date || '',
    meeting_time: row.meeting_time || '',
    meeting_notes: row.meeting_notes || '',
    next_follow_up_date: row.next_follow_up_date || '',
    tracker_comment: row.tracker_comment || '',
  }); }, [row]);
  const sourceUrls = splitMulti(row.contact_source_urls);
  return <div className="tracker-details contact-details">
    <div className="contact-details-head">
      <div>
        <b>Contact Supporter</b>
        <p>All public routes found are preserved. Edit selected recipients before exporting or sending.</p>
      </div>
      <div className="contact-details-actions">
        <button className="tracker-primary small" disabled={generating} onClick={onGenerate}>{generating ? 'Generating…' : 'Generate / Refresh'}</button>
        <button className="tracker-primary small" disabled={saving} onClick={() => onSave(draft)}>{saving ? 'Saving…' : 'Save edits'}</button>
      </div>
    </div>

    <div className="tracker-detail-grid contact-grid-four">
      <label><span>POC</span><input value={draft.poc_name || ''} onChange={e => setDraft({ ...draft, poc_name: e.target.value })} /></label>
      <label><span>Owner</span><input value={draft.outreach_owner || ''} onChange={e => setDraft({ ...draft, outreach_owner: e.target.value })} /></label>
      <label><span>Best route</span><input value={draft.best_contact_route || ''} onChange={e => setDraft({ ...draft, best_contact_route: e.target.value })} /></label>
      <label><span>Confidence</span><select value={draft.contact_confidence || ''} onChange={e => setDraft({ ...draft, contact_confidence: e.target.value })}><option value="">—</option><option>High</option><option>Medium</option><option>Low</option></select></label>
    </div>

    <div className="tracker-detail-grid">
      <label><span>To emails</span><input value={draft.selected_to_emails || ''} onChange={e => setDraft({ ...draft, selected_to_emails: e.target.value })} placeholder="primary@email.org | second@email.org" /></label>
      <label><span>CC emails</span><input value={draft.selected_cc_emails || ''} onChange={e => setDraft({ ...draft, selected_cc_emails: e.target.value })} placeholder="backup@email.org | info@email.org" /></label>
      <label><span>Selected phone</span><input value={draft.selected_phone || ''} onChange={e => setDraft({ ...draft, selected_phone: e.target.value })} /></label>
    </div>

    <div className="contact-editor-grid">
      <TextListEditor label="All emails found" value={draft.all_emails} onChange={v => setDraft({ ...draft, all_emails: v })} />
      <TextListEditor label="All phones found" value={draft.all_phones} onChange={v => setDraft({ ...draft, all_phones: v })} />
      <TextListEditor label="LinkedIn org URLs" value={draft.linkedin_org_urls} onChange={v => setDraft({ ...draft, linkedin_org_urls: v })} />
      <TextListEditor label="LinkedIn people URLs" value={draft.linkedin_people_urls} onChange={v => setDraft({ ...draft, linkedin_people_urls: v })} />
    </div>

    <TextListEditor label="Contact forms" value={draft.contact_form_urls} onChange={v => setDraft({ ...draft, contact_form_urls: v })} />

    <div className="tracker-detail-grid">
      <label><span>Website detail used</span><input value={draft.website_detail || ''} onChange={e => setDraft({ ...draft, website_detail: e.target.value })} /></label>
      <label><span>Reviewer line used</span><input value={draft.reviewer_line || ''} onChange={e => setDraft({ ...draft, reviewer_line: e.target.value })} /></label>
      <label><span>Confidence reason</span><input value={draft.contact_confidence_reason || ''} onChange={e => setDraft({ ...draft, contact_confidence_reason: e.target.value })} /></label>
    </div>

    <label className="tracker-wide"><span>Email subject</span><input value={draft.email_subject || ''} onChange={e => setDraft({ ...draft, email_subject: e.target.value })} /></label>
    <label className="tracker-wide"><span>Email body</span><textarea className="outreach-body" value={draft.email_body || ''} onChange={e => setDraft({ ...draft, email_body: e.target.value })} /></label>
    <label className="tracker-wide"><span>LinkedIn message</span><textarea value={draft.linkedin_message || ''} onChange={e => setDraft({ ...draft, linkedin_message: e.target.value })} /></label>

    <div className="tracker-detail-grid">
      <label><span>Meeting date</span><input type="date" value={draft.meeting_date || ''} onChange={e => setDraft({ ...draft, meeting_date: e.target.value })} /></label>
      <label><span>Meeting time</span><input type="time" value={draft.meeting_time || ''} onChange={e => setDraft({ ...draft, meeting_time: e.target.value })} /></label>
      <label><span>Next follow-up</span><input type="date" value={draft.next_follow_up_date || ''} onChange={e => setDraft({ ...draft, next_follow_up_date: e.target.value })} /></label>
    </div>
    <label className="tracker-wide"><span>Meeting notes</span><textarea value={draft.meeting_notes || ''} onChange={e => setDraft({ ...draft, meeting_notes: e.target.value })} /></label>
    <label className="tracker-wide"><span>Tracker comment</span><textarea value={draft.tracker_comment || ''} onChange={e => setDraft({ ...draft, tracker_comment: e.target.value })} /></label>

    <div className="contact-lock-row">
      <label><input type="checkbox" checked={isTrue(draft.outreach_locked)} onChange={e => setDraft({ ...draft, outreach_locked: e.target.checked ? 'yes' : '' })} /> Lock outreach text</label>
      <label><input type="checkbox" checked={isTrue(draft.manual_review_needed)} onChange={e => setDraft({ ...draft, manual_review_needed: e.target.checked ? 'yes' : '' })} /> Needs manual review</label>
    </div>

    <div className="tracker-context">
      <p><b>PM</b> {row.pm_reviewer || '—'} · {row.pm_rating || '—'}</p>
      <p><b>PM comment</b> {row.pm_comment || '—'}</p>
      <p><b>Background</b> {row.one_line_understanding || row.background || '—'}</p>
      {row.website && <p><b>Website</b> <a href={safeUrl(row.website) || '#'} target="_blank" rel="noreferrer">{row.website}</a></p>}
      {sourceUrls.length > 0 && <p><b>Sources</b> {sourceUrls.slice(0, 6).map((u, i) => <span key={u}>{i ? ' · ' : ''}<a href={safeUrl(u) || '#'} target="_blank" rel="noreferrer">source {i + 1}</a></span>)}</p>}
    </div>
  </div>;
}

function AdminGear({ settings, setSettings, queryMode, setQueryMode, onImport, importing }: { settings: Settings; setSettings: (s: Settings) => void; queryMode: string; setQueryMode: (s: string) => void; onImport: (file: File) => void; importing: boolean }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Settings>(settings);
  useEffect(() => setDraft(settings), [settings]);
  function save() {
    setSettings(draft);
    try { localStorage.setItem('dfp2-contact-settings', JSON.stringify(draft)); } catch {}
    setOpen(false);
  }
  return <>
    <button className="contact-gear-button" onClick={() => setOpen(true)} title="Contact Supporter settings">⚙</button>
    {open && <div className="contact-gear-overlay">
      <div className="contact-gear-panel">
        <div className="contact-gear-head"><div><b>Contact Supporter Admin</b><p>Edit the whole outreach structure and CSV controls.</p></div><button onClick={() => setOpen(false)}>×</button></div>
        <div className="contact-gear-grid">
          <label><span>Sender name</span><input value={draft.sender_name || ''} onChange={e => setDraft({ ...draft, sender_name: e.target.value })} /></label>
          <label><span>Template name</span><input value={draft.template_name || ''} onChange={e => setDraft({ ...draft, template_name: e.target.value })} /></label>
          <label><span>Query mode</span><select value={queryMode} onChange={e => setQueryMode(e.target.value)}><option value="cheap">Cheap · 2 queries</option><option value="balanced">Balanced · 4 queries</option><option value="deep">Deep · 6 queries</option></select></label>
          <label><span>Max first-wave emails</span><input type="number" min="1" max="8" value={draft.max_first_wave_emails || 3} onChange={e => setDraft({ ...draft, max_first_wave_emails: Number(e.target.value || 3) })} /></label>
        </div>
        <label className="tracker-wide"><span>Subject template</span><input value={draft.email_subject_template || ''} onChange={e => setDraft({ ...draft, email_subject_template: e.target.value })} /></label>
        <label className="tracker-wide"><span>Email body template</span><textarea className="gear-template" value={draft.email_body_template || ''} onChange={e => setDraft({ ...draft, email_body_template: e.target.value })} /></label>
        <label className="tracker-wide"><span>LinkedIn template</span><textarea value={draft.linkedin_template || ''} onChange={e => setDraft({ ...draft, linkedin_template: e.target.value })} /></label>
        <div className="contact-gear-grid">
          <label><span>Feeding India website</span><input value={draft.feeding_india_website || ''} onChange={e => setDraft({ ...draft, feeding_india_website: e.target.value })} /></label>
          <label><span>Annual report</span><input value={draft.annual_report_link || ''} onChange={e => setDraft({ ...draft, annual_report_link: e.target.value })} /></label>
        </div>
        <label className="tracker-wide"><span>Social links</span><input value={draft.social_links || ''} onChange={e => setDraft({ ...draft, social_links: e.target.value })} /></label>
        <div className="contact-variable-box">
          Variables: [ngo_name], [contact_name_or_team], [sender_name], [website_detail], [reviewer_line], [category], [rating], [program_name], [feeding_india_website], [annual_report_link], [social_links]
        </div>
        <div className="contact-import-box">
          <span>Upload NGO input or edited CSV</span>
          <div className="contact-import-actions">
            <a href={BACKEND ? `${BACKEND}/contact-tracker/sample-input.csv` : '#'}>Sample CSV</a>
            <input type="file" accept=".csv,text/csv" disabled={importing} onChange={e => { const file = e.target.files?.[0]; if (file) onImport(file); e.currentTarget.value = ''; }} />
          </div>
        </div>
        <div className="contact-gear-actions"><button onClick={() => setDraft(defaultSettings)}>Reset</button><button className="tracker-primary" onClick={save}>Save settings</button></div>
      </div>
    </div>}
  </>;
}

export default function ContactTrackerPage() {
  const [region, setRegion] = useState('Karnataka');
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [filter, setFilter] = useState<Filter>('all');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState('');
  const [saving, setSaving] = useState('');
  const [generating, setGenerating] = useState('');
  const [queryMode, setQueryMode] = useState('balanced');
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    try { const saved = localStorage.getItem('dfp2-contact-settings'); if (saved) setSettings({ ...defaultSettings, ...JSON.parse(saved) }); } catch {}
  }, []);

  const load = useCallback(async () => {
    if (!BACKEND) return;
    const r = await safeJSON(`${BACKEND}/contact-tracker?region=${encodeURIComponent(region)}`);
    if (r.ok && r.data) { setRows(r.data.rows || []); setSummary(r.data.summary || {}); }
    else setMessage(r.error || 'Could not load tracker.');
  }, [region]);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => rows.filter(r => {
    if (filter === 'all') return true;
    if (filter === 'ready') return isReady(r);
    if (filter === 'needs_review') return isTrue(r.manual_review_needed);
    if (filter === 'linkedin_only') return isLinkedinOnly(r);
    if (filter === 'no_contact') return noContact(r);
    return String(r.contact_status || 'not_started') === filter;
  }), [rows, filter]);

  async function sendBuckets(buckets: string[]) {
    if (!BACKEND) return;
    setBusy(true); setMessage('Sending from Final Output…');
    const r = await safeJSON(`${BACKEND}/ranking/final/send-to-contact-tracker`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region, buckets }),
    });
    setBusy(false);
    if (!r.ok) { setMessage(r.error || 'Could not send to tracker.'); return; }
    setMessage(`${r.data.sent_count || 0} added · ${r.data.skipped_existing_count || 0} already existed.`);
    await load();
  }

  async function updateRow(row: Row, patch: Row) {
    if (!BACKEND) return;
    setSaving(row.tracker_id);
    const r = await safeJSON(`${BACKEND}/contact-tracker/update`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region, tracker_id: row.tracker_id, ...patch }),
    });
    setSaving('');
    if (!r.ok) { setMessage(r.error || 'Could not update tracker row.'); return; }
    setMessage('Saved.');
    await load();
  }

  async function generateRows(target?: Row, force = false) {
    if (!BACKEND) return;
    const label = target?.ngo_name || `${visible.length} visible NGOs`;
    setGenerating(target?.tracker_id || 'visible');
    setMessage(`Generating contacts and outreach for ${label}…`);
    const payload: Row = target ? { region, tracker_id: target.tracker_id, query_mode: queryMode, settings, force } : { region, tracker_ids: visible.map(r => r.tracker_id).filter(Boolean), query_mode: queryMode, settings, force, limit: 25 };
    const r = await safeJSON(`${BACKEND}/contact-tracker/generate-outreach`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    setGenerating('');
    if (!r.ok) { setMessage(r.error || 'Could not generate contact support.'); return; }
    const skipped = (r.data.skipped || []).length;
    const errors = (r.data.errors || []).length;
    setMessage(`${r.data.generated_count || 0} generated${skipped ? ` · ${skipped} skipped` : ''}${errors ? ` · ${errors} errors` : ''}.`);
    await load();
  }

  async function importCsv(file: File) {
    if (!BACKEND) return;
    const fd = new FormData();
    fd.append('file', file);
    setImporting(true); setMessage('Importing NGO/contact CSV…');
    const r = await safeJSON(`${BACKEND}/contact-tracker/import-csv?region=${encodeURIComponent(region)}`, { method: 'POST', body: fd });
    setImporting(false);
    if (!r.ok) { setMessage(r.error || 'Could not import CSV.'); return; }
    setMessage(`${r.data.updated_count || 0} rows imported · ${r.data.appended_count || 0} new NGOs added.`);
    await load();
  }

  async function removeRow(row: Row) {
    if (!BACKEND) return;
    if (!window.confirm(`Remove ${row.ngo_name || 'this NGO'} from Contact Tracker?`)) return;
    const r = await safeJSON(`${BACKEND}/contact-tracker/remove`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region, tracker_id: row.tracker_id }),
    });
    if (!r.ok) { setMessage(r.error || 'Could not remove row.'); return; }
    setMessage('Removed from tracker.');
    await load();
  }

  return <main className="tracker-page">
    <Header active="tracker" />
    <section className="dfp-wrap tracker-shell contact-supporter-shell">
      <div className="tracker-hero">
        <div>
          <p className="tracker-kicker">Part 03 · Contact Supporter</p>
          <h1>Contact and track</h1>
          <p>Find all public contact routes, generate editable email and LinkedIn outreach, then track replies and meetings.</p>
        </div>
        <div className="tracker-actions">
          <select value={region} onChange={e => setRegion(e.target.value)}>{states.map(s => <option key={s}>{s}</option>)}</select>
          <select value={queryMode} onChange={e => setQueryMode(e.target.value)}><option value="cheap">Cheap</option><option value="balanced">Balanced</option><option value="deep">Deep</option></select>
          <label className="tracker-upload-label">
            Upload NGO CSV
            <input type="file" accept=".csv,text/csv" disabled={importing} onChange={e => { const file = e.target.files?.[0]; if (file) importCsv(file); e.currentTarget.value = ''; }} />
          </label>
          <a href={BACKEND ? `${BACKEND}/contact-tracker/sample-input.csv` : '#'}>Sample CSV</a>
          <button disabled={busy} onClick={() => sendBuckets(['final_shortlist'])}>Send Final Shortlist</button>
          <button disabled={busy} onClick={() => sendBuckets(['strong_maybe'])}>Send Strong Maybe</button>
          <button disabled={!!generating || visible.length === 0} onClick={() => generateRows()}>{generating === 'visible' ? 'Generating…' : 'Generate Visible'}</button>
          <a href={BACKEND ? `${BACKEND}/contact-tracker/export.csv?region=${encodeURIComponent(region)}` : '#'}>Export CSV</a>
        </div>
      </div>

      <AdminUndoRedo region={region} context="Contact Tracker recovery" onRestored={load} />

      <div className="tracker-stats contact-stats">
        <Stat label="In tracker" value={summary.total_in_tracker} />
        <Stat label="Emails found" value={summary.emails_found_count} />
        <Stat label="LinkedIn found" value={summary.linkedin_found_count} />
        <Stat label="Ready to email" value={summary.ready_to_email_count} />
        <Stat label="Needs review" value={summary.needs_review_count} />
        <Stat label="Meetings scheduled" value={summary.meeting_scheduled_count} />
      </div>

      <div className="tracker-tabs contact-tabs">
        {(['all','ready','needs_review','linkedin_only','no_contact','not_started','connected','meeting_scheduled','meeting_done','follow_up_needed','on_hold'] as Filter[]).map(k => <button key={k} className={filter === k ? 'active' : ''} onClick={() => setFilter(k)}>{k === 'all' ? 'All' : k === 'ready' ? 'Ready to Email' : k === 'needs_review' ? 'Needs Review' : k === 'linkedin_only' ? 'LinkedIn Only' : k === 'no_contact' ? 'No Contact' : labelOf(k)}</button>)}
      </div>

      {message && <div className="tracker-message">{message}</div>}

      <section className="tracker-table-card">
        <div className="tracker-table-head"><span>{visible.length} NGOs</span><span>{region} · {queryMode} mode</span></div>
        <div className="scroll-table tracker-table-wrap">
          <table className="tracker-table contact-table"><thead><tr><th>NGO</th><th>Contacts</th><th>First wave</th><th>Subject / route</th><th>Status</th><th>Confidence</th><th>Owner</th><th>Actions</th></tr></thead>
          <tbody>{visible.length ? visible.map(row => {
            const url = safeUrl(row.website);
            const firstWave = [row.selected_to_emails, row.selected_cc_emails].filter(Boolean).join(' cc ');
            return <tr key={row.tracker_id} className={isReady(row) ? 'row-ready' : isTrue(row.manual_review_needed) ? 'row-review' : ''}>
              <td><b>{row.ngo_name || '—'}</b><small>{url ? <a href={url} target="_blank" rel="noreferrer">website</a> : 'Needs website/contact'} {isTrue(row.outreach_locked) ? ' · locked' : ''}</small></td>
              <td><ContactChips row={row} /></td>
              <td><small className="contact-route-text">{firstWave || row.selected_phone || row.linkedin_org_urls || row.linkedin_people_urls || '—'}</small></td>
              <td><b className="contact-subject">{row.email_subject || '—'}</b><small>{row.best_contact_route || '—'}</small></td>
              <td><select value={row.contact_status || 'not_started'} onChange={e => updateRow(row, { contact_status: e.target.value })}>{statusOptions.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></td>
              <td><span className={`confidence-pill ${String(row.contact_confidence || '').toLowerCase()}`}>{row.contact_confidence || '—'}</span><small>{isTrue(row.manual_review_needed) ? 'Needs review' : row.contact_generated_at ? 'Generated' : 'Not generated'}</small></td>
              <td>{row.outreach_owner || '—'}</td>
              <td className="tracker-row-actions"><button disabled={generating === row.tracker_id} onClick={() => generateRows(row)}>{generating === row.tracker_id ? 'Wait…' : 'Generate'}</button><button onClick={() => setExpanded(expanded === row.tracker_id ? '' : row.tracker_id)}>{expanded === row.tracker_id ? 'Close' : 'Open'}</button><button onClick={() => removeRow(row)}>Remove</button></td>
            </tr>;
          }) : <tr><td colSpan={8}>No tracker rows yet. Upload an NGO CSV, or send NGOs from Final Output using the buttons above. <a href={BACKEND ? `${BACKEND}/contact-tracker/sample-input.csv` : '#'}>Download sample CSV</a></td></tr>}
          </tbody></table>
        </div>
        {visible.map(row => expanded === row.tracker_id ? <RowDetails key={`details-${row.tracker_id}`} row={row} saving={saving === row.tracker_id} generating={generating === row.tracker_id} onGenerate={() => generateRows(row)} onSave={patch => updateRow(row, patch)} /> : null)}
      </section>
    </section>
    <AdminGear settings={settings} setSettings={setSettings} queryMode={queryMode} setQueryMode={setQueryMode} onImport={importCsv} importing={importing} />
  </main>;
}
