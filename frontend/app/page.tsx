import Link from "next/link";

export default function HomePage() {
  return (
    <div className="landing">
      <header className="landingHeader">
        <div className="landingBrand">
          <span className="landingMark" aria-hidden="true" />
          <span>Claim Desk</span>
        </div>
        <nav className="landingNav">
          <Link className="landingNavLink" href="/login">
            Sign in
          </Link>
          <Link className="landingNavCta" href="/login">
            Get started
          </Link>
        </nav>
      </header>

      <main className="landingMain">
        <section className="landingHero">
          <p className="landingEyebrow">Airline dispute resolution</p>
          <h1 className="landingTitle">
            Resolve claims with clarity, in Urdu or English.
          </h1>
          <p className="landingLead">
            A voice and text assistant for refunds, baggage rules, cancellations,
            and flight questions — grounded in airline policy where it matters.
          </p>
          <div className="landingActions">
            <Link className="btnPrimary landingPrimary" href="/login">
              Start a conversation
            </Link>
            <Link className="btnGhost landingSecondary" href="/chat">
              Open chat
            </Link>
          </div>
        </section>

        <section className="landingGrid">
          <article className="landingCard">
            <h2>Policy-grounded answers</h2>
            <p>
              Retrieves relevant airline and regulatory clauses, then explains them
              in plain language without exposing internal system details.
            </p>
          </article>
          <article className="landingCard">
            <h2>Bilingual by design</h2>
            <p>
              Speak or type in Urdu, English, or a mix. Responses follow your
              language while legal citations stay in original wording.
            </p>
          </article>
          <article className="landingCard">
            <h2>Voice when you need it</h2>
            <p>
              Hands-free mode for natural back-and-forth, with text input when
              you prefer a quieter workflow.
            </p>
          </article>
        </section>
      </main>

      <footer className="landingFooter">
        <p>Built for Pakistani airline passengers and dispute workflows.</p>
      </footer>
    </div>
  );
}
