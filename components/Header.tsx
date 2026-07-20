import Link from 'next/link';

export default function Header({ active }: { active?: 'repository' | 'ranking' | 'tracker' }) {
  return (
    <header className="dfp-topbar">
      <div className="dfp-wrap dfp-topbar-inner no-brand">
        <nav className="dfp-nav" aria-label="Primary navigation">
          <Link className={active === undefined ? 'nav-pill active' : 'nav-pill'} href="/">Home</Link>
          <Link className={active === 'repository' ? 'nav-pill active' : 'nav-pill'} href="/ngo-discovery">NGO Discovery</Link>
          <Link className={active === 'ranking' ? 'nav-pill active' : 'nav-pill'} href="/progress">Rankings</Link>
          <Link className={active === 'tracker' ? 'nav-pill active' : 'nav-pill'} href="/contact-tracker">Tracker</Link>
        </nav>
      </div>
    </header>
  );
}
