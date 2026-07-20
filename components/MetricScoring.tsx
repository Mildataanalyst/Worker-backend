'use client';

import { useMemo } from 'react';

export type MetricKey = 'child_progression' | 'learning_model' | 'development_ecosystem';
export type MetricScore = { rank: number; reason: string };
export type EvidenceLink = { label?: string; url: string };
export type MetricEvidence = {
  text?: string;
  links?: EvidenceLink[];
  ceiling_rank?: number;
  ceiling_reason?: string;
};

type GuideRow = {
  score: number;
  meaning: string;
  exampleTitle: string;
  example: string;
};

type MetricGuide = {
  where: string;
  ceiling: string;
  rows: GuideRow[];
};

export const METRIC_SCALE: Record<number, string> = {
  1: 'Minimal',
  2: 'Limited',
  3: 'Meaningful',
  4: 'Strong',
  5: 'Benchmark-level',
};

export const METRIC_DEFINITIONS: Array<{
  key: MetricKey;
  letter: string;
  title: string;
  question: string;
  guide: MetricGuide;
}> = [
  {
    key: 'child_progression',
    letter: 'A',
    title: 'Alumni Outcomes',
    question: 'What have former students gone on to do?',
    guide: {
      where: 'On the NGO’s website, go to sections such as Alumni, Impact, Success Stories, Higher Education or Careers. Always check the latest annual report.',
      ceiling: 'A few good stories can reach 3. Strong numbers plus named outcomes can reach 4. A broad, repeated alumni track record across several years is required for 5.',
      rows: [
        {
          score: 1,
          meaning: 'The NGO does not show what any former student is doing. It only says children become successful, confident or empowered.',
          exampleTitle: 'Example',
          example: 'The website says “we transform lives” or “children build better futures”, but gives no former-student names, numbers, colleges, jobs or other destinations. The annual report also does not provide any specific alumni outcomes.',
        },
        {
          score: 2,
          meaning: 'Students show progress while they are still inside the programme, but there is little information about what they do after leaving.',
          exampleTitle: 'Akshar Foundation',
          example: 'On their website, go to Schools → Akshar Forum → Learning & Earning. Akshar explains that older students are trained and paid to teach younger children and can earn points for school supplies, clothing and hygiene products. Under Pathway to a Fulfilling Career, it describes vocational skills and guidance towards college, apprenticeships or skilled work. This shows current students building responsibility and earning, but gives little information about what former students do after leaving Akshar.',
        },
        {
          score: 3,
          meaning: 'Some real former-student outcomes are visible, but the evidence covers only a limited group, programme or period.',
          exampleTitle: 'ABM Samaj',
          example: 'On their website, go to the Achievements page and find the section on the Competitive Exams Training Centre for Scheduled Caste Students. ABM reports that 7 of the first 10 students qualified in the IBPS examination and that 45 students were selected for government and private-sector jobs. Employers listed include Mumbai High Court, Railways, MSEDCL, Union Bank of India, HDFC Bank and ICICI Bank. These are real destinations, but the page does not show what happened to the NGO’s wider alumni population over several years.',
        },
        {
          score: 4,
          meaning: 'Strong outcomes are shown through numbers and named examples, covering several stages of former students’ journeys.',
          exampleTitle: 'Kalkeri Sangeet Vidyalaya',
          example: 'In the 2020–21 Annual Report, go to the Higher Education section, pages 15–20. Kalkeri reports 65 students in higher education: 26 in PUC, 28 in undergraduate programmes, 3 in postgraduate programmes and 8 in vocational training. Ten students completed higher education that year. Sangeeta Chavan completed a master’s in English, topped her batch and was expected to join Kalkeri as a teacher. Prakash Chavan completed an MBA and became a Service Delivery Manager at IndusInd Bank. The report also states that 14 of 15 students entered PU college and 85% finishing PUC continued to undergraduate study.',
        },
        {
          score: 5,
          meaning: 'The NGO shows what happens to large numbers of former students across several years, with clear completion and employment results and many named destinations.',
          exampleTitle: 'Shanti Bhavan',
          example: 'On their website, go to the Impact page → Unlimited Potential. Shanti Bhavan reports that 98% of students obtain a college degree and 97% enter full-time employment. On the same page, it lists universities such as Dartmouth, Duke, Middlebury, Stanford, Christ University and Mount Carmel College. Under Companies Employing Graduates, it lists Amazon, Deloitte, EY, Goldman Sachs, Google, KPMG, LinkedIn, Mercedes-Benz, Morgan Stanley, State Street, Verizon and Walmart. This combines overall outcome rates with many named destinations.',
        },
      ],
    },
  },
  {
    key: 'learning_model',
    letter: 'B',
    title: 'Learning Model',
    question: 'What is different or especially strong about how children are taught or trained?',
    guide: {
      where: 'On the NGO’s website, go to sections such as Programme, School, Curriculum, Methodology or Model. Always check the latest annual report.',
      ceiling: 'Ordinary teaching can reach 2. Ordinary teaching with meaningful additional support can reach 3. Several embedded and adapted methods can reach 4. A distinctive method that defines the institution is required for 5.',
      rows: [
        {
          score: 1,
          meaning: 'The NGO says it provides education or training but does not explain how children are taught.',
          exampleTitle: 'Example',
          example: 'The website only says “quality education”, “holistic learning” or “child-centred education”. It does not explain what teachers do, what students do or how the method differs from ordinary classes.',
        },
        {
          score: 2,
          meaning: 'It looks like ordinary teaching: classes, teachers, tests, homework and general support.',
          exampleTitle: 'Example',
          example: 'The website shows a normal school timetable, regular lessons, examinations and teacher support, but no adapted or clearly different teaching method.',
        },
        {
          score: 3,
          meaning: 'Normal teaching has useful additional support, but the basic learning model remains conventional.',
          exampleTitle: 'OM Foundation',
          example: 'On their website, go to Programmes → Holistic Development. OM describes structured lesson plans, regular assessments and additional academic support. Under the Afternoon Programme, it provides a NIOS pathway with timetables, assessments and mentoring. Under Higher Education and Career Support, it lists CUET preparation, application support, résumé training and interview preparation. These are meaningful additions, but the website does not show a distinctive teaching method that fundamentally changes how children learn each day.',
        },
        {
          score: 4,
          meaning: 'Several strong teaching methods are clearly built into the programme and used regularly.',
          exampleTitle: 'Shishu Mandir',
          example: 'On their website, go to English Medium School → Our Methodology. The school states that children are allowed to learn at their own pace and that teachers assess each child’s strengths and weaknesses. Small class sizes allow individual support. Under How the School Works, it describes an extended school day combining academics, arts, sports and skill-building. The page also describes regular teacher training and work with pedagogical experts. The model is strongly adapted, but the institution is not built around one defining method.',
        },
        {
          score: 5,
          meaning: 'The institution is built around a distinctive and intensive method that defines how children learn or train.',
          exampleTitle: 'Kalkeri Sangeet Vidyalaya',
          example: 'On their website, go to What We Do → Performing Arts. Kalkeri states that performing-arts classes run from 8:30 a.m. to 11:30 a.m., five days a week. Older students receive three hours of daily instruction in vocal music, tabla, sitar, violin, dance and drama. Students receive hands-on training and perform through Manch Pradarshan every other Friday. Music and performance are therefore a central daily curriculum, not an extracurricular activity.',
        },
      ],
    },
  },
  {
    key: 'development_ecosystem',
    letter: 'C',
    title: 'Development Environment',
    question: 'What sustained support and opportunities surround the child beyond teaching or training?',
    guide: {
      where: 'On the NGO’s website, go to sections such as Student Welfare, Residential, Health, Counselling, Family Support or Scholarships. Always check the latest annual report.',
      ceiling: 'Basic or occasional services can reach 2. Several regular supports can reach 3. A connected and sustained support system can reach 4. Exceptional daily depth and long-term continuity are required for 5.',
      rows: [
        {
          score: 1,
          meaning: 'Almost nothing is shown beyond the main teaching or training programme.',
          exampleTitle: 'Example',
          example: 'The NGO describes classes or training but does not show any meaningful care, protection, exposure or sustained support around the child.',
        },
        {
          score: 2,
          meaning: 'Support is mainly basic or occasional.',
          exampleTitle: 'Example',
          example: 'The NGO provides free education, books, uniforms, ordinary meals or occasional health camps. These are useful, but they do not form a deeper support system around the child.',
        },
        {
          score: 3,
          meaning: 'Several useful supports exist, but their depth, regularity or connection to one another is limited or unclear.',
          exampleTitle: 'OM Foundation',
          example: 'On their website, go to Programmes → Holistic Development. OM lists nutrition, social-emotional learning and mental-health counselling. Under Enabling Engagement, it describes individual and group mentoring, parent counselling, workshops and home visits. Under Future Progression, it lists scholarships, financial aid and access to education loans. These are meaningful supports, but the website does not clearly show how frequently they occur, how many children receive them or whether they operate as one intensive system.',
        },
        {
          score: 4,
          meaning: 'Different supports and opportunities work together around the child and continue regularly.',
          exampleTitle: 'Akshar Foundation',
          example: 'On their website, go to Schools → Akshar Forum → Learning & Earning. Students earn points that can be exchanged for school supplies, hygiene products and clothing. Under Pathway to Self-Sufficiency, students take responsibility for tutoring, gardening, lunch service, basic first aid, recycling, furniture building and electrical repair. Under Every School a Recycling Centre, students collect plastic, make Eco-bricks and sell recycled products. These activities create a connected environment of responsibility, earning, practical exposure and community participation.',
        },
        {
          score: 5,
          meaning: 'The institution provides exceptionally deep support across daily life and continues supporting the child through major transitions.',
          exampleTitle: 'Kalkeri Sangeet Vidyalaya',
          example: 'On their website, go to the Student Welfare page. Kalkeri describes residential care, three daily meals for more than 300 people, an on-campus infirmary, hospital partnerships and wardens supporting children on campus. In the KSV institutional presentation, it states that students live on campus seven days a week for ten months of the year and continue receiving financial and mentoring support while pursuing higher education. This creates a complete daily and long-term environment around the child.',
        },
      ],
    },
  },
];

export const NGO_REFERENCE_ROWS = [
  { name: 'Shanti Bhavan', model: 'Long-horizon residential', outcomes: 5, learning: 4, environment: 5, basis: 'Impact page and reported college and employment outcomes.' },
  { name: 'Katha', model: 'Community / alternative education', outcomes: 4, learning: 5, environment: 4, basis: 'StoryPedagogy and historical outcome reporting.' },
  { name: 'Lotus Petal Foundation', model: 'Integrated full-day school', outcomes: 4, learning: 3, environment: 4, basis: 'Impact, Stories of Change, school and wellness pages.' },
  { name: 'Kaliyuva Mane / Divya Deepa', model: 'Alternative residential education', outcomes: 4, learning: 5, environment: 5, basis: 'Alternative school model, annual report and residential continuity.' },
  { name: 'Bridges of Sports', model: 'Sports-led transformation', outcomes: 4, learning: 5, environment: 5, basis: 'Structured athlete pathway, reported medals and whole-athlete support.' },
  { name: 'Shishu Mandir', model: 'Long-term school and community support', outcomes: 4, learning: 4, environment: 5, basis: 'Higher-education support, school methodology and social welfare.' },
  { name: 'ABM Samaj', model: 'Tribal residential education', outcomes: 3, learning: 3, environment: 4, basis: 'Achievements and activities pages, including reported job destinations.' },
  { name: 'Nareshwadi / Giri Vanvasi', model: 'Tribal residential / experiential', outcomes: 2, learning: 4, environment: 5, basis: 'Farm, project and vocational learning inside a residential campus.' },
  { name: 'Akshar Foundation', model: 'Alternative / vocational education', outcomes: 2, learning: 5, environment: 4, basis: 'Schools, model and annual-report evidence.' },
  { name: 'Kalkeri Sangeet Vidyalaya', model: 'Arts-integrated residential education', outcomes: 4, learning: 5, environment: 5, basis: 'Annual Report 2020–21, Performing Arts and Student Welfare.' },
  { name: 'OM Foundation', model: 'School and transition support', outcomes: 4, learning: 3, environment: 3, basis: 'Programmes and Holistic Development pages.' },
];

export const DEFAULT_METRIC_SCORES: Record<MetricKey, MetricScore> = {
  child_progression: { rank: 3, reason: '' },
  learning_model: { rank: 3, reason: '' },
  development_ecosystem: { rank: 3, reason: '' },
};

export const EMPTY_METRIC_EVIDENCE: Record<MetricKey, MetricEvidence> = {
  child_progression: { text: '', links: [], ceiling_rank: 0, ceiling_reason: '' },
  learning_model: { text: '', links: [], ceiling_rank: 0, ceiling_reason: '' },
  development_ecosystem: { text: '', links: [], ceiling_rank: 0, ceiling_reason: '' },
};

function clampRank(value: unknown, fallback = 3) {
  const rank = Number(value);
  if (!Number.isFinite(rank)) return fallback;
  return Math.min(5, Math.max(1, Math.round(rank)));
}

function clampOptionalRank(value: unknown) {
  const rank = Number(value);
  if (!Number.isFinite(rank) || rank < 1) return 0;
  return Math.min(5, Math.max(1, Math.round(rank)));
}

export function normaliseMetricScores(value: unknown): Record<MetricKey, MetricScore> {
  const raw = value && typeof value === 'object' ? value as Record<string, any> : {};
  const next = structuredClone(DEFAULT_METRIC_SCORES);
  for (const metric of METRIC_DEFINITIONS) {
    const row = raw[metric.key] || {};
    next[metric.key] = {
      rank: clampRank(row.rank ?? row.score),
      reason: String(row.reason || ''),
    };
  }
  return next;
}

function validExternalUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

export function parseEvidenceLinks(value: string): EvidenceLink[] {
  return String(value || '')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      const split = line.split('|');
      const url = (split.length > 1 ? split.pop() : split[0])?.trim() || '';
      const label = split.join('|').trim();
      return { label: label || undefined, url };
    })
    .filter(link => validExternalUrl(link.url));
}

export function evidenceLinksToText(links?: EvidenceLink[]) {
  return (Array.isArray(links) ? links : [])
    .filter(link => link && validExternalUrl(String(link.url || '')))
    .map((link, index) => `${link.label || `Source ${index + 1}`} | ${link.url}`)
    .join('\n');
}

export function evidenceFacts(text?: string) {
  const raw = String(text || '').trim();
  if (!raw) return [];
  const lines = raw.split(/\r?\n/).map(line => line.replace(/^[-•\d.)\s]+/, '').trim()).filter(Boolean);
  if (lines.length > 1) return lines.slice(0, 8);
  return raw
    .split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
    .map(line => line.trim())
    .filter(Boolean)
    .slice(0, 8);
}

export function normaliseMetricEvidence(value: unknown): Record<MetricKey, MetricEvidence> {
  const raw = value && typeof value === 'object' ? value as Record<string, any> : {};
  const next = structuredClone(EMPTY_METRIC_EVIDENCE);
  for (const metric of METRIC_DEFINITIONS) {
    const row = raw[metric.key] || {};
    const links = Array.isArray(row.links)
      ? row.links.map((link: any, index: number) => {
          if (typeof link === 'string') return { label: `Source ${index + 1}`, url: link };
          return { label: String(link?.label || `Source ${index + 1}`), url: String(link?.url || '') };
        }).filter((link: EvidenceLink) => validExternalUrl(link.url))
      : [];
    next[metric.key] = {
      text: String(row.text || ''),
      links,
      ceiling_rank: clampOptionalRank(row.ceiling_rank ?? row.recommended_ceiling ?? row.max_rank),
      ceiling_reason: String(row.ceiling_reason || row.recommended_ceiling_reason || ''),
    };
  }
  return next;
}

function GuideTable({ metric }: { metric: typeof METRIC_DEFINITIONS[number] }) {
  return (
    <>
      <div className="metric-guide-where"><b>Where to check</b><p>{metric.guide.where}</p></div>
      <div className="metric-guide-table-wrap">
        <table className="metric-guide-table">
          <thead><tr><th>Score</th><th>What it looks like</th><th>Detailed example</th></tr></thead>
          <tbody>
            {metric.guide.rows.map(row => (
              <tr key={row.score}>
                <td><span className="metric-guide-score">{row.score}</span></td>
                <td>{row.meaning}</td>
                <td><strong>{row.exampleTitle}:</strong> {row.example}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="metric-guide-ceiling"><b>Ceiling rule</b><p>{metric.guide.ceiling}</p></div>
    </>
  );
}

export function MetricScoringCard({
  metricKey,
  score,
  evidence,
  disabled,
  onChange,
  onOpenReferences,
}: {
  metricKey: MetricKey;
  score: MetricScore;
  evidence?: MetricEvidence;
  disabled?: boolean;
  onChange: (next: MetricScore) => void;
  onOpenReferences: () => void;
}) {
  const metric = METRIC_DEFINITIONS.find(item => item.key === metricKey)!;
  const chars = score.reason.length;
  const complete = chars >= 100;
  const normalisedEvidence = useMemo(
    () => normaliseMetricEvidence({ [metricKey]: evidence })[metricKey],
    [evidence, metricKey],
  );
  const links = normalisedEvidence.links || [];
  const facts = useMemo(() => evidenceFacts(normalisedEvidence.text), [normalisedEvidence.text]);
  const hasEvidence = facts.length > 0 || links.length > 0 || Boolean(normalisedEvidence.ceiling_rank || normalisedEvidence.ceiling_reason);
  const ceilingRank = normalisedEvidence.ceiling_rank || 0;
  const exceedsCeiling = Boolean(ceilingRank && score.rank > ceilingRank);

  return (
    <article className={`metric-score-card metric-score-${score.rank} ${exceedsCeiling ? 'metric-score-over-ceiling' : ''}`}>
      <div className="metric-score-head">
        <div>
          <span className="metric-letter">{metric.letter}</span>
          <div>
            <h4>{metric.title}</h4>
            <p>{metric.question}</p>
          </div>
        </div>
        <button type="button" className="metric-reference-btn" onClick={onOpenReferences}>How to score this</button>
      </div>

      <details className={`metric-evidence-box metric-evidence-collapsible ${hasEvidence ? '' : 'metric-evidence-empty'}`}>
        <summary>
          <div>
            <span>Evidence pack</span>
            <small>{hasEvidence ? `${facts.length} fact${facts.length === 1 ? '' : 's'} · ${links.length} source${links.length === 1 ? '' : 's'}` : 'Not added yet'}</small>
          </div>
          <b>Open</b>
        </summary>
        <div className="metric-evidence-content">
          {facts.length > 0 ? (
            <ol className="metric-evidence-facts">
              {facts.map((fact, index) => <li key={`${fact}-${index}`}>{fact}</li>)}
            </ol>
          ) : <p className="metric-evidence-placeholder">No factual evidence package has been added from the gear panel yet.</p>}

          {links.length > 0 && (
            <div className="metric-evidence-links">
              {links.map((link, index) => (
                <a key={`${link.url}-${index}`} href={link.url} target="_blank" rel="noreferrer">
                  {link.label || `Source ${index + 1}`} ↗
                </a>
              ))}
            </div>
          )}

          {(normalisedEvidence.ceiling_rank || normalisedEvidence.ceiling_reason) && (
            <div className="metric-ceiling-note">
              <div><span>Recommended ceiling</span>{normalisedEvidence.ceiling_rank ? <b>Maximum {normalisedEvidence.ceiling_rank}</b> : null}</div>
              {normalisedEvidence.ceiling_reason && <p>{normalisedEvidence.ceiling_reason}</p>}
            </div>
          )}
        </div>
      </details>

      <div className="metric-rank-panel metric-choice-panel">
        <div className="metric-rank-value"><strong>{score.rank}</strong><span>{METRIC_SCALE[score.rank]}</span></div>
        <div className="metric-score-choices" role="radiogroup" aria-label={`${metric.title} rank`}>
          {Object.entries(METRIC_SCALE).map(([rank, label]) => {
            const value = Number(rank);
            const active = value === score.rank;
            return (
              <button
                key={rank}
                type="button"
                className={active ? 'active' : ''}
                role="radio"
                aria-checked={active}
                disabled={disabled}
                onClick={() => onChange({ ...score, rank: value })}
              >
                <b>{rank}</b>
                <span>{label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {exceedsCeiling && (
        <div className="metric-ceiling-block">
          This score is above the evidence pack’s recommended ceiling of {ceilingRank}. Keep the metric at or below the ceiling. Use the single exception override below the three metrics when the overall judgement needs to differ.
        </div>
      )}

      <label className="metric-reason-field">
        <span>Why this score? <b>Required</b></span>
        <textarea
          value={score.reason}
          disabled={disabled}
          minLength={100}
          onChange={event => onChange({ ...score, reason: event.target.value })}
          placeholder={`Explain why ${metric.title.toLowerCase()} is ${score.rank}. Use the evidence pack, the recommended ceiling and the comparison with the score above or below.`}
        />
        <small className={complete ? 'complete' : ''}>{chars}/100 characters minimum{complete ? ' · complete' : ''}</small>
      </label>
    </article>
  );
}

export function MetricReferenceModal({
  activeMetric,
  documentUrl,
  onClose,
}: {
  activeMetric: MetricKey;
  documentUrl?: string;
  onClose: () => void;
}) {
  const metric = METRIC_DEFINITIONS.find(item => item.key === activeMetric)!;
  const safeDocumentUrl = documentUrl && validExternalUrl(documentUrl) ? documentUrl : '';
  return (
    <div className="metric-reference-scrim" onClick={onClose}>
      <section className="metric-reference-modal metric-rules-modal" onClick={event => event.stopPropagation()}>
        <button className="modal-x" type="button" onClick={onClose}>×</button>
        <span className="workstream-kicker">How to score this</span>
        <h2>{metric.title}</h2>
        <p className="metric-reference-question">{metric.question}</p>
        <GuideTable metric={metric} />
        <div className="metric-reference-actions">
          <a className="ghost-btn metric-reference-document" href="/references/ngo-scoring-reference.html" target="_blank" rel="noreferrer">Open complete NGO reference ↗</a>
          {safeDocumentUrl && <a className="primary-red metric-reference-document" href={safeDocumentUrl} target="_blank" rel="noreferrer">Open admin reference document ↗</a>}
        </div>
      </section>
    </div>
  );
}

export function MetricReferenceLibraryModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="metric-reference-scrim" onClick={onClose}>
      <section className="metric-reference-modal metric-library-modal" onClick={event => event.stopPropagation()}>
        <button className="modal-x" type="button" onClick={onClose}>×</button>
        <span className="workstream-kicker">Reference library</span>
        <h2>How the benchmark NGOs are ranked</h2>
        <p className="metric-reference-question">Screening calibration based on the public evidence reviewed so far. Use the detailed score tables below to understand why each number means what it does.</p>

        <div className="metric-benchmark-table-wrap">
          <table className="metric-benchmark-table metric-library-table">
            <thead><tr><th>NGO</th><th>Model</th><th>Alumni</th><th>Learning</th><th>Environment</th><th>Evidence basis</th></tr></thead>
            <tbody>
              {NGO_REFERENCE_ROWS.map(row => (
                <tr key={row.name}>
                  <td><strong>{row.name}</strong></td><td>{row.model}</td>
                  <td><span className="metric-mini-score">{row.outcomes}</span></td>
                  <td><span className="metric-mini-score">{row.learning}</span></td>
                  <td><span className="metric-mini-score">{row.environment}</span></td>
                  <td>{row.basis}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="metric-library-sections">
          {METRIC_DEFINITIONS.map(metric => (
            <section className="metric-library-section" key={metric.key}>
              <div className="metric-library-section-head"><span className="metric-letter">{metric.letter}</span><div><h3>{metric.title}</h3><p>{metric.question}</p></div></div>
              <GuideTable metric={metric} />
            </section>
          ))}
        </div>
        <div className="metric-reference-actions"><a className="primary-red metric-reference-document" href="/references/ngo-scoring-reference.html" target="_blank" rel="noreferrer">Open as full-page reference ↗</a></div>
      </section>
    </div>
  );
}
