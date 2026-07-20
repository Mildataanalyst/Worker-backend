'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import WorkstreamPanel from './WorkstreamPanel';
import AdminUndoRedo from '@/components/AdminUndoRedo';
import { DEFAULT_DASHBOARD_DATA, PM_PROFILES } from '@/lib/progressData';
import { safeExternalUrl } from '@/lib/urlSafety';
import { BACKEND, safeJSON } from '@/lib/backendClient';

type AnyObj = Record<string, any>;

const blankNgo = { name: '', location: '', type: '', summary: '', url: '' };
const blankSector = {
  registeredScanned: 0,
  noOfficialWebsite: 0,
  noOfficialWebsitePct: 0,
  wrongWebsite: 0,
  wrongWebsitePct: 0,
  unreachable: 0,
  unreachablePct: 0,
  enoughPublicInfo: 0,
  enoughPublicInfoPct: 0,
  metroSkewText: ''
};

const stateOptions = [
  'Karnataka',
  'Andhra Pradesh',
  'Arunachal Pradesh',
  'Assam',
  'Bihar',
  'Chhattisgarh',
  'Goa',
  'Gujarat',
  'Haryana',
  'Himachal Pradesh',
  'Jharkhand',
  'Kerala',
  'Madhya Pradesh',
  'Maharashtra',
  'Manipur',
  'Meghalaya',
  'Mizoram',
  'Nagaland',
  'Odisha',
  'Punjab',
  'Rajasthan',
  'Sikkim',
  'Tamil Nadu',
  'Telangana',
  'Tripura',
  'Uttar Pradesh',
  'Uttarakhand',
  'West Bengal',
  'Andaman and Nicobar Islands',
  'Chandigarh',
  'Dadra and Nagar Haveli and Daman and Diu',
  'Delhi',
  'Jammu and Kashmir',
  'Ladakh',
  'Lakshadweep',
  'Puducherry'
];

function clone<T>(v: T): T { return JSON.parse(JSON.stringify(v)); }
function n(v: any) { const x = Number(v); return Number.isFinite(x) ? x : 0; }
function fmt(v: any) { return n(v).toLocaleString('en-IN'); }
function pct(v: any) { const x = Number(v); return Number.isFinite(x) ? `${x}%` : '0%'; }
function openUrl(url: any) { const safe = safeExternalUrl(url); if (safe) window.open(safe, '_blank', 'noopener,noreferrer'); }
function emptyState() {
  return {
    sheetLink: '',
    totalScanned: 0,
    shortlisted: 0,
    finalShortlist: 0,
    rejected: 0,
    funnelScanned: 0,
    funnelReview: 0,
    funnelFinal: 0,
    sector: clone(blankSector),
    ngos: []
  };
}
function normaliseState(raw: any, fallback: any) {
  const next = { ...clone(fallback), ...(raw || {}) };
  next.sheetLink = next.sheetLink || '';
  next.totalScanned = n(next.totalScanned);
  next.shortlisted = n(next.shortlisted);
  next.finalShortlist = n(next.finalShortlist);
  next.rejected = n(next.rejected);
  next.funnelScanned = n(next.funnelScanned);
  next.funnelReview = n(next.funnelReview);
  next.funnelFinal = n(next.funnelFinal);
  next.sector = { ...clone(blankSector), ...(next.sector || {}) };
  next.ngos = Array.isArray(next.ngos) ? next.ngos.slice(0, 12).map((ngo: any) => ({ ...blankNgo, ...(ngo || {}) })) : [];
  while (next.ngos.length < 8) next.ngos.push({ ...blankNgo });
  return next;
}
function ensureData(input: any) {
  const base: any = clone(DEFAULT_DASHBOARD_DATA);
  const data: any = input && input.states ? clone(input) : base;
  data.states = data.states || {};
  data.states.Karnataka = normaliseState(data.states.Karnataka || base.states.Karnataka, base.states.Karnataka);
  for (const st of stateOptions) {
    data.states[st] = normaliseState(data.states[st], st === 'Karnataka' ? base.states.Karnataka : emptyState());
  }
  return data;
}

function LineIcon({ kind }: { kind: 'users' | 'globe' | 'file' | 'eye' | 'city' | 'chart' | 'leaf' | 'health' | 'people' | 'network' | 'award' }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.9, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  const paths: Record<string, React.ReactNode> = {
    users: <><path {...common} d="M17 21a6 6 0 1 0 0-12 6 6 0 0 0 0 12Z"/><path {...common} d="M7 36c1.7-6.7 5.3-10 10-10s8.3 3.3 10 10"/><path {...common} d="M31 20a4.8 4.8 0 1 0-1.3-9.4"/><path {...common} d="M30 27c3.8.8 6.4 3.6 7.7 8.5"/></>,
    globe: <><circle {...common} cx="22" cy="22" r="15"/><path {...common} d="M7 22h30M22 7c4 4.3 6 9.3 6 15s-2 10.7-6 15M22 7c-4 4.3-6 9.3-6 15s2 10.7 6 15"/></>,
    file: <><path {...common} d="M13 7h15l6 6v24H13Z"/><path {...common} d="M28 7v8h8M18 21h12M18 27h12M18 33h7"/></>,
    eye: <><path {...common} d="M5 22s6-10 17-10 17 10 17 10-6 10-17 10S5 22 5 22Z"/><circle {...common} cx="22" cy="22" r="5"/></>,
    city: <><path {...common} d="M10 36V18h8v18M18 36V10h10v26M28 36V22h8v14"/><path {...common} d="M13 23h2M13 28h2M22 16h2M22 21h2M22 26h2M31 27h2"/></>,
    chart: <><path {...common} d="M9 35V23h6v12M19 35V15h6v20M29 35V9h6v26"/><path {...common} d="M8 12l10-7 8 6 10-9"/></>,
    leaf: <><path {...common} d="M23 36c-8-2-13-7-13-15C19 21 25 15 33 8c3 14-1 24-10 28Z"/><path {...common} d="M14 29c5-2 10-7 15-15"/></>,
    health: <><path {...common} d="M22 8v28M8 22h28"/><path {...common} d="M15 10h14a3 3 0 0 1 3 3v18a3 3 0 0 1-3 3H15a3 3 0 0 1-3-3V13a3 3 0 0 1 3-3Z"/></>,
    people: <><circle {...common} cx="15" cy="16" r="5"/><circle {...common} cx="29" cy="16" r="5"/><path {...common} d="M5 35c1.4-6 5-9 10-9s8.6 3 10 9M20 35c1.2-5.2 4.2-8 9-8 4.2 0 7.3 2.5 9 8"/></>,
    network: <><circle {...common} cx="10" cy="22" r="4"/><circle {...common} cx="22" cy="10" r="4"/><circle {...common} cx="34" cy="22" r="4"/><circle {...common} cx="22" cy="34" r="4"/><path {...common} d="M13 19l6-6M25 13l6 6M31 25l-6 6M19 31l-6-6"/></>,
    award: <><circle {...common} cx="22" cy="17" r="9"/><path {...common} d="m16 25-3 12 9-5 9 5-3-12"/></>
  };
  return <svg viewBox="0 0 44 44" aria-hidden="true">{paths[kind]}</svg>;
}
function EduIcon({ i }: { i: number }) {
  const kinds: Array<'leaf' | 'health' | 'people' | 'network' | 'award' | 'globe'> = ['leaf', 'health', 'people', 'network', 'award', 'globe'];
  return <span className="ngo-symbol"><LineIcon kind={kinds[i % kinds.length]} /></span>;
}
const PM_TASKS = [
  { name: 'Kamran', rank: '1', title: 'First to complete his allotted shortlist.', text: 'Connected with the Logs team to source strong NGO leads in Karnataka.' },
  { name: 'Avika', rank: '2', title: 'Second to complete her allotted shortlist.', text: 'Connected with the U&I network to source prospective NGO leads.' },
  { name: 'Rachit', rank: '', title: 'Connected with Karnataka supply teams', text: 'and surfaced several high-quality NGO leads through that network.' },
  { name: 'Milan', rank: '', title: 'Focused on DFP partner NGOs', text: 'and identified leads from trusted partner recommendations.' },
  { name: 'Piyush', rank: '', title: 'Connected with the CD team', text: 'to source and verify NGO leads.' },
  { name: 'Ipshita', rank: '', title: 'Reached out to TFI folks', text: 'to explore potential NGO leads.' }
];
function Trophy({ rank }: { rank: string }) {
  return <span className="pm-rank"><span className="pm-crown"><LineIcon kind="award" /></span>{rank}</span>;
}
function Field({ label, value, onChange, type = 'text' }: { label: string; value: any; onChange: (v: any) => void; type?: string }) {
  return <label className="admin-field"><span>{label}</span><input type={type} value={value ?? ''} onChange={e => onChange(type === 'number' ? Number(e.target.value) : e.target.value)} /></label>;
}


function pick(row: any, ...keys: string[]) {
  for (const key of keys) {
    const value = row?.[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return '';
}
function asRows(value: any): any[] {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'object') return [];
  if (Array.isArray(value.rows)) return value.rows;
  if (Array.isArray(value.items)) return value.items;
  if (Array.isArray(value.data)) return value.data;
  return [];
}
function groupsFromCompiled(data: any) {
  const grouped = data?.grouped_by_rating || data?.groups || {};
  return ['5','4','3','2','1','pending'].map(key => ({ key, rows: asRows(grouped[key]) }));
}
function finalBucketKey(value: any) {
  const raw = String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (['highest_transformation_potential','transformation_potential','transformation','high_transformation','final_shortlist','shortlist','final_output'].includes(raw)) return 'highest_transformation_potential';
  if (['great_ngos','great_ngo','great','strong_maybe','maybe'].includes(raw)) return 'great_ngos';
  if (['needs_more_context','worth_looking','needs_followup','needs_follow_up','follow_up','hold','rejected','reject'].includes(raw)) return 'needs_more_context';
  return 'needs_more_context';
}
function groupsFromFinal(data: any) {
  const labels: Record<string, string> = {
    highest_transformation_potential: 'Highest Transformation Potential',
    great_ngos: 'Great NGOs',
    needs_more_context: 'Worth a Closer Look',
  };
  const notes: Record<string, string> = {
    highest_transformation_potential: 'Institutions that appear most capable of fundamentally changing long-term life outcomes.',
    great_ngos: 'Strong organisations with clear relevance for DFP outreach.',
    needs_more_context: 'Relevant organisations that may be worth outreach after a little more context.',
  };
  const order = ['highest_transformation_potential','great_ngos','needs_more_context'];
  const grouped = data?.grouped_by_bucket || data?.grouped || data?.buckets || {};
  const rows = asRows(data?.rows);
  const buckets: Record<string, any[]> = Object.fromEntries(order.map(k => [k, []]));
  if (rows.length) {
    for (const row of rows) {
      const key = finalBucketKey(pick(row, 'effective_bucket', 'final_bucket', 'bucket'));
      (buckets[key] ||= []).push(row);
    }
  } else {
    const aliasMap: Record<string, string> = {
      final_shortlist: 'highest_transformation_potential',
      shortlist: 'highest_transformation_potential',
      highest_transformation_potential: 'highest_transformation_potential',
      transformation_potential: 'highest_transformation_potential',
      strong_maybe: 'great_ngos',
      great_ngos: 'great_ngos',
      needs_follow_up: 'needs_more_context',
      needs_followup: 'needs_more_context',
      hold: 'needs_more_context',
      reject: 'needs_more_context',
      rejected: 'needs_more_context',
    };
    for (const [rawKey, value] of Object.entries(grouped)) {
      const key = aliasMap[String(rawKey)] || finalBucketKey(rawKey);
      buckets[key] = [...(buckets[key] || []), ...asRows(value)];
    }
  }
  return order.map(key => ({ key, label: labels[key], note: notes[key], rows: buckets[key] || [] }));
}
function CombinedReviewPanel({ data, loading, error }: { data: any; loading: boolean; error: string }) {
  type SortKey = 'combined_score' | 'child_progression' | 'learning_model' | 'development_ecosystem' | 'ngo_name';
  const [sortKey, setSortKey] = useState<SortKey>('combined_score');
  const [sortDirection, setSortDirection] = useState<'desc' | 'asc'>('desc');
  const rows = asRows(data?.rows);
  const sortedRows = useMemo(() => [...rows].sort((left: any, right: any) => {
    if (sortKey === 'ngo_name') {
      const result = String(pick(left, 'ngo_name', 'name')).localeCompare(String(pick(right, 'ngo_name', 'name')));
      return sortDirection === 'asc' ? result : -result;
    }
    const result = n(right?.[sortKey]) - n(left?.[sortKey]);
    return sortDirection === 'desc' ? result : -result;
  }), [rows, sortKey, sortDirection]);
  const averageScore = n(data?.average_combined_score);

  function chooseSort(next: SortKey) {
    if (next === sortKey) setSortDirection(current => current === 'desc' ? 'asc' : 'desc');
    else {
      setSortKey(next);
      setSortDirection(next === 'ngo_name' ? 'asc' : 'desc');
    }
  }
  function sortLabel(key: SortKey, label: string) {
    return <button type="button" className={sortKey === key ? 'active' : ''} onClick={() => chooseSort(key)}>{label}{sortKey === key ? (sortDirection === 'desc' ? ' ↓' : ' ↑') : ''}</button>;
  }

  return <section className="review-board combined-minimal-board combined-metric-board">
    {loading && <div className="empty-review">Loading combined shortlisting…</div>}
    {error && <div className="error-box">{error}</div>}
    <div className="combined-metric-summary">
      <div><span>Shortlisted NGOs</span><b>{data?.total_shortlisted ?? rows.length}</b><small>Completed new-metric assessments only</small></div>
      <div><span>Completed assessments</span><b>{data?.completed_assessments ?? rows.length}</b><small>Duplicate NGO reviews are averaged</small></div>
      <div><span>Average combined score</span><b>{averageScore.toFixed(2)}</b><small>{(averageScore * 100).toFixed(1)}% of the 15-point maximum</small></div>
    </div>
    <div className="combined-metric-toolbar">
      <div><h2>Combined shortlisting</h2><p>Combined score = (Child Progression + Learning Model + Development Ecosystem) ÷ 15.</p></div>
      <label><span>Sort by</span><select value={sortKey} onChange={event => { const next = event.target.value as SortKey; setSortKey(next); setSortDirection(next === 'ngo_name' ? 'asc' : 'desc'); }}><option value="combined_score">Highest combined score</option><option value="child_progression">Highest child progression</option><option value="learning_model">Highest learning model</option><option value="development_ecosystem">Highest development ecosystem</option><option value="ngo_name">NGO name</option></select></label>
    </div>
    <div className="combined-metric-table-wrap">
      <table className="combined-metric-table">
        <thead><tr><th>#</th><th>NGO</th><th>{sortLabel('child_progression', 'Child progression')}</th><th>{sortLabel('learning_model', 'Learning model')}</th><th>{sortLabel('development_ecosystem', 'Development ecosystem')}</th><th>{sortLabel('combined_score', 'Combined score')}</th><th>Website</th></tr></thead>
        <tbody>
          {sortedRows.length ? sortedRows.map((row: any, index: number) => {
            const website = safeExternalUrl(pick(row, 'website', 'Website'));
            const reviewers = Array.isArray(row.reviewers) ? row.reviewers.join(', ') : String(row.reviewer || '');
            const points = n(row.combined_points);
            const combined = n(row.combined_score);
            return <tr key={pick(row, 'ngo_ref', 'id', 'ngo_name', 'name') || index}>
              <td className="combined-rank-cell">{index + 1}</td>
              <td><b>{pick(row, 'ngo_name', 'NGO Name', 'name') || 'Untitled NGO'}</b><small>{reviewers ? `Reviewed by ${reviewers}` : 'New metric assessment'}{n(row.assessment_count) > 1 ? ` · ${n(row.assessment_count)} assessments averaged` : ''}</small></td>
              <td><strong>{n(row.child_progression).toFixed(2)}</strong><small>/ 5</small></td>
              <td><strong>{n(row.learning_model).toFixed(2)}</strong><small>/ 5</small></td>
              <td><strong>{n(row.development_ecosystem).toFixed(2)}</strong><small>/ 5</small></td>
              <td className="combined-score-cell"><strong>{combined.toFixed(2)}</strong><small>{points.toFixed(2)}/15 · {(combined * 100).toFixed(1)}%</small></td>
              <td>{website ? <a href={website} target="_blank" rel="noreferrer">Open website ↗</a> : <span>—</span>}</td>
            </tr>;
          }) : <tr><td colSpan={7}><div className="empty-review">No completed three-metric shortlist assessments yet.</div></td></tr>}
        </tbody>
      </table>
    </div>
  </section>;
}

function defaultFinalCopy(groups: Array<{key:string; label:string; note:string; rows:any[]}>) {
  const base: Record<string, { label: string; note: string }> = {};
  for (const group of groups) base[group.key] = { label: group.label, note: group.note };
  return {
    title: 'Final Ranking',
    subtitle: 'A curated view of the NGOs most worth moving forward.',
    buckets: base,
  };
}
function compactPreview(value: any, limit = 420) {
  const full = String(value || '').replace(/\s+/g, ' ').trim();
  if (!full) return { preview: '', full: '', shortened: false };
  if (full.length <= limit) return { preview: full, full, shortened: false };
  const cut = full.slice(0, limit);
  const safeCut = cut.slice(0, Math.max(cut.lastIndexOf(' '), limit - 48)).trim();
  return { preview: `${safeCut}…`, full, shortened: true };
}

function FinalOutputPanel({
  data,
  loading,
  error,
  region,
  onBack,
  onRestored,
}: {
  data: any;
  loading: boolean;
  error: string;
  region: string;
  onBack: () => void;
  onRestored: () => void;
}) {
  const groups = groupsFromFinal(data);
  const [trackerMessage, setTrackerMessage] = useState('');
  const [inDepthOpen, setInDepthOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [expandedBuckets, setExpandedBuckets] = useState<Record<string, boolean>>({});
  const [copy, setCopy] = useState(() => defaultFinalCopy(groups));

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem('dfp2_final_ranking_copy');
      if (saved) {
        const parsed = JSON.parse(saved);
        setCopy({ ...defaultFinalCopy(groups), ...parsed, buckets: { ...defaultFinalCopy(groups).buckets, ...(parsed?.buckets || {}) } });
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateCopy(next: any) {
    setCopy(next);
    try { window.localStorage.setItem('dfp2_final_ranking_copy', JSON.stringify(next)); } catch {}
  }

  async function sendBucketToTracker(bucket: string) {
    if (!BACKEND) { setTrackerMessage('Backend URL is not configured.'); return; }
    setTrackerMessage('Sending to tracker…');
    const r = await safeJSON(`${BACKEND}/ranking/final/send-to-contact-tracker`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ region, buckets: [bucket] })
    });
    if (!r.ok) { setTrackerMessage(r.error || 'Could not send to tracker.'); return; }
    setTrackerMessage(`Tracker updated: ${r.data?.sent_count ?? r.data?.created ?? 0} sent, ${r.data?.skipped_existing_count ?? r.data?.skipped ?? 0} skipped.`);
  }

  function bucketSendKey(key: string) {
    if (key === 'highest_transformation_potential') return 'shortlist';
    return key;
  }

  return <section className="final-ranking-board final-ranking-editorial final-ranking-v109">
    <div className="final-commandbar">
      <div className="final-command-leading">
        <button className="quiet-btn final-back-btn" onClick={onBack}>← Back</button>
        <span className="final-state-pill">{region}</span>
      </div>

      <AdminUndoRedo
        region={region}
        context="Final ranking / tracker recovery"
        onRestored={onRestored}
      />

      <div className="final-command-special">
        <button className="gold-in-depth-btn" onClick={() => setInDepthOpen(true)}>
          <span className="gold-spark" aria-hidden="true">✦</span>
          In-depth
        </button>
        <button className="final-gear-btn" onClick={() => setSettingsOpen(true)} aria-label="Edit final ranking text">⚙</button>
      </div>
    </div>

    {trackerMessage && <div className="pool-message">{trackerMessage}</div>}
    {loading && <div className="empty-review">Loading final ranking…</div>}
    {error && <div className="error-box">{error}</div>}

    <div className="final-ranking-buckets final-ranking-buckets-vertical">
      {groups.map((bucket, bucketIndex) => {
        const visibleRows = expandedBuckets[bucket.key] ? bucket.rows : bucket.rows.slice(0, 4);
        const title = copy.buckets?.[bucket.key]?.label || bucket.label;
        const note = copy.buckets?.[bucket.key]?.note || bucket.note;
        return <section className={`final-rank-bucket final-rank-${bucket.key} final-rank-tier-clean`} key={bucket.key}>
          <header className="final-tier-header">
            <div className="final-tier-heading">
              <span className="final-tier-index">{String(bucketIndex + 1).padStart(2, '0')}</span>
              <div>
                <h3>{title}</h3>
                <p>{note}</p>
              </div>
            </div>
            <div className="final-tier-controls">
              <span className="final-tier-count">{bucket.rows.length} {bucket.rows.length === 1 ? 'NGO' : 'NGOs'}</span>
              <button
                className={bucket.key === 'highest_transformation_potential' ? 'primary-red small-red' : 'quiet-btn'}
                onClick={() => sendBucketToTracker(bucketSendKey(bucket.key))}
              >
                Send tier
              </button>
            </div>
          </header>

          <div className="final-rank-list final-rank-list-clean">
            {bucket.rows.length ? visibleRows.map((row:any, i:number) => {
              const website = safeExternalUrl(pick(row,'website','Website'));
              const understanding = compactPreview(pick(row,'one_line_understanding','background','summary','district','source'), 430);
              const finalComment = compactPreview(pick(row,'final_comment','pm_comment','comment','reason'), 280);
              return <article className="final-rank-card final-rank-card-clean" key={pick(row,'ngo_ref','id','ngo_name','name') || i}>
                <div className="final-rank-main">
                  <div className="final-rank-name-row">
                    <h4>{pick(row,'ngo_name','NGO Name','name') || 'Untitled NGO'}</h4>
                    <span className="final-row-number">{String(i + 1).padStart(2, '0')}</span>
                  </div>

                  <p className="final-rank-summary">{understanding.preview || 'No summary available yet.'}</p>

                  {understanding.shortened && <details className="final-fullcopy">
                    <summary>Read full organisation profile</summary>
                    <p>{understanding.full}</p>
                  </details>}

                  <div className="final-rank-meta">
                    {pick(row,'district','District') && <span>{pick(row,'district','District')}</span>}
                    {pick(row,'source_mix','source','Source') && <span>{pick(row,'source_mix','source','Source')}</span>}
                    {website && <span>Website available</span>}
                  </div>
                </div>

                <div className="final-rank-note">
                  <span className="final-note-label">Final PM view</span>
                  <p>{finalComment.preview || 'No final comment.'}</p>
                  {finalComment.shortened && <details className="final-fullcopy final-fullcopy-note">
                    <summary>Read full PM view</summary>
                    <p>{finalComment.full}</p>
                  </details>}
                </div>

                <div className="final-rank-actions">
                  {website ? <a className="quiet-btn" href={website} target="_blank" rel="noreferrer">Website</a> : <span className="quiet-btn disabled">No website</span>}
                  <button className="quiet-btn" onClick={() => sendBucketToTracker(bucketSendKey(bucket.key))}>Send</button>
                </div>
              </article>;
            }) : <div className="empty-review final-empty-tier">
              <span>No NGOs in this tier yet.</span>
            </div>}
          </div>

          {bucket.rows.length > 4 && <div className="final-show-row">
            <button className="quiet-btn" onClick={() => setExpandedBuckets(prev => ({ ...prev, [bucket.key]: !prev[bucket.key] }))}>
              {expandedBuckets[bucket.key] ? 'Show less' : `Show all ${bucket.rows.length}`}
            </button>
          </div>}
        </section>;
      })}
    </div>

    <Link className="final-go-tracker" href="/contact-tracker">Go to Tracker</Link>

    {inDepthOpen && <div className="modal-scrim" onClick={() => setInDepthOpen(false)}>
      <section className="trend-modal in-depth-modal" onClick={e => e.stopPropagation()}>
        <button className="modal-x" onClick={() => setInDepthOpen(false)}>×</button>
        <p className="tracker-kicker compact-kicker">In-depth</p>
        <h2>In-depth analysis</h2>
        <p>This layer is intentionally empty for now. It can later hold the journey, PM evaluation patterns, and what is common across the strongest NGOs.</p>
        <div className="trend-placeholder-grid">
          <div><b>Journey</b><span>How leads moved from discovery into final judgement tiers.</span></div>
          <div><b>PM evaluation</b><span>How reviewers evaluated NGOs across the shortlist.</span></div>
          <div><b>Common patterns</b><span>What is common across the best NGOs.</span></div>
        </div>
      </section>
    </div>}

    {settingsOpen && <div className="modal-scrim" onClick={() => setSettingsOpen(false)}>
      <section className="trend-modal final-settings-modal" onClick={e => e.stopPropagation()}>
        <button className="modal-x" onClick={() => setSettingsOpen(false)}>×</button>
        <p className="tracker-kicker compact-kicker">Gearbox</p>
        <h2>Edit page text</h2>
        <p>These edits are saved in this browser so you can adjust the language while reviewing the final page.</p>
        {groups.map(bucket => <div className="copy-edit-block" key={bucket.key}>
          <label className="admin-field"><span>{bucket.label} title</span><input value={copy.buckets?.[bucket.key]?.label || bucket.label} onChange={e => updateCopy({ ...copy, buckets: { ...copy.buckets, [bucket.key]: { ...(copy.buckets?.[bucket.key] || {}), label: e.target.value } } })} /></label>
          <label className="admin-field"><span>{bucket.label} description</span><input value={copy.buckets?.[bucket.key]?.note || bucket.note} onChange={e => updateCopy({ ...copy, buckets: { ...copy.buckets, [bucket.key]: { ...(copy.buckets?.[bucket.key] || {}), note: e.target.value } } })} /></label>
        </div>)}
        <div className="button-row"><button className="quiet-btn" onClick={() => { const next = defaultFinalCopy(groups); updateCopy(next); }}>Reset text</button><button className="primary-red small-red" onClick={() => setSettingsOpen(false)}>Done</button></div>
      </section>
    </div>}
  </section>;
}


export default function ProgressClient({ initialData }: { initialData: AnyObj }) {
  const [data, setData] = useState<any>(() => ensureData(initialData));
  const [view, setView] = useState<'hub' | 'team' | 'pm' | 'combined' | 'final'>('hub');
  const [state, setState] = useState('');
  const [configOpen, setConfigOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [sectorOpen, setSectorOpen] = useState(false);
  const [selectedPM, setSelectedPM] = useState<any>(null);
  const [compiledReview, setCompiledReview] = useState<any>(null);
  const [finalBoard, setFinalBoard] = useState<any>(null);
  const [rankingLoading, setRankingLoading] = useState(false);
  const [rankingError, setRankingError] = useState('');
  const [restoreTick, setRestoreTick] = useState(0);
  const [password, setPassword] = useState('');
  const [msg, setMsg] = useState('');
  const [draft, setDraft] = useState<any>(() => ensureData(initialData));
  const ngoCarouselRef = useRef<HTMLDivElement | null>(null);

  const current = state ? data.states?.[state] : null;
  const draftState = state ? draft.states?.[state] : null;
  const funnelRejected = current ? Math.max(0, n(current.funnelScanned || current.totalScanned) - n(current.funnelReview || current.shortlisted)) : 0;
  const keepRate = current && n(current.funnelScanned || current.totalScanned)
    ? ((n(current.funnelReview || current.shortlisted) / n(current.funnelScanned || current.totalScanned)) * 100).toFixed(1)
    : '0.0';
  const sectorRows = current ? [
    { key: 'no-official', label: 'No official website', count: current.sector.noOfficialWebsite, share: current.sector.noOfficialWebsitePct },
    { key: 'wrong-site', label: 'Wrong / mismatched website', count: current.sector.wrongWebsite, share: current.sector.wrongWebsitePct },
    { key: 'unreachable', label: 'Website unreachable', count: current.sector.unreachable, share: current.sector.unreachablePct },
    { key: 'enough-info', label: 'Enough public information to understand', count: current.sector.enoughPublicInfo, share: current.sector.enoughPublicInfoPct }
  ] : [];

  function updateDraft(path: string[], value: any) {
    setDraft((old: any) => {
      const next = clone(old);
      let cur = next;
      for (let i = 0; i < path.length - 1; i++) { cur[path[i]] = cur[path[i]] || {}; cur = cur[path[i]]; }
      cur[path[path.length - 1]] = value;
      return next;
    });
  }
  function updateNgo(idx: number, key: string, value: any) {
    setDraft((old: any) => {
      const next = clone(old);
      const arr = next.states[state].ngos || [];
      while (arr.length <= idx) arr.push({ ...blankNgo });
      arr[idx] = { ...(arr[idx] || blankNgo), [key]: value };
      next.states[state].ngos = arr;
      return next;
    });
  }
  function addNgoCard() {
    setDraft((old: any) => {
      const next = clone(old);
      next.states[state].ngos = next.states[state].ngos || [];
      if (next.states[state].ngos.length < 12) next.states[state].ngos.push({ ...blankNgo });
      return next;
    });
  }
  function applyLocal() { setData(clone(draft)); setMsg('Applied locally. Publish to make it visible to everyone.'); }
  function showProgressDetails() {
    setDetailsOpen(true);
    window.setTimeout(() => document.getElementById('progress-details')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
  }
  function openShortlistSheet() {
    const safe = safeExternalUrl(current?.sheetLink);
    if (safe) {
      window.open(safe, '_blank', 'noopener,noreferrer');
      return;
    }
    setMsg('Add the shortlisted NGO Excel / Google Sheet link from the gear panel, then publish.');
    window.alert('No shortlisted NGO sheet link is published yet. Add it from the gear panel.');
  }
  async function publish() {
    setMsg('Publishing…');
    try {
      const res = await fetch('/api/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password, data: draft }) });
      const json = await res.json();
      if (!res.ok || !json.ok) { setMsg(json.error || `Server error ${res.status}`); return; }
      setData(ensureData(json.data)); setDraft(ensureData(json.data)); setMsg('Published.');
    } catch (err: any) { setMsg(err?.message || 'Publish failed.'); }
  }
  const visibleNgos = useMemo(() => current?.ngos?.filter((x: any) => x.name || x.summary || x.location)?.slice(0, 12) || [], [current]);
  useEffect(() => {
    if (!state || (view !== 'combined' && view !== 'final')) return;
    let cancelled = false;
    async function loadRankingBoards() {
      if (!BACKEND) return;
      setRankingLoading(true);
      setRankingError('');
      try {
        if (view === 'combined') {
          const r = await safeJSON(`${BACKEND}/ranking/compiled-review?region=${encodeURIComponent(state)}`);
          if (!cancelled) {
            if (r.ok) setCompiledReview(r.data || {});
            else setRankingError(r.error || 'Could not load combined review.');
          }
        } else {
          const r = await safeJSON(`${BACKEND}/ranking/final-board`);
          if (!cancelled) {
            if (r.ok) setFinalBoard(r.data || {});
            else setRankingError(r.error || 'Could not load final output.');
          }
        }
      } finally {
        if (!cancelled) setRankingLoading(false);
      }
    }
    loadRankingBoards();
    return () => { cancelled = true; };
  }, [state, view, restoreTick]);
  function scrollNgoCarousel(dir: -1 | 1) {
    const el = ngoCarouselRef.current;
    if (!el) return;
    const step = Math.min(480, Math.max(330, el.clientWidth * 0.42));
    el.scrollBy({ left: dir * step, behavior: 'smooth' });
  }

  const ngoEditCount = Math.max(8, draftState?.ngos?.length || 0);
  const rankingHeaderTitle = view === 'final' ? 'Final Ranking' : view === 'combined' ? 'Combined Shortlisting' : view === 'pm' ? 'PM Shortlists' : 'Ranking Center';
  const rankingHeaderSubtitle = view === 'final'
    ? 'A curated view of the NGOs most worth moving forward.'
    : view === 'combined'
      ? 'Compare completed three-metric assessments and sort by the strongest combined or individual metric scores.'
      : view === 'pm'
        ? 'Open reviewer shortlists and complete one NGO at a time.'
        : 'Select a state, review PM shortlists, combine ratings, and finalise the best NGOs.';

  return <div className="progress-final">
    <main className="dfp-wrap page-stack progress-stack">
      <section className="state-gate-card ranking-state-gate">
        <div>
          <p className="tracker-kicker compact-kicker">Part 02 · Rankings</p>
          <h2>{rankingHeaderTitle}</h2>
          <small>{rankingHeaderSubtitle}</small>
        </div>
        <label>
          <span>State / UT</span>
          <select value={state} onChange={e => { setState(e.target.value); setDraft(ensureData(data)); setDetailsOpen(false); setView('hub'); }}>
            <option value="">Select state</option>{stateOptions.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      </section>

      {!state && <section className="empty-progress"><div><div className="empty-icon">▥</div><p>Select a state to view rankings.</p></div></section>}

      {state && view === 'hub' && <section className="source-choice-grid three-choice ranking-journey-grid">
        <button className="source-choice-card" onClick={() => setView('pm')}><span>01</span><b>PM Shortlists</b><small>Open reviewer shortlists and complete one NGO at a time.</small></button>
        <button className="source-choice-card" onClick={() => setView('combined')}><span>02</span><b>Combined Shortlisting</b><small>Rank completed shortlists by combined score or any of the three new metrics.</small></button>
        <button className="source-choice-card leadpool-entry" onClick={() => setView('final')}><span>03</span><b>Final Ranking</b><small>View the final buckets and move selected NGOs to the tracker.</small></button>
      </section>}

      {state && view === 'team' && current && <>
        <section className="team-metrics">
          <div className="metric-card large"><span>Total NGOs scanned</span><strong>{fmt(current.totalScanned)}</strong><small>Across {state}</small><button onClick={showProgressDetails}>Know more →</button></div>
          <div className="metric-card large shortlisted-metric"><span>Shortlisted NGOs</span><strong>{fmt(current.shortlisted)}</strong><small>Review-ready NGOs</small><button onClick={openShortlistSheet}>Know more →</button></div>
        </section>

        {visibleNgos.length > 0 && <section className="shortlist-carousel-card base-shortlist" aria-label="Shortlisted NGO carousel">
          <div className="overview-head carousel-head"><div><h2>Shortlisted NGOs overview</h2><p>Swipe horizontally or use the arrows to move through the shortlist.</p></div><div className="small-arrows"><button aria-label="Previous NGO" onClick={() => scrollNgoCarousel(-1)}>←</button><button aria-label="Next NGO" onClick={() => scrollNgoCarousel(1)}>→</button></div></div>
          <div className="ngo-carousel" ref={ngoCarouselRef}>
            {visibleNgos.map((ngo: any, i: number) => <article className={i === 1 ? 'ngo-tile selected' : 'ngo-tile'} key={`${ngo.name}-${i}`}>
              <span className="idx">{String(i + 1).padStart(2, '0')}</span><EduIcon i={i}/>
              <h3>{ngo.name || `NGO ${i + 1}`}</h3><p className="meta">{[ngo.location, ngo.type].filter(Boolean).join(' · ') || 'Location · Type'}</p>
              <p>{ngo.summary || 'Add this NGO’s short description from the configuration panel.'}</p><button onClick={() => openUrl(ngo.url)}>View profile →</button>
            </article>)}
          </div>
        </section>}

        {detailsOpen && <section id="progress-details" className="progress-details">
          <section className="funnel-card funnel-card-polished">
            <div className="funnel-head">
              <div><h2>Shortlisting funnel</h2><p>From discovery to review-ready partners.</p></div>
              <div className="funnel-actions"><button className="info-btn" aria-label="Open sector snapshot" onClick={() => setSectorOpen(true)}>i</button></div>
            </div>
            <div className="funnel-stage-wrap">
              <div className="funnel-stage stage-scan"><span className="stage-icon">◎</span><strong>{fmt(current.funnelScanned)}</strong><span>Scanned NGOs</span></div>
              <div className="funnel-stage stage-review"><span className="stage-icon">☆</span><strong>{fmt(current.funnelReview)}</strong><span>Shortlisted NGOs</span></div>
              <div className="funnel-stage stage-final"><span className="stage-icon">◉</span><strong>{fmt(current.funnelFinal)}</strong><span>Final shortlist</span></div>
            </div>
            <div className="funnel-foot"><span>✕ {fmt(funnelRejected)} NGOs rejected</span><span>↗ Keep rate: {keepRate}%</span></div>
          </section>
          <section className="source-row"><h2>Where leads came from</h2><p>Ecosystem referrals that strengthened the shortlist.</p><div className="source-grid"><div><span className="source-icon"><LineIcon kind="globe" /></span><b>NGO Darpan</b><span>universe scan</span></div><div><span className="source-icon"><LineIcon kind="network" /></span><b>Eternal ecosystem</b><span>Supply / Logs / CD</span></div><div><span className="source-icon"><LineIcon kind="people" /></span><b>DFP NGO</b><span>partner referrals</span></div><div><span className="source-icon"><LineIcon kind="users" /></span><b>External referrals</b><span>e.g. U&I</span></div></div></section>
        </section>}
      </>}


      {state && view === 'pm' && <><div className="source-topline ranking-subtop"><button className="quiet-btn" onClick={() => setView('hub')}>← Back</button><span>PM Shortlists · {state}</span></div><AdminUndoRedo region={state} context="PM shortlisting recovery" onRestored={() => setRestoreTick(x => x + 1)} /><WorkstreamPanel key={restoreTick} stateName={state} /></>}
      {state && view === 'combined' && <><div className="source-topline ranking-subtop"><button className="quiet-btn" onClick={() => setView('hub')}>← Back</button><span>Combined Shortlisting · {state}</span></div><AdminUndoRedo region={state} context="Combined shortlisting recovery" onRestored={() => setRestoreTick(x => x + 1)} /><CombinedReviewPanel data={compiledReview} loading={rankingLoading} error={rankingError} /></>}
      {state && view === 'final' && <FinalOutputPanel
        data={finalBoard}
        loading={rankingLoading}
        error={rankingError}
        region={state}
        onBack={() => setView('hub')}
        onRestored={() => setRestoreTick(x => x + 1)}
      />}

      <footer className="page-foot">For internal use only</footer>
    </main>

    {sectorOpen && current && <div className="modal-scrim" onClick={() => setSectorOpen(false)}>
      <section className="sector-card sector-card-polished" onClick={e => e.stopPropagation()}>
        <button className="modal-x" onClick={() => setSectorOpen(false)}>×</button>
        <div className="sector-hero">
          <div>
            <h2>What does this say about<br/>{state}’s <span>NGO sector?</span></h2>
            <p>An extrapolative read of the organised NGO sector — across child, literacy, health, nutrition and allied work.</p>
          </div>
          <div className="sector-signal-lockup" aria-hidden="true"><div className="signal-orb"><span/><span/><span/></div><p><b>{state}</b><i/>Sector snapshot</p></div>
        </div>
        <div className="sector-stats"><div><span className="sector-stat-icon"><LineIcon kind="users" /></span><b>{fmt(current.sector.registeredScanned)}</b><span>registered organisations scanned</span></div><div><span className="sector-stat-icon"><LineIcon kind="globe" /></span><b>{pct(current.sector.noOfficialWebsitePct)}</b><span>had no official website</span></div><div><span className="sector-stat-icon"><LineIcon kind="file" /></span><b>{pct(current.sector.enoughPublicInfoPct)}</b><span>left enough public information online to understand what they do</span></div></div>
        <div className="sector-bars">
          <div className="sector-chart-block"><h3>How the sector shows up online</h3><div className="bar-graphic">{sectorRows.map(row => <i key={row.key} className={row.key} style={{ width: `${Math.max(3, n(row.share))}%` }} />)}</div></div>
          <table><thead><tr><th>Category</th><th>Organisations</th><th>Share</th></tr></thead><tbody>{sectorRows.map(row => <tr key={row.key} className={row.key}><td><span className="row-dot"/> {row.label}</td><td>{fmt(row.count)}</td><td>{pct(row.share)}</td></tr>)}</tbody></table>
        </div>
        <div className="sector-notes"><div><b>The visible layer is tiny</b><p>Only {fmt(current.sector.enoughPublicInfo)} organisations left enough public evidence to understand online — a very small slice of the registered universe.</p></div><div><b>Visibility is metro-skewed</b><p>{current.sector.metroSkewText || 'Visibility is likely concentrated in districts with stronger digital presence.'}</p></div></div>
        <p className="sector-disclaimer">ⓘ Extrapolation only — not a definitive census of NGO quality or impact. Some false negatives are possible due to weak websites, search mismatches, timeouts, or temporary connection failures. Thin digital visibility does not necessarily mean weak grassroots work.</p>
      </section>
    </div>}

    {selectedPM && <div className="modal-scrim" onClick={() => setSelectedPM(null)}><section className="pm-about-modal" onClick={e => e.stopPropagation()}><button className="modal-x" onClick={() => setSelectedPM(null)}>×</button><div className="pm-about-head"><Image src={selectedPM.img} alt={selectedPM.name} width={104} height={104}/><div><span>{selectedPM.tagline}</span><h2>{selectedPM.name}</h2><p>{selectedPM.role}</p></div></div><p className="pm-about-copy">{selectedPM.about}</p></section></div>}

    {configOpen && state && draftState && <><div className="drawer-scrim" onClick={() => setConfigOpen(false)}/><aside className="config-drawer"><div className="drawer-head"><h2>Configure {state}</h2><button onClick={() => setConfigOpen(false)}>×</button></div><div className="drawer-body">
      <Field label="Shortlisted NGO Excel / Google Sheet link" value={draftState.sheetLink} onChange={v => updateDraft(['states', state, 'sheetLink'], v)}/>
      <div className="two-cols"><Field type="number" label="Total NGOs scanned" value={draftState.totalScanned} onChange={v => updateDraft(['states', state, 'totalScanned'], v)}/><Field type="number" label="Shortlisted NGOs" value={draftState.shortlisted} onChange={v => updateDraft(['states', state, 'shortlisted'], v)}/><Field type="number" label="Funnel scanned" value={draftState.funnelScanned} onChange={v => updateDraft(['states', state, 'funnelScanned'], v)}/><Field type="number" label="Funnel shortlisted" value={draftState.funnelReview} onChange={v => updateDraft(['states', state, 'funnelReview'], v)}/><Field type="number" label="Funnel final" value={draftState.funnelFinal} onChange={v => updateDraft(['states', state, 'funnelFinal'], v)}/><Field type="number" label="Rejected" value={draftState.rejected} onChange={v => updateDraft(['states', state, 'rejected'], v)}/></div>
      <div className="drawer-section-title"><h3>Selected NGOs overview</h3><button type="button" className="add-ngo-btn" onClick={addNgoCard}>+ Add NGO card</button></div>
      {Array.from({ length: ngoEditCount }).map((_, i) => <div className="ngo-edit" key={i}><b>NGO {i + 1}</b><Field label="Name" value={draftState.ngos[i]?.name} onChange={v => updateNgo(i, 'name', v)}/><Field label="Location" value={draftState.ngos[i]?.location} onChange={v => updateNgo(i, 'location', v)}/><Field label="Type / tag" value={draftState.ngos[i]?.type} onChange={v => updateNgo(i, 'type', v)}/><Field label="Subtext" value={draftState.ngos[i]?.summary} onChange={v => updateNgo(i, 'summary', v)}/><Field label="URL" value={draftState.ngos[i]?.url} onChange={v => updateNgo(i, 'url', v)}/></div>)}
      <h3>Sector snapshot</h3>
      <div className="two-cols"><Field type="number" label="Registered organisations scanned" value={draftState.sector.registeredScanned} onChange={v => updateDraft(['states', state, 'sector', 'registeredScanned'], v)}/><Field type="number" label="No official website" value={draftState.sector.noOfficialWebsite} onChange={v => updateDraft(['states', state, 'sector', 'noOfficialWebsite'], v)}/><Field type="number" label="No website %" value={draftState.sector.noOfficialWebsitePct} onChange={v => updateDraft(['states', state, 'sector', 'noOfficialWebsitePct'], v)}/><Field type="number" label="Wrong website" value={draftState.sector.wrongWebsite} onChange={v => updateDraft(['states', state, 'sector', 'wrongWebsite'], v)}/><Field type="number" label="Wrong website %" value={draftState.sector.wrongWebsitePct} onChange={v => updateDraft(['states', state, 'sector', 'wrongWebsitePct'], v)}/><Field type="number" label="Website unreachable" value={draftState.sector.unreachable} onChange={v => updateDraft(['states', state, 'sector', 'unreachable'], v)}/><Field type="number" label="Unreachable %" value={draftState.sector.unreachablePct} onChange={v => updateDraft(['states', state, 'sector', 'unreachablePct'], v)}/><Field type="number" label="Enough public info" value={draftState.sector.enoughPublicInfo} onChange={v => updateDraft(['states', state, 'sector', 'enoughPublicInfo'], v)}/><Field type="number" label="Enough public info %" value={draftState.sector.enoughPublicInfoPct} onChange={v => updateDraft(['states', state, 'sector', 'enoughPublicInfoPct'], v)}/></div>
      <Field label="Metro-skew note" value={draftState.sector.metroSkewText} onChange={v => updateDraft(['states', state, 'sector', 'metroSkewText'], v)}/><Field label="Publish password" value={password} onChange={setPassword}/><p className="drawer-msg">{msg}</p></div><div className="drawer-foot"><button className="ghost-btn" onClick={applyLocal}>Apply locally</button><button className="primary-red" onClick={publish}>Publish</button></div></aside></>}
  </div>;
}
