'use client';

// DFP 2.0 — "How it works" v90: THE TYPOGRAPHIC FILM + THE FILTER.
// Drop-in replacement for app/how-it-works/page.tsx.
// Companion CSS: story-v90.css (self-contained, .svt- namespace).
//
// Typography leads the film. Exactly THREE graphics exist:
//   1. THE FILTER — leads rain into a funnel; 46,000+ collapses to 497
//   2. THE 1–5 SCALE — the caret ticks across and locks on 5
//   3. RACHIT TYPING — the PM writes the reason, live, with a caret
// Plus one small status row in the new CONTACT act.
//
//   0:00  How do we find / the best NGOs / in a region?
//   0:04  You search the internet. · Or you ask people who know. (red)
//   0:08  DISCOVER — text columns (46,000+ names · 5,000+ searches ·
//         partners / internal employees / connects → ≈150 human leads)
//   0:19  THE FILTER — funnel graphic · 46,000+ → 497 usable leads
//   0:27  RATE — 1–5 locks on 5 · Rachit types the reason ·
//         AI checks the review — not the score
//   0:36  RANK — Highest Transformation Potential
//   0:40  CONTACT — get in touch with the shortlist · see if it's a fit
//   0:46  The question returns → Like this. → loop

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

const TOTAL_MS = 51000;

const PHASES = [
  { id: 'q', at: 0 },
  { id: 'ways', at: 4200 },
  { id: 'discover', at: 8000 },
  { id: 'funnel', at: 19000 },
  { id: 'rate', at: 27000 },
  { id: 'rank', at: 36000 },
  { id: 'contact', at: 40500 },
  { id: 'ending', at: 46000 }
] as const;

type PhaseId = (typeof PHASES)[number]['id'];

function phaseIndexAt(t: number): number {
  let idx = 0;
  for (let i = 0; i < PHASES.length; i++) if (t >= PHASES[i].at) idx = i;
  return idx;
}

function useFilmClock(paused: boolean, reduced: boolean) {
  const [t, setT] = useState(0);
  const [cycle, setCycle] = useState(0);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const tRef = useRef(0);

  useEffect(() => {
    if (reduced) { tRef.current = 37000; setT(37000); return; }
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      if (!pausedRef.current) {
        tRef.current += now - last;
        if (tRef.current > TOTAL_MS) { tRef.current = 0; setCycle((c) => c + 1); }
        setT(tRef.current);
      }
      last = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [reduced]);

  const scrub = (ms: number) => {
    tRef.current = ms;
    setT(ms);
    setCycle((c) => c + 1);
  };

  return { t, cycle, scrub };
}

export default function HowItWorksFilm({ onClose }: { onClose?: () => void }) {
  const [paused, setPaused] = useState(false);
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window !== 'undefined') {
      setReduced(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }
  }, []);

  const { t, cycle, scrub } = useFilmClock(paused, reduced);
  const phaseIdx = phaseIndexAt(t);
  const phase: PhaseId = PHASES[phaseIdx].id;
  const progress = Math.min(t / TOTAL_MS, 1);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'ArrowRight') scrub(PHASES[Math.min(phaseIdx + 1, PHASES.length - 1)].at + 1);
      if (event.key === 'ArrowLeft') scrub(PHASES[Math.max(phaseIdx - 1, 0)].at + 1);
      if (event.key === ' ') { event.preventDefault(); setPaused((c) => !c); }
      if (event.key === 'Escape' && onClose) onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [phaseIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  const RootTag: 'main' | 'div' = onClose ? 'div' : 'main';

  return (
    <RootTag
      role={onClose ? 'dialog' : undefined}
      aria-modal={onClose ? 'true' : undefined}
      aria-label={onClose ? 'How it works preview' : undefined}
      className="svt-film"
      data-phase={phase}
      data-paused={paused ? 'true' : 'false'}
      data-reduced={reduced ? 'true' : 'false'}
    >
      <div className="svt-progress"><i style={{ width: `${progress * 100}%` }} /></div>
      <header className="svt-controls">
        {onClose ? <button type="button" className="svt-ctl" onClick={onClose}>← Back</button> : <Link href="/" className="svt-ctl">← Back</Link>}
        <div>
          <button type="button" className="svt-ctl" onClick={() => setPaused((c) => !c)}>{paused ? 'Resume' : 'Pause'}</button>
          <button type="button" className="svt-ctl" onClick={() => scrub(0)}>Restart</button>
        </div>
      </header>

      <section className="svt-stage" key={cycle} data-phase={phase} aria-hidden="true">

        {/* ============ 0:00 — THE QUESTION ============ */}
        <div className="svt-act svt-q">
          <h1>
            <span className="svt-ph svt-ph1">How do we find</span>
            <span className="svt-ph svt-ph2">the best NGOs</span>
            <span className="svt-ph svt-ph3">in a region?</span>
          </h1>
        </div>

        {/* ============ 0:04 — THE TWO WAYS (red) ============ */}
        <div className="svt-act svt-ways">
          <p className="svt-way svt-way1">You search the internet.</p>
          <p className="svt-way svt-way2">Or you ask people who know.</p>
        </div>

        {/* ============ 0:08 — DISCOVER (text columns) ============ */}
        <div className="svt-act svt-discover">
          <div className="svt-col">
            <h2><em>01</em>Search the internet.</h2>
            <p className="svt-line svt-d1">
              Scan the entire NGO Darpan database —
              <b>46,000+</b><u>names</u>
            </p>
            <p className="svt-line svt-d2">
              Run general searches across every type of NGO —
              <b>5,000+</b><u>searches</u>
            </p>
            <p className="svt-sub svt-d3">
              Residential schools · children’s homes · tribal hostels · special schools · alternative education · sports and arts programmes
            </p>
          </div>
          <div className="svt-col">
            <h2><em>02</em>Ask people who know.</h2>
            <p className="svt-line svt-h1">Current NGO partners.</p>
            <p className="svt-line svt-h2">Internal employees.</p>
            <p className="svt-line svt-h3">NGOs we have a connect with.</p>
            <p className="svt-line svt-h4">
              Together —
              <b>≈150</b><u>human leads</u>
            </p>
          </div>
        </div>

        {/* ============ 0:19 — THE FILTER (graphic #1) ============ */}
        <div className="svt-act svt-funnel">
          <p className="svt-beat svt-fn1">Then you filter.</p>
          <div className="svt-fnrow">
            <div className="svt-fngraph">
              <div className="svt-fnrain">
                {Array.from({ length: 18 }, (_, i) => (
                  <i key={i} style={{ animationDelay: `${1.2 + (i % 9) * 0.19}s` }} />
                ))}
              </div>
            </div>
            <div className="svt-fnnums">
              <div className="svt-fnbig svt-fnbefore"><b>46,000+</b><u>names in</u></div>
              <div className="svt-fnbig svt-fnafter"><b>497</b><u>usable leads out</u></div>
            </div>
          </div>
          <p className="svt-sub svt-fn2">Duplicates, mismatches, and weak leads removed. Human leads get calls to fill in the gaps.</p>
          <p className="svt-beat svt-fn3">Only the best move forward.</p>
        </div>

        {/* ============ 0:27 — RATE (graphics #2 and #3) ============ */}
        <div className="svt-act svt-rate">
          <p className="svt-beat svt-r1">PMs rate every NGO.</p>
          <div className="svt-scale">
            <span>1</span><span>2</span><span>3</span><span>4</span><span className="svt-five">5</span>
            <i className="svt-caret" />
          </div>
          {/* Rachit types the reason — live */}
          <div className="svt-typing">
            <em>Rachit · Why this rating?</em>
            <div className="svt-typed">Strong child focus and a great long-term model.</div>
          </div>
          <p className="svt-beat svt-r3">AI checks the review — <span className="svt-red">not the score.</span></p>
          <p className="svt-ask">“What makes this transformative?”</p>
        </div>

        {/* ============ 0:36 — RANK ============ */}
        <div className="svt-act svt-rank">
          <p className="svt-beat svt-b1">And the strongest rise to</p>
          <h2 className="svt-tiername">Highest Transformation Potential</h2>
        </div>

        {/* ============ 0:40 — CONTACT ============ */}
        <div className="svt-act svt-contact">
          <p className="svt-beat svt-c1">Then you get in touch.</p>
          <p className="svt-beat svt-c2">Call the shortlist. Visit. See if it’s a real fit.</p>
          <div className="svt-status">
            <b>Shanti Bhavan Educational Trust</b>
            <i className="svt-st1">Contacted</i>
            <i className="svt-st2">Good fit</i>
          </div>
        </div>

        {/* ============ 0:46 — THE ANSWER ============ */}
        <div className="svt-act svt-endq">
          <h1>How do we find the best NGOs in a region?</h1>
          <p className="svt-likethis">Like this.</p>
        </div>
      </section>
    </RootTag>
  );
}
