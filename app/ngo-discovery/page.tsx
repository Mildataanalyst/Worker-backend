'use client';

import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import Header from '@/components/Header';
import AdminUndoRedo from '@/components/AdminUndoRedo';
import { safeExternalUrl } from '@/lib/urlSafety';
import { BACKEND, SEARCH_BACKEND, STORY_BACKEND, BACKEND_CONFIG_ERROR, SEARCH_BACKEND_CONFIG_ERROR, STORY_BACKEND_CONFIG_ERROR, safeJSON, safeSearchJSON, safeStoryJSON, isFailureStatus, isTerminalReady } from '@/lib/backendClient';

type AnyRow = Record<string, any>;
type View = 'source' | 'internet' | 'referrals' | 'leadpool';
type Tab = 'general' | 'bulk';
type RunModule = 'discovery' | 'repository' | 'recovery' | 'presence';
type ActiveRun = {
  run_id: string;
  module: RunModule;
  label: string;
  location: string;
  service: string;
  job: AnyRow;
  status: AnyRow;
};


type ArchiveBoundaryProps = { label: string; onRetry: () => void; children: ReactNode };
type ArchiveBoundaryState = { failed: boolean };

class ArchiveListBoundary extends Component<ArchiveBoundaryProps, ArchiveBoundaryState> {
  state: ArchiveBoundaryState = { failed: false };

  static getDerivedStateFromError(): ArchiveBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`Could not render ${this.props.label} archive`, error);
  }

  private retry = () => {
    this.setState({ failed: false });
    this.props.onRetry();
  };

  render() {
    if (this.state.failed) {
      return <div className="muted-empty">One saved run contains malformed display data. <button className="quiet-btn" onClick={this.retry}>Reload history</button></div>;
    }
    return this.props.children;
  }
}

function isRecord(value: unknown): value is AnyRow {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function archiveRowsFromPayload(payload: unknown): AnyRow[] | null {
  if (!isRecord(payload)) return null;
  const nested = isRecord(payload.data) ? payload.data : null;
  const raw = payload.rows ?? payload.runs ?? nested?.rows ?? nested?.runs;
  if (!Array.isArray(raw)) return null;
  return raw.filter(isRecord);
}

function displayScalar(value: unknown, fallback = '—'): string {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value);
  try { return JSON.stringify(value); } catch { return fallback; }
}

function finiteNumber(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatDuration(value: unknown): string {
  const total = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

const RECOVERY_RUN_STORAGE_KEY = 'dfp2:last-recovery-run-id';
const POLL_MS = 2500;
const BULK_MAX = 10000;
const MAX_DISCOVERY_BUDGET = 5500;
const states = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh','Goa','Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka','Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram','Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu','Telangana','Tripura','Uttar Pradesh','Uttarakhand','West Bengal','Andaman and Nicobar Islands','Chandigarh','Dadra and Nagar Haveli and Daman and Diu','Delhi','Jammu and Kashmir','Ladakh','Lakshadweep','Puducherry'
];
const PM_NAMES = ['Milan','Avika','Piyush','Kamran','Ipshita','Rachit','Tanishq'];

const runModes = [
  { key: 'test', label: 'Test Run', budget: 200, note: 'Small run to check the search logic.' },
  { key: 'standard', label: 'Standard Run', budget: 1500, note: 'Serious pass without using the full balance.' },
  { key: 'full', label: 'Full Karnataka Run', budget: 4000, note: 'Deep Karnataka discovery.' },
  { key: 'extended', label: 'Extended Run', budget: 5500, note: 'Maximum Karnataka discovery.' },
  { key: 'custom', label: 'Custom', budget: 200, note: 'Set your own query budget.' },
];

const pathwayOptions = [
  { key: 'residential_life_system', label: 'Residential / life-system', note: 'Residential schools, hostels, children’s homes, and full-life support institutions.' },
  { key: 'full_day_alternative', label: 'Whole-child / alternative education', note: 'Full-day learning models, bridge schools, alternate schools, and deep education pathways.' },
  { key: 'child_protection_rehab', label: 'Child protection / rehabilitation', note: 'Child labour, street children, rescue, rehabilitation, shelter, and protection pathways.' },
  { key: 'disability_special_needs', label: 'Disability / special needs', note: 'Special schools, therapy-linked education, inclusive support, and disability-focused institutions.' },
  { key: 'sports_arts_stem_vocational', label: 'Sports / arts / STEM / niche pathways', note: 'Distinctive sports, music, arts, STEM, vocational, or talent-building programs.' },
  { key: 'exceptional_community_pathway', label: 'Exceptional community pathway', note: 'Unusual community institutions with credible child outcomes and strong local signal.' },
];
const defaultPathways = pathwayOptions.slice(0, 5).map(p => p.key);

function field(row: AnyRow, ...keys: string[]) {
  for (const k of keys) if (row?.[k] !== undefined && row?.[k] !== null && row?.[k] !== '') return row[k];
  return '';
}
const rowName = (r: AnyRow) => field(r, 'ngo_name', 'NGO Name', 'Organisation', 'organization', 'name', 'input_name');
const rowWebsite = (r: AnyRow) => field(r, 'website', 'Website', 'url', 'Website / Source', 'Source URL');
const rowSource = (r: AnyRow) => field(r, 'source_type', 'Source', 'module', 'Website / Source', 'Source URL', 'Article URL');
const rowLocation = (r: AnyRow) => field(r, 'district', 'District', 'Location', 'Traced Place', 'state', 'State', 'location');
const rowPathway = (r: AnyRow) => field(r, 'Pathway', 'Story Category', 'Story Type', 'pathway');
const rowWhy = (r: AnyRow) => field(r, 'evidence_summary', 'Why It Belongs', 'Why NGO Is Interesting', 'Story Summary', 'Notes', 'note', 'notes');
const rowConfidence = (r: AnyRow) => field(r, 'confidence', 'Confidence', 'AI Confidence', 'conf');
const rowStatus = (r: AnyRow) => field(r, 'curation_status', 'Curation Status', 'information_status', 'Information Status', 'Output Tier', 'Status', 'Repository Status', 'status');
const rowInfoStatus = (r: AnyRow) => field(r, 'information_status', 'Information Status', 'Output Tier', 'Status', 'Repository Status', 'status');
const rowOneLine = (r: AnyRow) => field(r, 'one_line_understanding', 'One-line Understanding', 'background_summary', 'Background', 'evidence_summary', 'Why It Belongs', 'notes');
const rowNote = (r: AnyRow) => field(r, 'reviewer_comments', 'comments', 'Comments', 'notes', 'Notes', 'note', 'reason');
const rowContact = (r: AnyRow) => field(r, 'contact_number', 'Contact Number', 'phone', 'Phone');
const rowReferredBy = (r: AnyRow) => field(r, 'referred_by', 'Referred By', 'referral_source');
const rowSourceTag = (r: AnyRow) => field(r, 'source_tag', 'Source Tag', 'source_mix', 'source_type', 'Source', 'source');
const rowShortlistingComment = (r: AnyRow) => field(r, 'shortlisting_comment', 'Shortlisting Comment', 'curation_comment', 'Curation Comment', 'reviewer_comments', 'comments', 'Comments', 'notes', 'Notes');
function isTruthyCell(value: unknown){ return ['1','true','yes','y','send','sent','approve','approved','shortlist','shortlisted','x','✓','✔'].includes(String(value||'').trim().toLowerCase()); }

function ExternalLink({ value, children }: { value: unknown; children: ReactNode }) {
  const url = safeExternalUrl(value);
  if (!url) return <>—</>;
  return <a href={url} target="_blank" rel="noopener noreferrer">{children}</a>;
}
function StatBox({label,value}:{label:string;value:any}){return <div className="statbox"><strong>{value ?? '—'}</strong><span>{label}</span></div>;}
function DownloadButton({ href, ready, children }: { href: string; ready: boolean; children: ReactNode }) {return <a className={ready?'dark-download ready':'dark-download off'} href={ready?href:'#'} onClick={e=>{if(!ready)e.preventDefault();}}>{children}</a>;}
function downloadText(name:string,text:string){ const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([text],{type:'text/csv'})); a.download=name; a.click(); URL.revokeObjectURL(a.href); }
function discoveryDownload(runId: string, kind: string) { return STORY_BACKEND ? `${STORY_BACKEND}/discovery/export/${encodeURIComponent(runId)}/${kind}` : '#'; }
function repositoryDownload(runId: string, kind: string) { return SEARCH_BACKEND ? `${SEARCH_BACKEND}/repository/export/${encodeURIComponent(runId)}/${kind}` : '#'; }
function recheckDownload(runId: string, kind: string) { return SEARCH_BACKEND ? `${SEARCH_BACKEND}/repository/recheck/export/${encodeURIComponent(runId)}/${kind}` : '#'; }
function presenceDownload(runId: string, kind: string) { return SEARCH_BACKEND ? `${SEARCH_BACKEND}/repository/presence/export/${encodeURIComponent(runId)}/${kind}` : '#'; }
function archiveDownload(row: AnyRow, kind: string) { const id=String(row?.run_id||''); const moduleName=String(row?.module||''); return moduleName === 'ngo_presence_check' ? presenceDownload(id, kind) : moduleName === 'no_website_recheck' ? recheckDownload(id, kind) : repositoryDownload(id, kind); }
function statusText(data: any) { return String(data?.run_status || data?.process_state || data?.stage || '').toLowerCase(); }
function jobModule(job: AnyRow): RunModule | null {
  const id=String(job?.run_id||'').toLowerCase();
  const kind=String(job?.job_type||job?.module||'').toLowerCase();
  if(id.startsWith('recheck_')||kind==='no_website_recheck')return 'recovery';
  if(id.startsWith('presence_')||kind==='ngo_presence_check')return 'presence';
  if(id.startsWith('discovery')||id.startsWith('story')||kind==='discovery'||kind==='story')return 'discovery';
  if(id.startsWith('run_')||kind==='repository'||kind==='dedupe_recheck')return 'repository';
  return null;
}
function recoveryStrategyLabel(value: unknown){ const strategy=String(value||'').toLowerCase(); return strategy==='fast'?'Fast Recovery':strategy==='deep'?'Deep Review':strategy==='firecrawl'?'Firecrawl Review':'Smart Recovery'; }
function runLabel(module: RunModule, status?:AnyRow){ return module==='recovery'?recoveryStrategyLabel(status?.strategy):module==='presence'?'NGO Presence Check':module==='discovery'?'General Discovery':'Bulk Discovery'; }
function runLocation(module: RunModule, status?:AnyRow){ return module==='recovery'?`NGO Discovery › Advanced › ${recoveryStrategyLabel(status?.strategy)}`:module==='presence'?'NGO Discovery › Advanced › Presence Check':module==='discovery'?'NGO Discovery › General Discovery':'NGO Discovery › Bulk Discovery'; }
function activeWord(value: unknown){ return ['queued','starting','running','resuming','pause_requested','stop_requested','cancel_requested','cancelling','searching','fetching','reading_articles','ai_batch_running','resume_started'].includes(String(value||'').toLowerCase()); }
function pausedWord(value: unknown){ return ['paused','pause_requested'].includes(String(value||'').toLowerCase()); }
function terminalWord(value: unknown){ return ['complete','completed','done','finished','success','succeeded','partial','error','failed','fatal_error','cancelled','canceled','cancelled_partial','stopped','stopped_partial','results_ready','partial_results_ready'].includes(String(value||'').toLowerCase()); }
function deepReviewReady(status:any){ const run=String(status?.run_status||'').toLowerCase(); const stage=String(status?.stage||'').toLowerCase(); return ['complete','stopped','cancelled','canceled'].includes(run)||['results_ready','results_ready_partial','stopped_partial','cancelled_partial'].includes(stage); }
function runIsLive(run: ActiveRun){ const s=run.status; if(terminalWord(s?.run_status)||terminalWord(s?.stage)||pausedWord(s?.run_status)||pausedWord(s?.stage))return false; if(String(s?.process_state||'').toLowerCase()==='running'||activeWord(s?.run_status)||activeWord(s?.stage))return true; return s===run.job&&(String(run.job?.live_state||'').toLowerCase()==='running'||activeWord(run.job?.status)||activeWord(run.job?.stage)); }
function runIsPaused(run: ActiveRun){ const s=run.status; if(terminalWord(s?.run_status)||terminalWord(s?.stage))return false; return pausedWord(s?.run_status)||pausedWord(s?.stage)||(s===run.job&&(pausedWord(run.job?.status)||pausedWord(run.job?.stage))); }
function runProgressPct(run: ActiveRun){ const direct=finiteNumber(run.status?.progress_pct); if(direct!==null)return Math.max(0,Math.min(100,direct)); const done=finiteNumber(run.status?.processed??run.job?.processed); const total=finiteNumber(run.status?.total??run.job?.total); return done!==null&&total&&total>0?Math.max(0,Math.min(100,done/total*100)):null; }
function runElapsedSeconds(run: ActiveRun){ const direct=finiteNumber(run.status?.active_elapsed_sec??run.status?.elapsed_sec); if(direct!==null)return direct; const raw=run.status?.started_at_epoch??run.job?.started_at_epoch; const epoch=finiteNumber(raw); if(epoch!==null)return Math.max(0,Date.now()/1000-epoch); const created=Date.parse(String(run.job?.created_at||run.status?.started_at||'')); return Number.isFinite(created)?Math.max(0,(Date.now()-created)/1000):0; }
function discoveryResultsReady(data: any) { return isTerminalReady(data); }
function repositoryResultsReady(data: any) { return isTerminalReady(data); }
function confidenceClass(value: unknown){ const s=String(value||'').toLowerCase(); if(s.includes('high'))return 'tag good'; if(s.includes('low'))return 'tag bad'; return 'tag'; }
async function countCsvRows(file: File) { const text = await file.text(); const lines = text.split(/\r?\n/).map(x => x.trim()).filter(Boolean); if (!lines.length) return 0; const first = lines[0].toLowerCase(); return first.includes('name') ? Math.max(0, lines.length - 1) : lines.length; }

function parseCsv(text: string): AnyRow[] {
  const rows: string[][] = [];
  let cur = '';
  let row: string[] = [];
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (ch === '"' && quoted && next === '"') { cur += '"'; i += 1; continue; }
    if (ch === '"') { quoted = !quoted; continue; }
    if (ch === ',' && !quoted) { row.push(cur.trim()); cur = ''; continue; }
    if ((ch === '\n' || ch === '\r') && !quoted) {
      if (ch === '\r' && next === '\n') i += 1;
      row.push(cur.trim());
      if (row.some(cell => cell.trim())) rows.push(row);
      row = []; cur = '';
      continue;
    }
    cur += ch;
  }
  row.push(cur.trim());
  if (row.some(cell => cell.trim())) rows.push(row);
  if (!rows.length) return [];
  const headers = rows[0].map(h => h.trim());
  return rows.slice(1).map(cells => {
    const out: AnyRow = {};
    headers.forEach((h, i) => { out[h] = cells[i] || ''; });
    return out;
  }).filter(row => rowName(row) || row.ngo_name || row.name);
}

function safeCsvValue(value: unknown) {
  let text = String(value || '').replace(/\u0000/g, '');
  if (/^[=+\-@]/.test(text.trimStart())) text = "'" + text;
  return `"${text.replace(/"/g, '""')}"`;
}

async function bulkCsvWithImplicitState(file: File, selectedState: string) {
  const text = await file.text();
  const rows = parseCsv(text);
  if (!rows.length) throw new Error('CSV is empty.');
  const first = rows[0] || {};
  const keys = Object.keys(first).map(k => k.toLowerCase());
  const hasName = keys.some(x => ['name', 'ngo_name', 'ngo name', 'organisation', 'organization'].includes(x));
  const hasDistrict = keys.includes('district');
  if (!hasName) throw new Error('CSV must include name.');
  if (!hasDistrict) throw new Error('CSV must include district.');
  const headers = ['name', 'district', 'website', 'source', 'notes', 'state'];
  const out = [headers.join(',')];
  for (const row of rows) {
    const values = [
      field(row, 'name', 'ngo_name', 'NGO Name', 'Organisation', 'organization'),
      field(row, 'district', 'District'),
      field(row, 'website', 'Website', 'url'),
      field(row, 'source', 'Source'),
      field(row, 'notes', 'Notes', 'comments', 'Comments'),
      field(row, 'state', 'State') || selectedState,
    ];
    out.push(values.map(safeCsvValue).join(','));
  }
  return new File([out.join('\n') + '\n'], file.name || 'bulk_discovery.csv', { type: 'text/csv' });
}

function referralRowsToCsv(rows: AnyRow[], selectedState: string) {
  const headers = ['name', 'district', 'website', 'source', 'notes', 'state'];
  const out = [headers.join(',')];
  for (const row of rows) {
    const values = [rowName(row), field(row, 'district', 'District'), rowWebsite(row), 'Referral', rowNote(row), selectedState];
    out.push(values.map(safeCsvValue).join(','));
  }
  return out.join('\n') + '\n';
}

function DiscoveryRow({ row }: { row: AnyRow }) {
  return <tr>
    <td>{rowName(row) || 'Needs review'}</td>
    <td><ExternalLink value={rowWebsite(row) || rowSource(row)}>open</ExternalLink></td>
    <td>{rowLocation(row) || '—'}</td>
    <td><span className="tag">{rowPathway(row) || '—'}</span></td>
    <td>{rowWhy(row) || 'Potential child pathway institution. Verify manually.'}</td>
    <td><span className="tag">{rowStatus(row) || 'Ready for Ranking'}</span></td>
    <td><span className={confidenceClass(rowConfidence(row))}>{rowConfidence(row) || '—'}</span></td>
  </tr>;
}

function VerifyRow({ row }: { row: AnyRow }) {
  return <tr>
    <td>{rowName(row) || '—'}</td>
    <td><ExternalLink value={rowWebsite(row)}>open</ExternalLink></td>
    <td>{rowLocation(row) || '—'}</td>
    <td>{rowConfidence(row) || '—'}</td>
    <td>{field(row, 'Official Website Match', 'Website Match', 'match_status', 'status') || '—'}</td>
    <td>{rowNote(row) || '—'}</td>
  </tr>;
}

export default function NgoDiscoveryPage(){
  const [view,setView]=useState<View>('source');
  const [tab,setTab]=useState<Tab>('general');
  const [state,setState]=useState('Karnataka');
  const [runMode,setRunMode]=useState('test');
  const [budget,setBudget]=useState(200);
  const [pathways,setPathways]=useState<string[]>(defaultPathways);
  const [advancedOpen,setAdvancedOpen]=useState(false);

  const [discRunId,setDiscRunId]=useState('');
  const [discPolling,setDiscPolling]=useState(false);
  const [discStarting,setDiscStarting]=useState(false);
  const [discStatus,setDiscStatus]=useState<any>(null);
  const [discResults,setDiscResults]=useState<any>(null);
  const [discArchive,setDiscArchive]=useState<AnyRow[]>([]);
  const [discError,setDiscError]=useState('');
  const discTimer=useRef<any>(null);

  const [bulkCSV,setBulkCSV]=useState<File|null>(null);
  const bulkRef=useRef<HTMLInputElement|null>(null);
  const [repoRunId,setRepoRunId]=useState('');
  const [repoPolling,setRepoPolling]=useState(false);
  const [repoStarting,setRepoStarting]=useState(false);
  const [repoStatus,setRepoStatus]=useState<any>(null);
  const [repoResults,setRepoResults]=useState<any>(null);
  const [repoArchive,setRepoArchive]=useState<AnyRow[]>([]);
  const [repoArchiveLoaded,setRepoArchiveLoaded]=useState(false);
  const [repoArchiveError,setRepoArchiveError]=useState('');
  const [repoError,setRepoError]=useState('');
  const repoTimer=useRef<any>(null);

  const [historyOpen,setHistoryOpen]=useState(true);
  const [discArchiveLoaded,setDiscArchiveLoaded]=useState(false);
  const [discArchiveError,setDiscArchiveError]=useState('');
  const [disk,setDisk]=useState<AnyRow|null>(null);
  const [fastRecoveryCSV,setFastRecoveryCSV]=useState<File|null>(null);
  const fastRecoveryRef=useRef<HTMLInputElement|null>(null);
  const [deepRecoveryCSV,setDeepRecoveryCSV]=useState<File|null>(null);
  const deepRecoveryRef=useRef<HTMLInputElement|null>(null);
  const [deepSourceRunId,setDeepSourceRunId]=useState('');
  const [recoveryRunId,setRecoveryRunId]=useState('');
  const [recoveryStatus,setRecoveryStatus]=useState<any>(null);
  const [recoveryBusy,setRecoveryBusy]=useState(false);
  const [deepReviewBusy,setDeepReviewBusy]=useState(false);
  const [recoveryError,setRecoveryError]=useState('');
  const [recoveryConnectionError,setRecoveryConnectionError]=useState('');
  const [recoveryLastContactAt,setRecoveryLastContactAt]=useState<number|null>(null);
  const [statusClock,setStatusClock]=useState(()=>Date.now());
  const recoveryTimer=useRef<any>(null);

  const [runsOpen,setRunsOpen]=useState(false);
  const [activeRuns,setActiveRuns]=useState<ActiveRun[]>([]);
  const [runsLoading,setRunsLoading]=useState(false);
  const [runsError,setRunsError]=useState('');
  const [runsLastContactAt,setRunsLastContactAt]=useState<number|null>(null);
  const [runsConnectionLost,setRunsConnectionLost]=useState(false);
  const [runActionBusy,setRunActionBusy]=useState('');
  const activeRunsTimer=useRef<any>(null);

  const [presenceCSV,setPresenceCSV]=useState<File|null>(null);
  const presenceRef=useRef<HTMLInputElement|null>(null);
  const [presenceRunId,setPresenceRunId]=useState('');
  const [presenceStatus,setPresenceStatus]=useState<any>(null);
  const [presenceResults,setPresenceResults]=useState<any>(null);
  const [presenceBusy,setPresenceBusy]=useState(false);
  const [presenceError,setPresenceError]=useState('');
  const presenceTimer=useRef<any>(null);

  const referralRef=useRef<HTMLInputElement|null>(null);
  const [referralFile,setReferralFile]=useState<File|null>(null);
  const [referralRows,setReferralRows]=useState<AnyRow[]>([]);
  const [referralSelected,setReferralSelected]=useState<Record<number, boolean>>({});
  const [referralMessage,setReferralMessage]=useState('');
  const [referralError,setReferralError]=useState('');
  const [referralSearching,setReferralSearching]=useState(false);

  const [leadPool,setLeadPool]=useState<AnyRow[]>([]);
  const [poolMessage,setPoolMessage]=useState('');
  const [poolBusy,setPoolBusy]=useState(false);
  const [sentRunIds,setSentRunIds]=useState<Record<string, boolean>>({});
  const [poolView,setPoolView]=useState<'pending'|'approved'|'followup'|'all'>('pending');
  const leadImportRef=useRef<HTMLInputElement|null>(null);
  const shortlistImportRef=useRef<HTMLInputElement|null>(null);
  const [rankingTarget,setRankingTarget]=useState('everyone');
  const [rankingPassword,setRankingPassword]=useState('');

  function onModeChange(modeKey:string){ setRunMode(modeKey); const m=runModes.find(x=>x.key===modeKey); if(m && modeKey!=='custom') setBudget(m.budget); }
  function togglePathway(key:string){ setPathways(prev=>prev.includes(key)?prev.filter(x=>x!==key):[...prev,key]); }
  const loadDiscoveryArchive = useCallback(async()=>{
    setDiscArchiveError('');
    if(!STORY_BACKEND){ setDiscArchiveLoaded(true); setDiscArchiveError(STORY_BACKEND_CONFIG_ERROR); return; }
    const r=await safeStoryJSON('/discovery/archive?limit=120');
    const rows=archiveRowsFromPayload(r.data);
    if(r.ok&&rows){ setDiscArchive(rows); }
    else { setDiscArchiveError(r.error||'Discovery archive returned an invalid rows payload.'); }
    setDiscArchiveLoaded(true);
  },[]);
  const loadRepositoryArchive = useCallback(async()=>{
    setRepoArchiveError('');
    if(!SEARCH_BACKEND){ setRepoArchiveLoaded(true); setRepoArchiveError(SEARCH_BACKEND_CONFIG_ERROR); return; }
    const r=await safeSearchJSON('/repository/archive?limit=120');
    const rows=archiveRowsFromPayload(r.data);
    if(r.ok&&rows){ setRepoArchive(rows); }
    else { setRepoArchiveError(r.error||'Bulk archive returned an invalid rows payload.'); }
    setRepoArchiveLoaded(true);
  },[]);

  // --- v134: resilient archive loading + disk usage ---
  const loadDiskUsage=useCallback(async()=>{ if(!SEARCH_BACKEND)return; const r=await safeSearchJSON('/repository/runs/disk-usage'); if(r.ok&&isRecord(r.data))setDisk(r.data); },[]);
  async function deleteRun(runId:string){
    if(!SEARCH_BACKEND)return;
    const password=window.prompt(`Delete run ${runId}?\nThis permanently frees disk space.\n\nEnter admin password to confirm:`);
    if(!password)return;
    const r=await safeSearchJSON('/repository/runs/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password,confirm:true,run_id:runId})});
    if(!r.ok){alert(r.error||'Could not delete run.');return;}
    loadRepositoryArchive(); loadDiscoveryArchive(); loadDiskUsage();
  }
  useEffect(()=>{ loadDiskUsage(); },[loadDiskUsage]);

  const loadActiveRuns=useCallback(async(quiet=true)=>{
    if(!SEARCH_BACKEND)return;
    if(!quiet)setRunsLoading(true);
    setRunsError('');
    const jobs=await safeSearchJSON('/jobs?limit=100');
    const rows=archiveRowsFromPayload(jobs.data);
    if(!jobs.ok||!rows){
      setRunsConnectionLost(true);
      setRunsError('Worker connection interrupted. The last known run state is shown below; reconnecting automatically.');
      if(!quiet)setRunsLoading(false);
      return;
    }
    setRunsConnectionLost(false);
    setRunsError('');
    setRunsLastContactAt(Date.now());
    const candidates=rows.filter(job=>{
      const runKind=jobModule(job);
      if(!runKind)return false;
      return String(job.live_state||'').toLowerCase()==='running'||activeWord(job.status)||activeWord(job.run_status)||activeWord(job.stage)||pausedWord(job.status)||pausedWord(job.run_status)||pausedWord(job.stage);
    }).slice(0,12);
    const detailed=(await Promise.all(candidates.map(async job=>{
      const runKind=jobModule(job);
      const id=String(job.run_id||'');
      if(!runKind||!id)return null;
      const path=runKind==='recovery'?`/repository/recheck/status/${encodeURIComponent(id)}`:runKind==='presence'?`/repository/presence/status/${encodeURIComponent(id)}`:runKind==='repository'?`/repository/status/${encodeURIComponent(id)}`:`/discovery/status/${encodeURIComponent(id)}`;
      const response=runKind==='discovery'?await safeStoryJSON(path):await safeSearchJSON(path);
      const status=response.ok&&isRecord(response.data)?response.data:job;
      const run:ActiveRun={run_id:id,module:runKind,label:runLabel(runKind,status),location:runLocation(runKind,status),service:'Railway search worker',job,status};
      return runIsLive(run)||runIsPaused(run)||Boolean(status?.can_resume)?run:null;
    }))).filter((run):run is ActiveRun=>!!run);
    detailed.sort((a,b)=>Number(runIsLive(b))-Number(runIsLive(a))||String(b.job.updated_at||b.status.updated_at||'').localeCompare(String(a.job.updated_at||a.status.updated_at||'')));
    setActiveRuns(detailed);
    const recovery=detailed.find(r=>r.module==='recovery');
    if(recovery){ setRecoveryRunId(recovery.run_id); setRecoveryStatus(recovery.status); }
    const discovery=detailed.find(r=>r.module==='discovery');
    if(discovery){ setDiscRunId(discovery.run_id); setDiscStatus(discovery.status); if(runIsLive(discovery))setDiscPolling(true); }
    const repository=detailed.find(r=>r.module==='repository');
    if(repository){ setRepoRunId(repository.run_id); setRepoStatus(repository.status); if(runIsLive(repository))setRepoPolling(true); }
    const presence=detailed.find(r=>r.module==='presence');
    if(presence){ setPresenceRunId(presence.run_id); setPresenceStatus(presence.status); }
    if(!quiet)setRunsLoading(false);
  },[]);

  useEffect(()=>{
    let stopped=false;
    async function tick(){ if(stopped)return; await loadActiveRuns(true); if(!stopped)activeRunsTimer.current=setTimeout(tick,5000); }
    tick();
    return()=>{stopped=true;if(activeRunsTimer.current)clearTimeout(activeRunsTimer.current);};
  },[loadActiveRuns]);

  const loadLeadPool = useCallback(async()=>{ if(!BACKEND)return; const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool`); if(r.ok&&r.data)setLeadPool(r.data.rows||[]); },[state]);
  useEffect(()=>{loadDiscoveryArchive(); loadRepositoryArchive(); loadLeadPool();},[loadDiscoveryArchive, loadRepositoryArchive, loadLeadPool]);

  // Restore the last Smart Recovery status after a route change, refresh, or browser reopen.
  // The job itself runs on the worker; this only reconnects the status panel.
  useEffect(()=>{
    if(!SEARCH_BACKEND || typeof window==='undefined') return;
    let cancelled=false;
    async function restoreRecovery(){
      const saved=(window.localStorage.getItem(RECOVERY_RUN_STORAGE_KEY)||'').trim();
      if(saved){
        const status=await safeSearchJSON(`/repository/recheck/status/${encodeURIComponent(saved)}`);
        if(!cancelled&&status.ok&&status.data){
          setRecoveryRunId(saved);
          setRecoveryStatus(status.data);
          return;
        }
        window.localStorage.removeItem(RECOVERY_RUN_STORAGE_KEY);
      }
      const resumable=await safeSearchJSON('/repository/recheck/resumable?limit=20');
      const active=Array.isArray(resumable.data?.active_runs) ? resumable.data.active_runs.find((id:unknown)=>typeof id==='string'&&id.trim()) : '';
      if(!cancelled&&active){
        setRecoveryRunId(String(active));
        window.localStorage.setItem(RECOVERY_RUN_STORAGE_KEY,String(active));
      }
    }
    restoreRecovery();
    return()=>{cancelled=true;};
  },[]);

  useEffect(()=>{
    if(typeof window==='undefined'||!recoveryRunId)return;
    window.localStorage.setItem(RECOVERY_RUN_STORAGE_KEY,recoveryRunId);
  },[recoveryRunId]);
  useEffect(()=>{
    if(!recoveryRunId)return;
    const timer=window.setInterval(()=>setStatusClock(Date.now()),1000);
    return()=>window.clearInterval(timer);
  },[recoveryRunId]);
  useEffect(()=>{
    try {
      const raw = window.localStorage.getItem('dfp2_sent_archive_runs');
      if (raw) setSentRunIds(JSON.parse(raw) || {});
    } catch {}
  },[]);
  function markRunSent(runId:string){
    setSentRunIds(prev=>{
      const next={...prev,[runId]:true};
      try { window.localStorage.setItem('dfp2_sent_archive_runs', JSON.stringify(next)); } catch {}
      return next;
    });
  }

  function leadPoolImportCopy(data:any){
    const added = Number(data?.added || 0);
    const existing = Number(data?.already_existing_count ?? data?.not_added_existing_count ?? data?.merged ?? data?.updated ?? 0);
    const alreadyRated = Number(data?.already_rated_marked || 0);
    const copy = `Lead Pool updated: ${added} added. ${existing} already there, so not duplicated.${alreadyRated ? ` ${alreadyRated} marked already rated.` : ''}`;
    return copy;
  }

  function rankingResultCopy(data:any){
    const tasks = Number(data?.new_tasks || 0);
    const leads = Number(data?.new_leads ?? data?.sent_count ?? 0);
    const skipped = Number(data?.not_sent_existing_count ?? data?.skipped_duplicate_count ?? 0);
    const assignments = data?.assignments && typeof data.assignments === 'object'
      ? Object.entries(data.assignments).filter(([,v])=>Number(v)>0).map(([k,v])=>`${k}: ${v}`).join(' · ')
      : '';
    return `Sent to ranking: ${tasks} PM task(s) from ${leads} lead(s). ${skipped} already there, so not sent again.${assignments ? ` ${assignments}` : ''}`;
  }

  async function sendRowsToLeadPool(rows: AnyRow[], sourceType: string){
    if(!BACKEND||!rows.length)return;
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/import`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source_type:sourceType, rows})
    });
    setPoolBusy(false);
    if(!r.ok){setPoolMessage(r.error||'Could not send to Lead Pool.'); return;}
    setLeadPool(r.data?.rows||[]);
    const copy = leadPoolImportCopy(r.data);
    setPoolMessage(copy);
    window.alert(copy);
  }

  async function sendRunToLeadPool(runId:string, moduleName:string, sourceType='Archive Import'){
    if(!BACKEND||!runId)return;
    const mod = String(moduleName || '').toLowerCase();
    const exportUrl = mod === 'discovery' || mod === 'legacy_story'
      ? (STORY_BACKEND ? `${STORY_BACKEND}/discovery/export/${encodeURIComponent(runId)}/leads` : '')
      : mod === 'no_website_recheck'
        ? (SEARCH_BACKEND ? `${SEARCH_BACKEND}/repository/recheck/export/${encodeURIComponent(runId)}/results` : '')
        : (SEARCH_BACKEND ? `${SEARCH_BACKEND}/repository/export/${encodeURIComponent(runId)}/repository` : '');
    if(!exportUrl){ setPoolMessage('Worker backend URL is not configured for this run.'); return; }
    setPoolBusy(true); setPoolMessage('');
    try {
      const exportRes = await fetch(exportUrl);
      if(!exportRes.ok){ throw new Error(`Could not fetch worker export (${exportRes.status}). Download the CSV and import manually if needed.`); }
      const rows = parseCsv(await exportRes.text()).map(row => ({...row, source_type:sourceType, source_run_id:runId}));
      if(!rows.length){ throw new Error('Worker export had no importable NGO rows.'); }
      const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/import`,{
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source_type:sourceType, source_run_id:runId, rows})
      });
      if(!r.ok){setPoolMessage(r.error||'Could not send run to Lead Pool.'); return;}
      const copy = leadPoolImportCopy(r.data);
      setPoolMessage(copy);
      window.alert(copy);
      markRunSent(runId);
      setLeadPool(r.data?.rows||[]);
    } catch (err:any) {
      setPoolMessage(err?.message || 'Could not send run to Lead Pool.');
    } finally {
      setPoolBusy(false);
    }
  }

  function downloadManualLeadSampleCsv(){
    const csv = [
      ['ngo_name','website','district','source_tag','shortlisting_comment','contact_number','referred_by','comments'].join(','),
      ['Example Child Foundation','https://example.org','Bengaluru','Human Referral','Recommended by regional partner; review for child-focused fit.','9876543210','Avika','Known through local network'].map(safeCsvValue).join(','),
      ['Example Residential School','','Mysuru','Internet Discovery','Residential education model; check website evidence and DFP relevance.','','','Found during internet search'].map(safeCsvValue).join(','),
    ].join('\n') + '\n';
    downloadText('manual_leads_sample.csv', csv);
  }

  function downloadShortlistDecisionCsv(){
    const headers = ['lead_id','send_for_shortlisting','source_tag','shortlisting_comment','ngo_name','website','district','current_status','source_mix'];
    const rows = leadPool.map(row => [
      row.lead_id || '',
      isTruthyCell(row.send_for_shortlisting) || ['approved_for_ranking','approved_with_comment'].includes(curationOf(row)) ? 'TRUE' : '',
      rowSourceTag(row) || '',
      rowShortlistingComment(row) || '',
      rowName(row) || '',
      rowWebsite(row) || '',
      rowLocation(row) || '',
      rowStatus(row) || '',
      field(row,'source_mix','source_type','Source') || '',
    ]);
    const csv = [headers.join(','), ...rows.map(values => values.map(safeCsvValue).join(','))].join('\n') + '\n';
    downloadText(`${state.replace(/\s+/g,'_').toLowerCase()}_lead_pool_shortlisting_decisions.csv`, csv);
  }

  async function importShortlistDecisionCsv(file?: File | null){
    if(!file) return;
    setPoolMessage('');
    try {
      const rows = parseCsv(await file.text());
      if(!rows.length){ setPoolMessage('No valid decision rows found in CSV.'); return; }
      const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/import-decisions`,{
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({rows, actor:'Excel Import'})
      });
      if(!r.ok){
        const blocked = Number(r.data?.blocked_count || r.data?.blocked?.length || 0);
        setPoolMessage(r.error + (blocked ? ` ${blocked} row(s) are missing source tag or comment.` : ''));
        return;
      }
      setLeadPool(r.data?.rows||[]);
      const copy = r.data?.message || `Excel decisions applied.`;
      setPoolMessage(copy);
      window.alert(copy);
    } catch (err:any) {
      setPoolMessage(err?.message || 'Could not read decision CSV.');
    } finally {
      if(shortlistImportRef.current) shortlistImportRef.current.value = '';
    }
  }

  async function importLeadPoolCsv(file?: File | null){
    if(!file) return;
    setPoolMessage('');
    try {
      const rows = parseCsv(await file.text()).map(row => ({
        ...row,
        source_type: field(row, 'source_type', 'source', 'Source') || 'Manual Add',
        curation_status: field(row, 'curation_status', 'Curation Status') || 'pending_review',
      }));
      if(!rows.length){ setPoolMessage('No valid rows found in CSV.'); return; }
      await sendRowsToLeadPool(rows, 'Manual Add');
    } catch (err:any) {
      setPoolMessage(err?.message || 'Could not read CSV.');
    } finally {
      if(leadImportRef.current) leadImportRef.current.value = '';
    }
  }

  async function editLead(row: AnyRow){
    const ngo_name = window.prompt('NGO name', String(rowName(row)||''));
    if(ngo_name === null) return;
    const website = window.prompt('Website', String(rowWebsite(row)||''));
    if(website === null) return;
    const one_line_understanding = window.prompt('One-line understanding', String(rowOneLine(row)||''));
    if(one_line_understanding === null) return;
    const contact_number = window.prompt('Contact number', String(rowContact(row)||''));
    if(contact_number === null) return;
    const source_tag = window.prompt('Source tag', String(rowSourceTag(row)||rowSource(row)||''));
    if(source_tag === null) return;
    const curation_comment = window.prompt('Shortlisting comment', String(rowShortlistingComment(row)||rowNote(row)||''));
    if(curation_comment === null) return;
    await updateLead(row, {ngo_name, website, one_line_understanding, contact_number, source_tag, shortlisting_comment: curation_comment, curation_comment, reviewer_comments: curation_comment, notes: curation_comment});
  }

  async function updateLead(row: AnyRow, patch: AnyRow){
    if(!BACKEND||!row.lead_id)return;
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/update`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lead_id:row.lead_id, ...patch})
    });
    setPoolBusy(false);
    if(!r.ok){setPoolMessage(r.error||'Could not update lead.'); return;}
    setLeadPool(r.data?.rows||[]);
    setPoolMessage('Lead updated.');
  }

  async function sendForShortlisting(row: AnyRow){
    if(!BACKEND||!row.lead_id)return;
    const defaultTag = String(rowSourceTag(row)||rowSource(row)||'').trim();
    const source_tag = window.prompt('Source tag (example: Internet Discovery or Human Referral)', defaultTag);
    if(source_tag === null) return;
    const shortlisting_comment = window.prompt('Shortlisting comment for PM review', String(rowShortlistingComment(row)||rowNote(row)||''));
    if(shortlisting_comment === null) return;
    if(!source_tag.trim() || !shortlisting_comment.trim()){
      setPoolMessage('Source tag and shortlisting comment are required before sending for shortlisting.');
      return;
    }
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/curate`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lead_id:row.lead_id, curation_status:'approved_with_comment', source_tag, shortlisting_comment, curation_comment:shortlisting_comment, actor:'Admin'})
    });
    setPoolBusy(false);
    if(!r.ok){setPoolMessage(r.error||'Could not send for shortlisting.'); return;}
    setLeadPool(r.data?.rows||[]);
    setPoolMessage('Lead marked for shortlisting.');
  }

  async function curateLead(row: AnyRow, status: string, needsComment = false){
    if(!BACKEND||!row.lead_id)return;
    let comment = rowNote(row) || '';
    if(needsComment || status === 'approved_with_comment' || status === 'needs_follow_up' || status === 'sent_back_to_pool'){
      const entered = window.prompt(status === 'approved_with_comment' ? 'Comment for PM reviewer' : 'Add comment', comment);
      if(entered === null) return;
      comment = entered;
      if(status === 'approved_with_comment' && !comment.trim()){ setPoolMessage('Add a comment before approving insufficient leads.'); return; }
    }
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/curate`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lead_id:row.lead_id, curation_status:status, curation_comment:comment, actor:'Admin'})
    });
    setPoolBusy(false);
    if(!r.ok){setPoolMessage(r.error||'Could not update curation.'); return;}
    setLeadPool(r.data?.rows||[]);
    setPoolMessage('Lead updated.');
  }

  async function deleteLeads(payload: AnyRow){
    if(!BACKEND)return;
    const n = Array.isArray(payload?.lead_ids) ? payload.lead_ids.length : (payload?.all ? leadPool.length : 0);
    if(!window.confirm(`Delete ${n || 'selected'} lead(s) from Lead Pool? This cannot be undone.`)) return;
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/delete`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    setPoolBusy(false);
    if(!r.ok){setPoolMessage(r.error||'Could not delete.'); return;}
    setLeadPool(r.data?.rows||[]);
    setPoolMessage(`Deleted ${r.data?.deleted || 0}.`);
  }

  async function deleteAllApprovedLeads(){
    if(!BACKEND)return;
    if(!approvedLeads.length){ setPoolMessage('No approved leads to delete.'); return; }
    if(!rankingPassword.trim()){ setPoolMessage('Enter admin password before deleting all approved leads.'); return; }
    const ok = window.confirm(`Delete ALL ${approvedLeads.length} approved lead(s) from Lead Pool memory? This will not delete PM ranking submissions. You can still use Admin Undo immediately if this was a mistake.`);
    if(!ok) return;
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/approved-leads/delete-all`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:rankingPassword, confirm:true})
    });
    setPoolBusy(false);
    if(!r.ok){setPoolMessage(r.error||'Could not delete approved leads.'); return;}
    setLeadPool(r.data?.rows||[]);
    setPoolMessage(`Deleted ${r.data?.deleted || 0} approved lead(s).`);
  }

  async function sendLeadPoolToRanking(){
    if(!BACKEND)return;
    if(!approvedLeads.length){ setPoolMessage('No approved leads to send.'); return; }
    if(!rankingPassword.trim()){ setPoolMessage('Enter admin password before sending to ranking.'); return; }
    const ok = window.confirm(`Send ${approvedLeads.length} approved lead(s) to ranking? Existing assigned/rated NGOs will be skipped and nothing already submitted will be overwritten.`);
    if(!ok) return;
    const allPm = PM_NAMES.filter(Boolean);
    let pms = allPm;
    let distribution = 'assign_to_each';
    if(rankingTarget === 'split'){
      distribution = 'split_evenly';
    } else if(rankingTarget !== 'everyone'){
      pms = [rankingTarget];
      distribution = 'specific_pm';
    }
    setPoolBusy(true); setPoolMessage('');
    const r=await safeJSON(`${BACKEND}/workspace/${encodeURIComponent(state)}/send-to-ranking`,{
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:rankingPassword,pms,distribution,lead_ids:approvedLeads.map(r=>r.lead_id).filter(Boolean)})
    });
    setPoolBusy(false);
    if(!r.ok){
      const blocked = Number(r.data?.blocked_missing_metadata_count || r.data?.blocked_not_approved_count || r.data?.blocked?.length || 0);
      setPoolMessage((r.error||'Could not send to ranking.') + (blocked ? ` ${blocked} lead(s) need source tag/comment or approval first.` : ''));
      return;
    }
    const copy = rankingResultCopy(r.data);
    setPoolMessage(copy);
    window.alert(copy);
    loadLeadPool();
  }


  function goToRun(run:ActiveRun){
    setView('internet');
    if(run.module==='discovery'){setTab('general');setDiscRunId(run.run_id);setDiscStatus(run.status);if(runIsLive(run))setDiscPolling(true);}
    if(run.module==='repository'){setTab('bulk');setRepoRunId(run.run_id);setRepoStatus(run.status);if(runIsLive(run))setRepoPolling(true);}
    if(run.module==='recovery'){setTab('bulk');setAdvancedOpen(true);setRecoveryRunId(run.run_id);setRecoveryStatus(run.status);}
    if(run.module==='presence'){setTab('bulk');setAdvancedOpen(true);setPresenceRunId(run.run_id);setPresenceStatus(run.status);}
    setRunsOpen(false);
    window.setTimeout(()=>document.getElementById(`run-panel-${run.module}`)?.scrollIntoView({behavior:'smooth',block:'center'}),80);
  }

  async function controlRun(run:ActiveRun,action:'pause'|'resume'|'cancel'|'end'){
    if(action==='end'&&!window.confirm(`End ${run.label} and finalise the rows completed so far? Downloads will remain available.`))return;
    if(action==='cancel'&&!window.confirm(`Cancel ${run.label}? The current bounded NGO operation may take a short while to stop. Completed checkpoints will be preserved.`))return;
    const key=`${run.run_id}:${action}`; setRunActionBusy(key); setRunsError('');
    let path='';
    if(run.module==='recovery')path=`/repository/recheck/${action==='end'?'stop':action}/${encodeURIComponent(run.run_id)}`;
    else if(run.module==='discovery')path=`/discovery/${action==='end'?'cancel':action}/${encodeURIComponent(run.run_id)}`;
    else if(run.module==='repository')path=`/repository/${action==='resume'?'resume':'cancel'}/${encodeURIComponent(run.run_id)}`;
    else if(run.module==='presence')path=`/repository/presence/cancel/${encodeURIComponent(run.run_id)}`;
    const response=run.module==='discovery'?await safeStoryJSON(path,{method:'POST'}):await safeSearchJSON(path,{method:'POST'});
    if(!response.ok)setRunsError(response.error||`Could not ${action} this run.`);
    await loadActiveRuns(true); setRunActionBusy('');
  }

  async function startDiscovery(){
    setDiscError(''); setDiscResults(null); setDiscStatus(null);
    if(!STORY_BACKEND){setDiscError(STORY_BACKEND_CONFIG_ERROR); return;}
    if(!pathways.length){setDiscError('Select at least one pathway.'); return;}
    const safeBudget=Math.max(1,Math.min(MAX_DISCOVERY_BUDGET,Number(budget||200)));
    if(safeBudget>4000 && !window.confirm('This can use almost your full Serper balance. Continue?')) return;
    setDiscStarting(true);
    const url=`${STORY_BACKEND}/discovery/start?state=${encodeURIComponent(state)}&budget=${safeBudget}&run_mode=${encodeURIComponent(runMode)}&pathways=${encodeURIComponent(pathways.join(','))}`;
    const r=await safeJSON(url,{method:'POST'});
    setDiscStarting(false);
    if(!r.ok||!r.data){setDiscError(r.error||'Could not start General Discovery.'); return;}
    setDiscRunId(r.data.run_id); setDiscPolling(true); loadDiscoveryArchive();
  }
  async function pauseDiscovery(){ if(!STORY_BACKEND||!discRunId)return; const r=await safeJSON(`${STORY_BACKEND}/discovery/pause/${encodeURIComponent(discRunId)}`,{method:'POST'}); if(!r.ok)setDiscError(r.error||'Could not pause run.'); else setDiscPolling(false); }
  async function resumeDiscovery(id=discRunId){ if(!STORY_BACKEND||!id)return; const r=await safeJSON(`${STORY_BACKEND}/discovery/resume/${encodeURIComponent(id)}`,{method:'POST'}); if(!r.ok){setDiscError(r.error||'Could not resume run.'); return;} setDiscRunId(id); setDiscPolling(true); }
  async function cancelDiscovery(){ if(!STORY_BACKEND||!discRunId)return; await safeJSON(`${STORY_BACKEND}/discovery/cancel/${encodeURIComponent(discRunId)}`,{method:'POST'}); setDiscPolling(false); loadDiscoveryArchive(); }

  useEffect(()=>{ if(!discPolling||!discRunId)return; let stopped=false; async function tick(){ if(stopped)return; const s=await safeJSON(`${STORY_BACKEND}/discovery/status/${encodeURIComponent(discRunId)}`); if(s.ok&&s.data)setDiscStatus(s.data); else if(s.error)setDiscError(s.error); const rr=await safeJSON(`${STORY_BACKEND}/discovery/results/${encodeURIComponent(discRunId)}?limit=120`); if(rr.ok&&rr.data){setDiscResults(rr.data); if(discoveryResultsReady(rr.data)){setDiscPolling(false); loadDiscoveryArchive(); return;}} if(isFailureStatus(s.data)||String(s.data?.stage||'').toLowerCase().includes('paused')){setDiscPolling(false); loadDiscoveryArchive(); return;} discTimer.current=setTimeout(tick,POLL_MS);} tick(); return()=>{stopped=true; if(discTimer.current)clearTimeout(discTimer.current);};},[discPolling,discRunId,loadDiscoveryArchive]);

  async function startRepository(mode:'bulk'){
    setRepoError(''); setRepoResults(null); setRepoStatus(null);
    if(!SEARCH_BACKEND){setRepoError(SEARCH_BACKEND_CONFIG_ERROR);return;}
    if(!bulkCSV){setRepoError('Upload a CSV first.');return;}
    const count=await countCsvRows(bulkCSV); if(count>BULK_MAX){setRepoError(`Bulk Discovery allows up to ${BULK_MAX} rows. This file appears to have ${count}.`);return;}
    const fd=new FormData();
    try { fd.append('file', await bulkCsvWithImplicitState(bulkCSV, state || 'Karnataka')); }
    catch (err:any) { setRepoError(err?.message || 'CSV must include name and district.'); return; }
    setRepoStarting(true);
    const r=await safeJSON(`${SEARCH_BACKEND}/repository/start?mode=${mode}`,{method:'POST',body:fd});
    setRepoStarting(false);
    if(!r.ok||!r.data){setRepoError(r.error||'Could not start Bulk Discovery.');return;}
    setRepoRunId(r.data.run_id); setRepoPolling(true); loadRepositoryArchive();
  }
  async function stopRepository(){ if(!SEARCH_BACKEND||!repoRunId)return; await safeJSON(`${SEARCH_BACKEND}/repository/cancel/${encodeURIComponent(repoRunId)}`,{method:'POST'}); setRepoPolling(false); loadRepositoryArchive(); }
  useEffect(()=>{ if(!repoPolling||!repoRunId)return; let stopped=false; async function tick(){ if(stopped)return; const s=await safeJSON(`${SEARCH_BACKEND}/repository/status/${encodeURIComponent(repoRunId)}`); if(s.ok&&s.data)setRepoStatus(s.data); else if(s.error)setRepoError(s.error); const rr=await safeJSON(`${SEARCH_BACKEND}/repository/results/${encodeURIComponent(repoRunId)}?limit=80`); if(rr.ok&&rr.data){setRepoResults(rr.data); if(repositoryResultsReady(rr.data)){setRepoPolling(false); loadRepositoryArchive(); return;}} if(isFailureStatus(s.data)){setRepoPolling(false); return;} repoTimer.current=setTimeout(tick,POLL_MS);} tick(); return()=>{stopped=true; if(repoTimer.current)clearTimeout(repoTimer.current);};},[repoPolling,repoRunId,loadRepositoryArchive]);

  async function startRecovery(strategy:'fast'|'deep', file:File|null){
    setRecoveryError(''); setRecoveryStatus(null);
    if(!SEARCH_BACKEND){setRecoveryError(SEARCH_BACKEND_CONFIG_ERROR); return;}
    if(!file){setRecoveryError(`Upload a CSV for ${strategy==='fast'?'Fast Recovery':'Deep Recovery'} first.`); return;}
    const fd = new FormData();
    fd.append('file', file);
    setRecoveryBusy(true);
    const r=await safeSearchJSON(`/repository/recheck/start?strategy=${strategy}`,{method:'POST',body:fd});
    setRecoveryBusy(false);
    if(!r.ok||!r.data){setRecoveryError(r.error||`Could not start ${strategy==='fast'?'Fast Recovery':'Deep Recovery'}.`); return;}
    const runId=String(r.data.run_id||'');
    setRecoveryRunId(runId);
    setRecoveryStatus({run_status:'starting',stage:'queued',strategy,processed:0,total:r.data.total||0});
    if(typeof window!=='undefined')window.localStorage.setItem(RECOVERY_RUN_STORAGE_KEY,runId);
    loadRepositoryArchive();
  }
  async function pauseRecovery(){ if(!SEARCH_BACKEND||!recoveryRunId)return; const r=await safeSearchJSON(`/repository/recheck/pause/${encodeURIComponent(recoveryRunId)}`,{method:'POST'}); if(!r.ok)setRecoveryError(r.error||'Could not pause.'); }
  async function resumeRecovery(strategyOverride=''){
    if(!SEARCH_BACKEND||!recoveryRunId)return;
    const query=strategyOverride?`?strategy_override=${encodeURIComponent(strategyOverride)}`:'';
    const r=await safeSearchJSON(`/repository/recheck/resume/${encodeURIComponent(recoveryRunId)}${query}`,{method:'POST'});
    if(!r.ok){setRecoveryError(r.error||'Could not resume.');return;}
    setRecoveryError('');
    setRecoveryStatus((prev:any)=>({...prev,run_status:'resuming',stage:'resume_started',strategy:strategyOverride||prev?.strategy}));
  }
  async function startDeepReview(sourceRunId=recoveryRunId){
    if(!SEARCH_BACKEND||!sourceRunId)return;
    setDeepReviewBusy(true); setRecoveryError('');
    const r=await safeSearchJSON(`/repository/recheck/deep-review/start/${encodeURIComponent(sourceRunId)}`,{method:'POST'});
    setDeepReviewBusy(false);
    if(!r.ok||!r.data){setRecoveryError(r.error||'Could not start Deep Review. Pause or finish the active Fast Recovery first.');return;}
    const childId=String(r.data.run_id||'');
    setRecoveryRunId(childId); setRecoveryStatus({run_status:'starting',stage:'queued',strategy:'deep',processed:0,total:r.data.total||0,parent_run_id:sourceRunId});
    if(typeof window!=='undefined')window.localStorage.setItem(RECOVERY_RUN_STORAGE_KEY,childId);
    loadRepositoryArchive();
  }
  async function stopRecovery(){ if(!SEARCH_BACKEND||!recoveryRunId)return; if(!window.confirm(`End this ${recoveryStrategyLabel(recoveryStatus?.strategy)} run and finalise the rows completed so far? Downloads will remain available.`))return; const r=await safeSearchJSON(`/repository/recheck/stop/${encodeURIComponent(recoveryRunId)}`,{method:'POST'}); if(!r.ok)setRecoveryError(r.error||'Could not end the run.'); loadRepositoryArchive(); }
  async function cancelRecovery(){ if(!SEARCH_BACKEND||!recoveryRunId)return; if(!window.confirm(`Cancel this ${recoveryStrategyLabel(recoveryStatus?.strategy)} run? The current bounded NGO operation may take a short while to stop. Completed checkpoints will be preserved.`))return; const r=await safeSearchJSON(`/repository/recheck/cancel/${encodeURIComponent(recoveryRunId)}`,{method:'POST'}); if(!r.ok)setRecoveryError(r.error||'Could not cancel the run.'); loadRepositoryArchive(); }
  useEffect(()=>{
    if(!recoveryRunId)return;
    let stopped=false;
    let consecutiveFailures=0;
    async function tick(){
      if(stopped)return;
      const response=await safeSearchJSON(`/repository/recheck/status/${encodeURIComponent(recoveryRunId)}`);
      if(response.ok&&response.data){
        consecutiveFailures=0;
        setRecoveryStatus(response.data);
        setRecoveryLastContactAt(Date.now());
        setRecoveryConnectionError('');
        setRecoveryError('');
      }else{
        consecutiveFailures+=1;
        setRecoveryConnectionError('Status connection interrupted. The run may still be active on Railway; retrying automatically.');
      }
      const rs=String(response.data?.run_status||'').toLowerCase();
      if(isFailureStatus(response.data)||String(response.data?.stage||'').toLowerCase().startsWith('results_ready')||rs==='completed'||rs==='stopped'||rs==='cancelled'||rs==='canceled'){
        loadRepositoryArchive();
        return;
      }
      const retryDelay=consecutiveFailures>=3?10000:consecutiveFailures?5000:POLL_MS;
      recoveryTimer.current=setTimeout(tick,retryDelay);
    }
    tick();
    return()=>{stopped=true; if(recoveryTimer.current)clearTimeout(recoveryTimer.current);};
  },[recoveryRunId,loadRepositoryArchive]);

  function downloadPresenceSampleCsv(){
    downloadText('ngo_presence_check_sample.csv', 'ngo_name,state,center_name\nHumana People to People India,Delhi,Learning Centre 1\nHumana People to People India,Delhi,Learning Centre 2\nSparsha Trust,Karnataka,\n');
  }

  async function startPresenceCheck(){
    setPresenceError(''); setPresenceStatus(null); setPresenceResults(null);
    if(!SEARCH_BACKEND){setPresenceError(SEARCH_BACKEND_CONFIG_ERROR); return;}
    if(!presenceCSV){setPresenceError('Upload a CSV first.'); return;}
    const count=await countCsvRows(presenceCSV);
    if(count>BULK_MAX){setPresenceError(`NGO Presence Check allows up to ${BULK_MAX} rows. This file appears to have ${count}.`); return;}
    const fd = new FormData();
    fd.append('file', presenceCSV);
    setPresenceBusy(true);
    const r=await safeJSON(`${SEARCH_BACKEND}/repository/presence/start`,{method:'POST',body:fd});
    setPresenceBusy(false);
    if(!r.ok||!r.data){setPresenceError(r.error||'Could not start NGO Presence Check.'); return;}
    setPresenceRunId(r.data.run_id);
    loadRepositoryArchive();
  }

  async function cancelPresenceCheck(){
    if(!SEARCH_BACKEND||!presenceRunId)return;
    await safeJSON(`${SEARCH_BACKEND}/repository/presence/cancel/${encodeURIComponent(presenceRunId)}`,{method:'POST'});
    loadRepositoryArchive();
  }

  useEffect(()=>{ if(!presenceRunId)return; let stopped=false; async function tick(){ if(stopped)return; const s=await safeJSON(`${SEARCH_BACKEND}/repository/presence/status/${encodeURIComponent(presenceRunId)}`); if(s.ok&&s.data)setPresenceStatus(s.data); else if(s.error)setPresenceError(s.error); const rr=await safeJSON(`${SEARCH_BACKEND}/repository/presence/results/${encodeURIComponent(presenceRunId)}?limit=80`); if(rr.ok&&rr.data)setPresenceResults(rr.data); if(isFailureStatus(s.data)||String(s.data?.stage||'').toLowerCase().startsWith('results_ready')||String(s.data?.run_status||'').toLowerCase()==='completed'){loadRepositoryArchive(); return;} presenceTimer.current=setTimeout(tick,POLL_MS);} tick(); return()=>{stopped=true; if(presenceTimer.current)clearTimeout(presenceTimer.current);};},[presenceRunId,loadRepositoryArchive]);

  async function handleReferralFile(file: File){
    setReferralFile(file); setReferralError(''); setReferralMessage('');
    const text = await file.text();
    const rows = parseCsv(text).map(row => ({
      ngo_name: field(row, 'ngo_name', 'NGO Name', 'name', 'Organisation', 'organization'),
      district: field(row, 'district', 'District'),
      contact_number: field(row, 'contact_number', 'Contact Number', 'phone', 'Phone'),
      website: field(row, 'website', 'Website', 'url'),
      referred_by: field(row, 'referred_by', 'Referred By', 'referral_source', 'source'),
      comments: field(row, 'comments', 'Comments', 'notes', 'Notes'),
      information_status: field(row, 'website', 'Website', 'url') ? 'Sufficient' : 'Needs Follow-up',
      curation_status: 'pending_review',
    })).filter(row => row.ngo_name);
    if(!rows.length){setReferralRows([]); setReferralSelected({}); setReferralError('CSV must include ngo_name/name.'); return;}
    setReferralRows(rows);
    setReferralSelected(Object.fromEntries(rows.map((_,i)=>[i,true])));
  }
  function updateReferralRow(idx:number, patch:AnyRow){
    setReferralRows(rows => rows.map((row,i)=>i===idx?{...row,...patch}:row));
  }
  function selectedReferralRows(){
    return referralRows.filter((_,i)=>referralSelected[i] !== false);
  }
  async function saveReferrals(){
    const rows = selectedReferralRows();
    if(!rows.length){ setReferralMessage('Select at least one referral row.'); return; }
    await sendRowsToLeadPool(rows.map(row => ({...row, source_type:'Human Referral', notes: row.comments, reviewer_comments: row.comments})), 'Human Referral');
  }
  async function searchReferralWebsites(){
    if(!SEARCH_BACKEND||!referralRows.length)return;
    setReferralSearching(true); setReferralError('');
    const fd = new FormData();
    fd.append('file', new File([referralRowsToCsv(referralRows, state)], 'referral_website_search.csv', {type:'text/csv'}));
    const r=await safeJSON(`${SEARCH_BACKEND}/repository/start?mode=bulk`,{method:'POST',body:fd});
    setReferralSearching(false);
    if(!r.ok||!r.data){setReferralError(r.error||'Could not start website search.'); return;}
    setRepoRunId(r.data.run_id); setRepoPolling(true); setView('internet'); setTab('bulk'); loadRepositoryArchive();
  }

  const discRows:AnyRow[]=discResults?.stories||discResults?.rows||[];
  const discDownloads=discResults?.downloads||discStatus?.downloads||{};
  const repoRows:AnyRow[]=repoResults?.rows||[];
  const repoDownloads=repoResults?.downloads||repoStatus?.downloads||{};
  const presenceRows:AnyRow[]=presenceResults?.rows||[];
  const presenceDownloads=presenceResults?.downloads||presenceStatus?.downloads||{};
  const currentDisc=discStatus?.current_search||discStatus?.current_url||discStatus?.current_item||'Waiting to start';
  const currentRepo=repoStatus?.current_search||repoStatus?.current_url||repoStatus?.current_item||'Waiting to start';
  const recoveryActiveElapsed=Math.max(0,Number(recoveryStatus?.active_elapsed_sec||0)) + (recoveryStatus?.current_item_started_at_epoch?Math.max(0,Number(recoveryStatus?.current_item_elapsed_sec||0)):0);
  const recoveryEtaSeconds=finiteNumber(recoveryStatus?.eta_seconds);
  const recoveryRate=finiteNumber(recoveryStatus?.throughput_rows_per_min);
  const recoveryProgress=finiteNumber(recoveryStatus?.progress_pct);
  const recoveryStrategy=String(recoveryStatus?.strategy||'fast').toLowerCase();
  const recoveryStrategyName=recoveryStrategyLabel(recoveryStrategy);
  const deepReviewCount=Math.max(0,Number(recoveryStatus?.deep_review_count??recoveryStatus?.file_counts?.deep_review_input??recoveryStatus?.summary?.deep_review_rows??0)||0);
  const recoveryIsLive=String(recoveryStatus?.process_state||'').toLowerCase()==='running'||activeWord(recoveryStatus?.run_status)||activeWord(recoveryStatus?.stage);
  const recoveryDeepReviewReady=recoveryStrategy==='fast'&&deepReviewCount>0&&deepReviewReady(recoveryStatus);
  const deepSourceRuns=repoArchive.filter(raw=>{
    const row=isRecord(raw)?raw:{};
    return String(row.module||'').toLowerCase()==='no_website_recheck'&&String(row.strategy||'').toLowerCase()==='fast'&&Math.max(0,Number(row.deep_review_count||0)||0)>0&&deepReviewReady(row);
  });
  const recoveryLastContactAge=recoveryLastContactAt==null?null:Math.max(0,Math.floor((statusClock-recoveryLastContactAt)/1000));
  const runsLastContactAge=runsLastContactAt==null?null:Math.max(0,Math.floor((statusClock-runsLastContactAt)/1000));
  const curationOf = (r:AnyRow) => String(field(r,'curation_status','Curation Status') || 'pending_review').toLowerCase();
  const approvedLeads = leadPool.filter(r => ['approved_for_ranking','approved_with_comment'].includes(curationOf(r)));
  const pendingLeads = leadPool.filter(r => !['approved_for_ranking','approved_with_comment','needs_follow_up'].includes(curationOf(r)));
  const followupLeads = leadPool.filter(r => curationOf(r)==='needs_follow_up');
  const visibleLeads = poolView==='approved' ? approvedLeads : poolView==='followup' ? followupLeads : poolView==='all' ? leadPool : pendingLeads;

  function renderHistory(){
    const diskPct=finiteNumber(disk?.volume_used_pct);
    const diskRuns=finiteNumber(disk?.runs_data_mb);
    const diskFree=finiteNumber(disk?.volume_free_mb);
    return <div className="collapse-body advanced-history">
      <div className="archive-toolbar"><button className="quiet-btn" onClick={()=>{loadDiscoveryArchive(); loadRepositoryArchive(); loadDiskUsage();}}>Refresh</button>{disk&&<span className={`disk-badge ${(diskPct??0)>80?'hot':''}`}>Disk {diskPct==null?'—':`${diskPct}%`} · {diskRuns==null?'—':`${diskRuns}MB`} runs · {diskFree==null?'—':`${diskFree}MB`} free</span>}{SEARCH_BACKEND&&<a className="dark-download ready" href={`${SEARCH_BACKEND}/repository/export/global/history`}>Global history</a>}</div>
      <div className="history-subtitle">General Discovery</div>
      <ArchiveListBoundary label="General Discovery" onRetry={loadDiscoveryArchive}>
        <div className="archive-list">
          {!discArchiveLoaded&&<div className="muted-empty">Loading discovery runs…</div>}
          {discArchiveError&&<div className="muted-empty">Could not load discovery runs: {discArchiveError}</div>}
          {discArchiveLoaded&&!discArchiveError&&discArchive.length===0&&<div className="muted-empty">No discovery runs found yet.</div>}
          {discArchive.slice(0,100).map((raw,i)=>{const r=isRecord(raw)?raw:{}; const id=displayScalar(r.run_id,''); const dl=isRecord(r.downloads)?r.downloads:{}; const legacy=r.module==='legacy_story'; return <div className="archive-row" key={`${id||'discovery'}-${i}`}><div><b>{sentRunIds[id]&&<span className="sent-star" title="Sent to Lead Pool">★</span>}{legacy?'Legacy Story Discovery':'General Discovery'} — {displayScalar(r.state,'Statewide')}</b><small>{displayScalar(r.updated_at)} · {displayScalar(r.run_mode,'run')} · {displayScalar(r.processed,'0')}/{displayScalar(r.total,'0')} queries · surfaced {displayScalar(r.stories_found,'0')}</small></div><div className="archive-links">{!!dl.stories&&id&&<a href={discoveryDownload(id,'leads')}>{legacy?'Output':'Clean output'}</a>}<button disabled={poolBusy||!id} onClick={()=>id&&sendRunToLeadPool(id,legacy?'legacy_story':'discovery','Archive Import')}>Send to Lead Pool</button>{!!dl.audit&&id&&<a href={discoveryDownload(id,'audit')}>Audit</a>}{!!dl.rejected&&id&&<a href={discoveryDownload(id,'rejected')}>Rejected</a>}{!!dl.candidates&&id&&<a href={discoveryDownload(id,'candidates')}>Reviewed</a>}{!!dl.raw_candidates&&id&&<a href={discoveryDownload(id,'raw_candidates')}>Raw</a>}{!!dl.queries&&id&&<a href={discoveryDownload(id,'queries')}>Queries</a>}{!!dl.errors&&id&&<a href={discoveryDownload(id,'errors')}>Errors</a>}{(r.stage==='paused'||r.run_status==='paused')&&id&&<button onClick={()=>resumeDiscovery(id)}>Resume</button>}<button className="archive-del" title="Delete run to free disk" disabled={!id} onClick={()=>id&&deleteRun(id)}>Delete</button></div></div>;})}
        </div>
      </ArchiveListBoundary>
      <div className="history-subtitle">Bulk / Recovery</div>
      <ArchiveListBoundary label="Bulk / Recovery" onRetry={loadRepositoryArchive}>
        <div className="archive-list">
          {!repoArchiveLoaded&&<div className="muted-empty">Loading bulk and recovery runs…</div>}
          {repoArchiveError&&<div className="muted-empty">Could not load bulk runs: {repoArchiveError}</div>}
          {repoArchiveLoaded&&!repoArchiveError&&repoArchive.length===0&&<div className="muted-empty">No bulk runs found yet.</div>}
          {repoArchive.slice(0,100).map((raw,i)=>{
            const r=isRecord(raw)?raw:{};
            const id=displayScalar(r.run_id,'');
            const dl=isRecord(r.downloads)?r.downloads:{};
            const moduleName=displayScalar(r.module,'');
            const isPresence=moduleName==='ngo_presence_check';
            const isRecovery=moduleName==='no_website_recheck';
            const archivedDeepCount=Math.max(0,Number(r.deep_review_count||0)||0);
            const title=isPresence?'NGO Presence Check':isRecovery?recoveryStrategyLabel(r.strategy):(r.run_type==='dedupe_recheck'?'Deduped NGO re-check':'Bulk Discovery');
            return <div className="archive-row" key={`${id||'repository'}-${i}`}>
              <div><b>{sentRunIds[id]&&<span className="sent-star" title="Sent to Lead Pool">★</span>}{title}</b><small>{displayScalar(r.updated_at)} · {id||'unknown run'} · {displayScalar(r.stage||r.run_status)} · rows {displayScalar(r.results_count||r.repository_count,'0')} · audit {displayScalar(r.audit_count,'0')}{isRecovery&&archivedDeepCount>0?` · deep review ${archivedDeepCount}`:''}</small></div>
              <div className="archive-links">
                {!!dl.repository&&id&&<a href={archiveDownload(r,'repository')}>Shortlist</a>}
                {!!dl.results&&id&&<a href={archiveDownload(r,'results')}>{isPresence?'Presence CSV':isRecovery?(String(r.strategy||'').toLowerCase()==='deep'?'Deep Results':'Fast Results'):'Results'}</a>}
                {!isPresence&&<button disabled={poolBusy||!id} onClick={()=>id&&sendRunToLeadPool(id,isRecovery?'no_website_recheck':'repository','Archive Import')}>Send to Lead Pool</button>}
                {isRecovery&&String(r.strategy||'').toLowerCase()==='fast'&&archivedDeepCount>0&&id&&<button disabled={deepReviewBusy||recoveryIsLive||!deepReviewReady(r)} title={!deepReviewReady(r)?'Finish or end the Fast Recovery pass first':recoveryIsLive?'Wait for the active recovery run to finish':''} onClick={()=>startDeepReview(id)}>Send to Deep Review ({archivedDeepCount})</button>}
                {isRecovery&&String(r.strategy||'').toLowerCase()==='fast'&&archivedDeepCount>0&&!!dl.deep_review_input&&id&&<a href={archiveDownload(r,'deep_review_input')}>Deep queue</a>}
                {!!dl.summary&&id&&<a href={archiveDownload(r,'summary')}>Summary</a>}
                {!!dl.skipped&&id&&<a href={archiveDownload(r,'skipped')}>Skipped</a>}
                {!!dl.audit&&id&&<a href={archiveDownload(r,'audit')}>Audit</a>}
                {!!dl.rejected&&id&&<a href={archiveDownload(r,'rejected')}>Rejected</a>}
                {!!dl.duplicates&&id&&<a href={archiveDownload(r,'duplicates')}>Dedupe audit</a>}
                {!!dl.errors&&id&&<a href={archiveDownload(r,'errors')}>Errors</a>}
                {!!dl.history&&id&&<a href={archiveDownload(r,'history')}>History</a>}
                <button className="archive-del" title="Delete run to free disk" disabled={!id} onClick={()=>id&&deleteRun(id)}>Delete</button>
              </div>
            </div>;
          })}
        </div>
      </ArchiveListBoundary>
    </div>;
  }

  function renderLeadPool(){
    return <section className="table-card lead-pool-card">
      <div className="table-title"><b>Lead Pool</b><span>{approvedLeads.length} approved · {pendingLeads.length} pending · {followupLeads.length} follow-up</span></div>
      <div className="lead-pool-actions">
        <button className={poolView==='pending'?'quiet-btn active':'quiet-btn'} onClick={()=>setPoolView('pending')}>Pending</button>
        <button className={poolView==='approved'?'quiet-btn active':'quiet-btn'} onClick={()=>setPoolView('approved')}>Approved Leads</button>
        <button className={poolView==='followup'?'quiet-btn active':'quiet-btn'} onClick={()=>setPoolView('followup')}>Follow-up</button>
        <button className={poolView==='all'?'quiet-btn active':'quiet-btn'} onClick={()=>setPoolView('all')}>All</button>
        <button className="quiet-btn" onClick={loadLeadPool}>Refresh</button>
        <button className="quiet-btn" onClick={downloadManualLeadSampleCsv}>Sample New Leads CSV</button>
        <input ref={leadImportRef} type="file" accept=".csv" hidden onChange={e=>importLeadPoolCsv(e.target.files?.[0]||null)} />
        <button className="quiet-btn" onClick={()=>leadImportRef.current?.click()}>Import New Leads CSV</button>
        <button className="quiet-btn" onClick={downloadShortlistDecisionCsv}>Download Shortlisting CSV</button>
        <input ref={shortlistImportRef} type="file" accept=".csv" hidden onChange={e=>importShortlistDecisionCsv(e.target.files?.[0]||null)} />
        <button className="quiet-btn" onClick={()=>shortlistImportRef.current?.click()}>Import Shortlisting CSV</button>
        {BACKEND&&<a className="dark-download ready" href={`${BACKEND}/workspace/${encodeURIComponent(state)}/lead-pool/export.csv`}>Download CSV</a>}
        <select className="lead-ranking-select" value={rankingTarget} onChange={e=>setRankingTarget(e.target.value)} title="Ranking assignment">
          <option value="everyone">Send to everyone</option>
          <option value="split">Split across PMs</option>
          {PM_NAMES.map(pm=><option key={pm} value={pm}>Send to {pm}</option>)}
        </select>
        <input className="lead-admin-password" type="password" value={rankingPassword} onChange={e=>setRankingPassword(e.target.value)} placeholder="Admin password" />
        <button className="primary-red small-red" disabled={poolBusy||!approvedLeads.length||!rankingPassword.trim()} onClick={sendLeadPoolToRanking}>Send for PM Shortlisting</button>
        {poolView==='approved'&&<button className="danger-btn" disabled={poolBusy||!approvedLeads.length||!rankingPassword.trim()} onClick={deleteAllApprovedLeads}>Delete all approved</button>}
        <button className="danger-btn" disabled={poolBusy||!visibleLeads.length} onClick={()=>deleteLeads({lead_ids: visibleLeads.map(r => r.lead_id).filter(Boolean)})}>Delete visible</button>
      </div>
      <AdminUndoRedo region={state} context="Lead Pool recovery" onRestored={loadLeadPool} />
      <div className="pool-helper">Old PM shortlist work is protected. Tags and shortlisting comments are required only for new leads moving forward; already assigned or reviewed NGOs are skipped, not overwritten. Use undo if a lead is sent forward, deleted, or imported by mistake.</div>
      {poolMessage&&<div className="pool-message">{poolMessage}</div>}
      <div className="scroll-table compact-pool"><table><thead><tr><th>NGO</th><th>Source</th><th>Source tag</th><th>Status</th><th>Website</th><th>Understanding</th><th>Contact</th><th>Shortlisting comment</th><th>Actions</th></tr></thead><tbody>{visibleLeads.length?visibleLeads.slice(0,120).map((r,i)=><tr key={r.lead_id||i}><td><b>{rowName(r)}</b><small>{rowLocation(r)||'—'}</small></td><td><span className="tag">{field(r,'source_mix','source_type','Source')||'—'}</span></td><td><span className="tag">{rowSourceTag(r)||'—'}</span></td><td><span className="tag">{rowStatus(r)||'pending_review'}</span><small>{rowInfoStatus(r)||''}</small></td><td><ExternalLink value={rowWebsite(r)}>open</ExternalLink></td><td>{rowOneLine(r)||'—'}</td><td>{rowContact(r)||'—'}</td><td>{rowShortlistingComment(r)||'—'}</td><td className="lead-actions"><button onClick={()=>sendForShortlisting(r)}>Send for shortlisting</button><button onClick={()=>curateLead(r,'needs_follow_up',true)}>Follow-up</button><button onClick={()=>curateLead(r,'sent_back_to_pool',true)}>Send back</button><button onClick={()=>editLead(r)}>Edit</button><button onClick={()=>{const comments=window.prompt('Add shortlisting comment', rowShortlistingComment(r)||rowNote(r)||''); if(comments!==null)updateLead(r,{shortlisting_comment:comments, curation_comment:comments,reviewer_comments:comments, notes: comments});}}>Comment</button><button onClick={()=>deleteLeads({lead_ids:[r.lead_id]})}>Delete</button></td></tr>):<tr><td colSpan={9}>No leads in this bucket.</td></tr>}</tbody></table></div>
    </section>;
  }

  return <><Header active="repository"/><main className="dfp-wrap page-stack discovery-revamp">
    <section className="module-hero discovery-hero"><div className="red-kicker">NGO Discovery Module</div><h1>Find the best <span>NGOs</span></h1><p>For internal use only</p><div className="hero-dots"/></section>

    {view==='source'&&<>
      <section className="state-gate-card">
        <div>
          <span className="red-kicker compact-kicker">Select region</span>
          <h2>{state}</h2>
        </div>
        <label>
          <span>State / UT</span>
          <select value={state} onChange={e=>setState(e.target.value)}>
            {states.map(s=><option key={s}>{s}</option>)}
          </select>
        </label>
      </section>
      <section className="source-choice-grid three-choice">
        <button className="source-choice-card" onClick={()=>setView('internet')}><span>01</span><b>Internet Leads</b><small>General Discovery, Bulk Discovery, History</small></button>
        <button className="source-choice-card" onClick={()=>setView('referrals')}><span>02</span><b>Referrals</b><small>Upload referral CSV, enrich, comment, save selected</small></button>
        <button className="source-choice-card leadpool-entry" onClick={()=>setView('leadpool')}><span>03</span><b>Go to Lead Pool</b><small>Approve leads, follow-ups, and send approved leads to ranking</small></button>
      </section>
    </>}

    {view==='internet'&&<>
      <div className="source-topline"><button className="quiet-btn" onClick={()=>setView('source')}>← Back</button><span>Internet Leads</span><button className="gear-btn" onClick={()=>setAdvancedOpen(!advancedOpen)}>⚙ Advanced</button></div>
      <section className="discover-card">
        <div className="mode-tabs"><button className={tab==='general'?'active':''} onClick={()=>setTab('general')}>General Discovery <small>find new</small></button><button className={tab==='bulk'?'active':''} onClick={()=>setTab('bulk')}>Bulk Discovery <small>csv</small></button></div>
        {tab==='general'&&<>
          <div className="story-top-controls no-state-control"><div className="selected-state-chip"><span>Selected State</span><b>{state}</b></div><label><span>Run mode</span><select value={runMode} onChange={e=>onModeChange(e.target.value)}>{runModes.map(m=><option key={m.key} value={m.key}>{m.label}</option>)}</select></label><label><span>Query budget</span><input type="number" min={1} max={MAX_DISCOVERY_BUDGET} value={budget} onChange={e=>{setRunMode('custom');setBudget(Math.max(1,Math.min(MAX_DISCOVERY_BUDGET,Number(e.target.value||1))));}}/></label><button className="primary-red" disabled={discStarting||discPolling} onClick={startDiscovery}>{discStarting?'Starting…':'Start General Discovery'}</button>{discRunId&&discPolling&&<button className="ghost-btn" onClick={pauseDiscovery}>Pause</button>}{discRunId&&discPolling&&<button className="ghost-btn" onClick={cancelDiscovery}>Cancel</button>}</div>
          <div className="budget-strip"><b>{Number(budget||0).toLocaleString()} / {MAX_DISCOVERY_BUDGET.toLocaleString()} query cap</b><span>{runModes.find(m=>m.key===runMode)?.note || 'Manual query budget'}</span></div>
          <div className="category-grid-dark compact-categories">{pathwayOptions.map(p=><button key={p.key} className={pathways.includes(p.key)?'active':''} onClick={()=>togglePathway(p.key)}><i>{pathways.includes(p.key)?'✓':'+'}</i><span><b>{p.label}</b><small>{p.note}</small></span></button>)}</div>
          {discError&&<div className="error-box">{discError}</div>}
        </>}
        {tab==='bulk'&&<>
          <div className="form-card minimal-upload"><label>Upload NGO CSV</label><div className="upload-box" onClick={()=>bulkRef.current?.click()}><strong>{bulkCSV?bulkCSV.name:'Upload NGO CSV'}</strong><span>Required: name, district</span><small>{state} is applied from selected region.</small></div><input ref={bulkRef} type="file" accept=".csv" hidden onChange={e=>setBulkCSV(e.target.files?.[0]||null)}/><button className="sample-btn" onClick={()=>downloadText('bulk_discovery_sample.csv','name,district\nShanti Bhavan,Bengaluru\nMahesh Foundation,Belagavi\n')}>Sample Human Leads CSV</button><div className="action-row" style={{padding:'14px 0 0'}}><button className="primary-red" disabled={repoStarting||repoPolling} onClick={()=>startRepository('bulk')}>{repoStarting?'Starting…':'Run Bulk Discovery'}</button>{repoRunId&&repoPolling&&<button className="ghost-btn" onClick={stopRepository}>Stop</button>}</div></div>
          {repoError&&<div className="error-box">{repoError}</div>}
        </>}
      </section>

      {advancedOpen&&<section className="advanced-shell"><div className="advanced-head"><b>Advanced settings</b><button className="quiet-btn" onClick={()=>setHistoryOpen(!historyOpen)}>{historyOpen?'Hide History':'History'}</button></div>
        <div id="run-panel-recovery" className="recovery-panel recovery-workspace">
          <div className="recovery-workspace-head"><div><b>Website Recovery</b><span className="advanced-help">Choose Fast or Deep before uploading. Each run is independent, checkpointed, pausable, resumable and immediately downloadable.</span></div>{recoveryRunId&&<span className="tag">Active view: {recoveryStrategyName}</span>}</div>
          <div className="recovery-mode-grid">
            <div className="recovery-mode-card fast-mode">
              <div><span className="recovery-mode-kicker">Mode 1</span><h4>Fast Recovery</h4><p>Broad first pass for the full CSV. Two searches, bounded verification and selective Firecrawl only when the strongest candidate cannot be read directly.</p></div>
              <input ref={fastRecoveryRef} type="file" accept=".csv" hidden onChange={e=>setFastRecoveryCSV(e.target.files?.[0]||null)}/>
              <div className="recovery-mode-actions"><button className="ghost-btn" onClick={()=>fastRecoveryRef.current?.click()}>{fastRecoveryCSV?fastRecoveryCSV.name:'Upload Fast CSV'}</button><button className="primary-red small-red" disabled={recoveryBusy||!fastRecoveryCSV||recoveryIsLive} onClick={()=>startRecovery('fast',fastRecoveryCSV)}>{recoveryBusy?'Starting…':'Start Fast Recovery'}</button></div>
              <small>Fast results remain separate and can be downloaded while the run is active, after Pause, after Cancel, after End, or on completion.</small>
            </div>
            <div className="recovery-mode-card deep-mode">
              <div><span className="recovery-mode-kicker">Mode 2</span><h4>Deep Recovery</h4><p>Full-depth verification with more candidates, more identity pages, rename recovery and selective Firecrawl. Start from a new CSV or eligible rows from an earlier Fast run.</p></div>
              <input ref={deepRecoveryRef} type="file" accept=".csv" hidden onChange={e=>setDeepRecoveryCSV(e.target.files?.[0]||null)}/>
              <div className="recovery-mode-actions"><button className="ghost-btn" onClick={()=>deepRecoveryRef.current?.click()}>{deepRecoveryCSV?deepRecoveryCSV.name:'Upload Deep CSV'}</button><button className="primary-red small-red" disabled={recoveryBusy||!deepRecoveryCSV||recoveryIsLive} onClick={()=>startRecovery('deep',deepRecoveryCSV)}>{recoveryBusy?'Starting…':'Start Deep Recovery'}</button></div>
              <div className="deep-source-row"><span>or use a previous Fast run</span><select value={deepSourceRunId} onChange={e=>setDeepSourceRunId(e.target.value)}><option value="">Select eligible Fast run</option>{deepSourceRuns.map((raw,i)=>{const row=isRecord(raw)?raw:{};const id=displayScalar(row.run_id,'');const count=Math.max(0,Number(row.deep_review_count||0)||0);return id?<option value={id} key={`${id}-${i}`}>{id} · {count} eligible</option>:null;})}</select><button className="ghost-btn" disabled={deepReviewBusy||!deepSourceRunId||recoveryIsLive} onClick={()=>startDeepReview(deepSourceRunId)}>{deepReviewBusy?'Starting…':'Start eligible Deep Review'}</button></div>
              <small>Only rows explicitly set aside by Fast Recovery are sent. The original Fast result file remains unchanged and downloadable.</small>
            </div>
          </div>
          {recoveryRunId&&<div className="recovery-live-strip">
            <div className="recovery-live-title"><b>{recoveryStrategyName}</b><code>{recoveryRunId}</code></div>
            <div className="recovery-live-actions">
              {recoveryStatus?.can_pause&&<button className="ghost-btn" onClick={pauseRecovery}>Pause</button>}
              {recoveryStatus?.can_resume&&recoveryStrategy==='smart'&&<><button className="primary-red small-red" onClick={()=>resumeRecovery('fast')}>Resume Fast</button><button className="ghost-btn" onClick={()=>resumeRecovery('deep')}>Resume Deep</button></>}
              {recoveryStatus?.can_resume&&recoveryStrategy!=='smart'&&<button className="ghost-btn" onClick={()=>resumeRecovery(recoveryStrategy)}>Resume {recoveryStrategy==='deep'?'Deep Recovery':'Fast Recovery'}</button>}
              {recoveryStatus?.can_cancel&&<button className="ghost-btn danger" onClick={cancelRecovery}>Cancel</button>}
              {recoveryStatus?.can_stop&&<button className="ghost-btn danger" onClick={stopRecovery}>End &amp; save</button>}
            </div>
            <div className="recovery-downloads">
              {(recoveryStatus?.partial_outputs_available||recoveryStatus?.downloads?.results)&&<a className="dark-download ready" href={recheckDownload(recoveryRunId,'results')}>Download {recoveryStrategy==='deep'?'Deep':'Fast'} Results</a>}
              {(recoveryStatus?.partial_outputs_available||recoveryStatus?.downloads?.results)&&<button className="dark-download ready" disabled={poolBusy} onClick={()=>sendRunToLeadPool(recoveryRunId,'no_website_recheck',recoveryStrategyName)}>Send current results to Lead Pool</button>}
              {recoveryStrategy==='fast'&&deepReviewCount>0&&recoveryDeepReviewReady&&<button className="primary-red small-red" disabled={deepReviewBusy||recoveryIsLive} onClick={()=>startDeepReview(recoveryRunId)}>{deepReviewBusy?'Starting Deep Review…':`Send eligible to Deep Review (${deepReviewCount})`}</button>}
              {recoveryStrategy==='fast'&&deepReviewCount>0&&recoveryStatus?.downloads?.deep_review_input&&<a className="dark-download ready" href={recheckDownload(recoveryRunId,'deep_review_input')}>Eligible Deep CSV</a>}
              {recoveryStatus?.downloads?.audit&&<a className="dark-download ready" href={recheckDownload(recoveryRunId,'audit')}>Audit</a>}
              {recoveryStatus?.downloads?.summary&&<a className="dark-download ready" href={recheckDownload(recoveryRunId,'summary')}>Summary</a>}
              {recoveryStatus?.downloads?.skipped&&<a className="dark-download ready" href={recheckDownload(recoveryRunId,'skipped')}>Skipped</a>}
              {recoveryStatus?.downloads?.errors&&<a className="dark-download ready" href={recheckDownload(recoveryRunId,'errors')}>Errors</a>}
            </div>
            {recoveryStatus&&<small className="recovery-stat">{recoveryStatus.run_status||recoveryStatus.stage} · {recoveryStatus.processed||0}/{recoveryStatus.total||0} done · {recoveryStatus.remaining??Math.max(0,(recoveryStatus.total||0)-(recoveryStatus.processed||0))} left{recoveryProgress!=null?` · ${recoveryProgress.toFixed(1)}%`:''}</small>}
            {recoveryStatus&&<small className="recovery-stat">Active elapsed: {formatDuration(recoveryActiveElapsed)}{recoveryRate!=null&&recoveryRate>0?` · ${recoveryRate.toFixed(2)} rows/min`:''}{recoveryEtaSeconds!=null?` · about ${formatDuration(recoveryEtaSeconds)} remaining`:''}</small>}
            {recoveryStatus?.current_item_started_at_epoch&&<small className="recovery-stat">Current NGO: {recoveryStatus.current_item||'Processing'} · {formatDuration(recoveryStatus.current_item_elapsed_sec)}{recoveryStatus.row_near_deadline&&recoveryStatus.row_deadline_remaining_sec!=null?` · watchdog skips in ≤${Math.ceil(Number(recoveryStatus.row_deadline_remaining_sec))}s`:''}</small>}
            {recoveryStrategy==='fast'&&deepReviewCount>0&&<small className="recovery-stat">{deepReviewCount} NGO{deepReviewCount===1?'':'s'} set aside for optional Deep Review. This does not delay or replace the Fast results.</small>}
            {Number(recoveryStatus?.row_timeouts||0)>0&&<small className="recovery-stat">{recoveryStatus.row_timeouts} slow NGO{Number(recoveryStatus.row_timeouts)===1?'':'s'} safely checkpointed for review.</small>}
            {recoveryStatus&&<small className="recovery-credits">Serper {recoveryStatus.queries_used||0}{recoveryStatus.firecrawl_credits||recoveryStatus.firecrawl_credits_used?` · Firecrawl ${recoveryStatus.firecrawl_credits||recoveryStatus.firecrawl_credits_used||0}`:''}{recoveryStatus.eta_at?` · ETA ${new Date(recoveryStatus.eta_at).toLocaleTimeString()}`:''}</small>}
            <small className="recovery-stat">Runs in the Railway worker. Refreshing, shortlisting, changing pages or closing this tab does not stop them.</small>
            {recoveryConnectionError&&<span className="connection-warning">{recoveryConnectionError}{recoveryLastContactAge!=null?` Last successful update ${formatDuration(recoveryLastContactAge)} ago.`:''}</span>}
            {recoveryError&&<span className="mini-error">{recoveryError}</span>}
          </div>}
          {!recoveryRunId&&recoveryError&&<span className="mini-error">{recoveryError}</span>}
        </div>
        <div id="run-panel-presence" className="recovery-panel"><b>NGO Presence Check</b><span className="advanced-help">CSV columns: ngo_name, state, center_name optional. Checks only correct official website identity + digital presence strength. No child/program-fit scoring.</span><input ref={presenceRef} type="file" accept=".csv" hidden onChange={e=>setPresenceCSV(e.target.files?.[0]||null)}/><button className="ghost-btn" onClick={()=>presenceRef.current?.click()}>{presenceCSV?presenceCSV.name:'Upload CSV'}</button><button className="ghost-btn" onClick={downloadPresenceSampleCsv}>Sample CSV</button><button className="primary-red small-red" disabled={presenceBusy||!presenceCSV} onClick={startPresenceCheck}>{presenceBusy?'Starting…':'Run Presence Check'}</button>{presenceRunId&&<button className="ghost-btn" onClick={cancelPresenceCheck}>Cancel</button>}{presenceRunId&&<a className="dark-download ready" href={presenceDownload(presenceRunId,'results')}>Presence CSV</a>}{presenceRunId&&<a className="dark-download ready" href={presenceDownload(presenceRunId,'summary')}>Summary</a>}{presenceRunId&&<a className="dark-download ready" href={presenceDownload(presenceRunId,'audit')}>Audit</a>}{presenceStatus&&<small>{presenceStatus.stage||presenceStatus.run_status} · {presenceStatus.processed||0}/{presenceStatus.total||0} NGOs · rows {presenceStatus.rows_ready||presenceRows.length||0} · queries {presenceStatus.queries_used||0}</small>}{presenceError&&<span className="mini-error">{presenceError}</span>}</div>
        {presenceRows.length>0&&<div className="scroll-table presence-preview-table"><table><thead><tr><th>NGO</th><th>Center</th><th>State</th><th>Website</th><th>Confidence</th><th>Strength</th><th>Assessment</th></tr></thead><tbody>{presenceRows.slice(0,30).map((r,i)=><tr key={i}><td>{rowName(r)||field(r,'NGO Name')}</td><td>{field(r,'Center Name')||'—'}</td><td>{field(r,'State')||'—'}</td><td><ExternalLink value={field(r,'Official Website','Website')}>open</ExternalLink></td><td><span className={confidenceClass(field(r,'Website Confidence'))}>{field(r,'Website Confidence')||'—'}</span></td><td>{field(r,'Website Strength')||'—'}</td><td>{field(r,'Digital Presence Assessment')||'—'}</td></tr>)}</tbody></table></div>}
        {historyOpen&&renderHistory()}
      </section>}

      {(discStatus||discPolling)&&tab==='general'&&<section id="run-panel-discovery" className="status-card"><div className="status-dot"/><div><b>{discStatus?.stage||(discPolling?'Starting…':'Waiting')}</b><p>{currentDisc}</p></div><div className="status-grid"><StatBox label="State" value={state}/><StatBox label="Queries used" value={discStatus?.processed??0}/><StatBox label="Budget" value={discStatus?.total??budget}/><StatBox label="Sources" value={discStatus?.links_found??'—'}/><StatBox label="Organisations" value={discStatus?.stories_found??discRows.length}/></div></section>}
      {(repoStatus||repoPolling)&&tab==='bulk'&&<section id="run-panel-repository" className="status-card"><div className="status-dot"/><div><b>{repoStatus?.stage||(repoPolling?'Starting…':'Waiting')}</b><p>{currentRepo}</p></div><div className="status-grid"><StatBox label="Mode" value="bulk"/><StatBox label="Processed" value={repoStatus?.processed??0}/><StatBox label="Total" value={repoStatus?.total??'—'}/><StatBox label="Ready for AI" value={repoStatus?.ready_for_ai??'—'}/><StatBox label="Errors" value={repoStatus?.errors??'—'}/></div></section>}
      {tab==='general'&&discRunId&&<div className="download-row"><DownloadButton ready={!!discDownloads.stories||!!discDownloads.story_csv} href={discoveryDownload(discRunId,'leads')}>Clean output CSV</DownloadButton><button className="dark-download ready" disabled={poolBusy} onClick={()=>sendRunToLeadPool(discRunId,'discovery','Internet Discovery')}>Send to Lead Pool</button><DownloadButton ready={!!discDownloads.audit} href={discoveryDownload(discRunId,'audit')}>Audit</DownloadButton><DownloadButton ready={!!discDownloads.rejected} href={discoveryDownload(discRunId,'rejected')}>Rejected</DownloadButton><DownloadButton ready={!!discDownloads.queries} href={discoveryDownload(discRunId,'queries')}>Query plan</DownloadButton></div>}
      {tab==='bulk'&&repoRunId&&<div className="download-row"><DownloadButton ready={!!repoDownloads.repository} href={repositoryDownload(repoRunId,'repository')}>Verified CSV</DownloadButton><button className="dark-download ready" disabled={poolBusy} onClick={()=>sendRunToLeadPool(repoRunId,'repository','Bulk Discovery')}>Send to Lead Pool</button><DownloadButton ready={!!repoDownloads.audit} href={repositoryDownload(repoRunId,'audit')}>Audit</DownloadButton><DownloadButton ready={!!repoDownloads.rejected} href={repositoryDownload(repoRunId,'rejected')}>Rejected</DownloadButton><DownloadButton ready={!!repoDownloads.errors} href={repositoryDownload(repoRunId,'errors')}>Errors</DownloadButton></div>}
      {tab==='general'&&!!discRows.length&&<section className="table-card"><div className="table-title"><b>General Discovery output</b><span>{discRows.length} surfaced leads</span></div><div className="scroll-table"><table><thead><tr><th>Organisation</th><th>Source</th><th>Location</th><th>Pathway</th><th>Why it belongs</th><th>Status</th><th>Confidence</th></tr></thead><tbody>{discRows.slice(0,120).map((r,i)=><DiscoveryRow row={r} key={i}/>)}</tbody></table></div></section>}
      {tab==='bulk'&&!!repoRows.length&&<section className="table-card"><div className="table-title"><b>Bulk Discovery output</b><span>{repoRows.length} rows</span></div><div className="scroll-table"><table><thead><tr><th>Input / NGO</th><th>Website</th><th>Location</th><th>Confidence</th><th>Match</th><th>Note</th></tr></thead><tbody>{repoRows.slice(0,80).map((r,i)=><VerifyRow row={r} key={i}/>)}</tbody></table></div></section>}
    </>}

    {view==='referrals'&&<>
      <div className="source-topline"><button className="quiet-btn" onClick={()=>setView('source')}>← Back</button><span>Referrals</span></div>
      <section className="discover-card referral-card">
        <div className="form-card minimal-upload"><label>Upload Referral CSV</label><div className="upload-box" onClick={()=>referralRef.current?.click()}><strong>{referralFile?referralFile.name:'Upload Referral CSV'}</strong><span>Required: ngo_name, contact_number, referred_by</span><small>Optional: district, website, comments</small></div><input ref={referralRef} type="file" accept=".csv" hidden onChange={e=>{const file=e.target.files?.[0]; if(file)handleReferralFile(file);}}/><button className="sample-btn" onClick={()=>downloadText('referral_sample.csv','ngo_name,contact_number,referred_by,district,website,comments\nExample NGO,9876543210,Avika,Bengaluru,,Spoken to founder\n')}>Sample Human Leads CSV</button></div>
        {referralError&&<div className="error-box">{referralError}</div>}
        {referralRows.length>0&&<><div className="referral-actions"><button className="primary-red" disabled={poolBusy} onClick={saveReferrals}>Save selected</button><button className="ghost-btn" disabled={referralSearching} onClick={searchReferralWebsites}>{referralSearching?'Starting…':'Run enrichment'}</button><button className="dark-download ready" onClick={()=>downloadText('referral_clean_preview.csv', referralRowsToCsv(referralRows, state))}>Export clean CSV</button></div>{referralMessage&&<div className="pool-message">{referralMessage}</div>}<div className="scroll-table referral-preview-table"><table><thead><tr><th>Send</th><th>NGO</th><th>District</th><th>Website</th><th>Contact</th><th>Referred by</th><th>Comment</th><th>Status</th></tr></thead><tbody>{referralRows.map((r,i)=><tr key={i}><td><input type="checkbox" checked={referralSelected[i] !== false} onChange={e=>setReferralSelected(old=>({...old,[i]:e.target.checked}))}/></td><td>{rowName(r)}</td><td>{rowLocation(r)||'—'}</td><td><ExternalLink value={rowWebsite(r)}>open</ExternalLink></td><td>{rowContact(r)||'—'}</td><td>{rowReferredBy(r)||'—'}</td><td><input className="mini-comment-input" value={String(rowNote(r)||'')} onChange={e=>updateReferralRow(i,{comments:e.target.value,notes:e.target.value})} placeholder="Add context"/></td><td><span className="tag">{rowStatus(r)||'preview'}</span></td></tr>)}</tbody></table></div></>}
      </section>
    </>}


    {view==='leadpool'&&<>
      <div className="source-topline"><button className="quiet-btn" onClick={()=>setView('source')}>← Back</button><span>Lead Pool</span><div className="topline-actions"><button className="quiet-btn" onClick={loadLeadPool}>Refresh</button><Link className="primary-red small-red nav-action-link" href="/progress">Go to Rankings</Link></div></div>
      {renderLeadPool()}
    </>}

    <button className={`active-runs-fab ${activeRuns.some(runIsLive)?'live':''}`} onClick={()=>{setRunsOpen(true);loadActiveRuns(false);}} aria-label="Show active runs">
      <span className="active-runs-dot"/><b>{activeRuns.length?`${activeRuns.length} run${activeRuns.length===1?'':'s'}`:'Runs'}</b><small>{runsConnectionLost?'connection lost':activeRuns.some(runIsLive)?'live':'check status'}</small>
    </button>
    {runsOpen&&<div className="active-runs-overlay" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)setRunsOpen(false);}}>
      <aside className="active-runs-drawer" role="dialog" aria-modal="true" aria-label="Active runs">
        <div className="active-runs-head"><div><span>Worker activity</span><h3>Active and paused runs</h3></div><button onClick={()=>setRunsOpen(false)} aria-label="Close active runs">×</button></div>
        <div className="active-runs-subhead"><span>{runsConnectionLost?'Showing the last known state while Railway reconnects.':`Live state is read directly from Railway${runsLastContactAge!=null?` · updated ${formatDuration(runsLastContactAge)} ago`:''}.`}</span><button className="quiet-btn" disabled={runsLoading} onClick={()=>loadActiveRuns(false)}>{runsLoading?'Checking…':runsConnectionLost?'Reconnect':'Refresh'}</button></div>
        {runsError&&<div className="connection-warning active-runs-error">{runsError}</div>}
        {!runsLoading&&!runsConnectionLost&&!activeRuns.length&&<div className="active-runs-empty"><b>No active runs</b><span>Nothing is currently running or paused on the search worker.</span></div>}
        <div className="active-runs-list">{activeRuns.map(run=>{
          const status=run.status; const pct=runProgressPct(run); const done=finiteNumber(status.processed??run.job.processed)??0; const total=finiteNumber(status.total??run.job.total); const live=runIsLive(run); const paused=runIsPaused(run); const canPause=live&&(run.module==='recovery'||run.module==='discovery')&&Boolean(status.can_pause??true); const canResume=!live&&(run.module==='recovery'||run.module==='discovery'||run.module==='repository')&&(paused||Boolean(status.can_resume)); const canCancel=live&&run.module==='recovery'&&Boolean(status.can_cancel??true);
          return <article className="active-run-card" key={run.run_id}>
            <div className="active-run-title"><div><span className={`run-state ${live?'running':paused?'paused':'idle'}`}>{live?'Running':paused?'Paused':'Available'}</span><h4>{run.label}</h4></div><small>{run.service}</small></div>
            <div className="active-run-location">{run.location}</div>
            <div className="active-run-progress"><div><i style={{width:`${pct??0}%`}}/></div><b>{pct!==null?`${pct.toFixed(1)}%`:'Working'}</b></div>
            <div className="active-run-metrics"><span><b>{done.toLocaleString()}</b>{total!==null?` / ${total.toLocaleString()}`:''} completed</span><span><b>{formatDuration(runElapsedSeconds(run))}</b> elapsed</span></div>
            <div className="active-run-current">{displayScalar(status.current_item||status.current_search||status.current_url||status.stage||status.run_status,'Waiting for status')}</div>
            <code>{run.run_id}</code>
            <div className="active-run-actions"><button className="primary-red small-red" onClick={()=>goToRun(run)}>Go to run</button>{canPause&&<button className="ghost-btn" disabled={!!runActionBusy} onClick={()=>controlRun(run,'pause')}>{runActionBusy===`${run.run_id}:pause`?'Pausing…':'Pause'}</button>}{canResume&&<button className="ghost-btn" disabled={!!runActionBusy} onClick={()=>controlRun(run,'resume')}>{runActionBusy===`${run.run_id}:resume`?'Resuming…':'Resume'}</button>}{canCancel&&<button className="ghost-btn danger" disabled={!!runActionBusy} onClick={()=>controlRun(run,'cancel')}>{runActionBusy===`${run.run_id}:cancel`?'Cancelling…':'Cancel'}</button>}{(live||run.module==='recovery')&&<button className="ghost-btn danger" disabled={!!runActionBusy} onClick={()=>controlRun(run,'end')}>{runActionBusy===`${run.run_id}:end`?'Ending…':'End & save'}</button>}</div>
          </article>;
        })}</div>
      </aside>
    </div>}

    <footer className="page-foot">For internal use only</footer>
  </main></>;
}
