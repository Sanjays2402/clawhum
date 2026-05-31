import type { Metadata } from "next";
import Link from "next/link";
import {
  Sparkle,
  Check,
  Lightning,
  Buildings,
  Headphones,
  ArrowRight,
} from "@phosphor-icons/react/dist/ssr";

export const metadata: Metadata = {
  title: "clawhum / pricing",
  description:
    "Pick a plan. Free for personal humming, paid tiers unlock higher monthly quotas, batch jobs, webhooks, and team workspaces.",
};

interface Tier {
  id: "free" | "studio" | "label";
  name: string;
  tagline: string;
  price: string;
  cadence: string;
  quota: string;
  features: string[];
  icon: any;
  accent?: boolean;
  ctaLabel: string;
  ctaEnv: string | null;
}

const TIERS: Tier[] = [
  {
    id: "free",
    name: "Free",
    tagline: "for personal humming",
    price: "$0",
    cadence: "forever",
    quota: "500 matches / month",
    features: [
      "In-browser recorder",
      "Top-k results with waveform",
      "Local query history",
      "Public share links",
      "CSV / JSON export",
    ],
    icon: Sparkle,
    ctaLabel: "current plan",
    ctaEnv: null,
  },
  {
    id: "studio",
    name: "Studio",
    tagline: "for songwriters and producers",
    price: "$12",
    cadence: "per month",
    quota: "10,000 matches / month",
    features: [
      "Everything in Free",
      "Batch zip / csv jobs",
      "Webhook delivery + retries",
      "Cloud-synced history across devices",
      "Priority embedding queue",
      "Email support",
    ],
    icon: Lightning,
    accent: true,
    ctaLabel: "upgrade to Studio",
    ctaEnv: "NEXT_PUBLIC_STRIPE_LINK_STUDIO",
  },
  {
    id: "label",
    name: "Label",
    tagline: "for catalogs and labels",
    price: "$99",
    cadence: "per month",
    quota: "200,000 matches / month",
    features: [
      "Everything in Studio",
      "Team workspace with roles",
      "Private fingerprint index",
      "Bring-your-own-storage (S3 / GCS)",
      "SSO + audit log",
      "SLA + Slack support",
    ],
    icon: Buildings,
    ctaLabel: "talk to sales",
    ctaEnv: "NEXT_PUBLIC_STRIPE_LINK_LABEL",
  },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: "What counts as one match?",
    a: "Each call to /match counts as one. Webhook deliveries, share-link views, and history reads are free.",
  },
  {
    q: "What happens when I hit my monthly quota?",
    a: "The /match endpoint returns 429 until the meter resets at the start of the next calendar month. Existing data stays intact and read-only endpoints keep working.",
  },
  {
    q: "Can I self-host clawhum?",
    a: "Yes. The entire repository is open source under the LICENSE shipped at the project root. The paid tiers cover the hosted service and managed fingerprint index.",
  },
  {
    q: "Do you train on my queries?",
    a: "No. Audio is processed in memory, embeddings are scoped to your tenant, and you can delete your history at any time from the settings page.",
  },
  {
    q: "How do I cancel?",
    a: "From settings. Your plan downgrades to Free at the end of the current billing period. No prorations, no questions asked.",
  },
];

function ctaHref(envName: string | null): string | null {
  if (!envName) return null;
  // These are public env vars, inlined at build time by Next.
  const map: Record<string, string | undefined> = {
    NEXT_PUBLIC_STRIPE_LINK_STUDIO: process.env.NEXT_PUBLIC_STRIPE_LINK_STUDIO,
    NEXT_PUBLIC_STRIPE_LINK_LABEL: process.env.NEXT_PUBLIC_STRIPE_LINK_LABEL,
  };
  const v = map[envName];
  return v && /^https?:\/\//.test(v) ? v : null;
}

export default function PricingPage() {
  return (
    <div className="px-4 py-6 max-w-6xl mx-auto space-y-8">
      <header className="space-y-2">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
          <Sparkle size={12} weight="duotone" />
          <span>pricing</span>
        </div>
        <h1 className="font-mono text-[14px] uppercase tracking-widest text-[var(--color-text)]">
          pay for what you hum
        </h1>
        <p className="font-mono text-[11px] text-[var(--color-muted)] max-w-2xl leading-relaxed">
          Start free. Move up when you start shipping. Every plan includes the
          same matching quality. Higher tiers raise quotas and unlock batch,
          webhooks, and team features.
        </p>
      </header>

      <section
        className="grid grid-cols-1 md:grid-cols-3 gap-3"
        aria-label="Pricing tiers"
      >
        {TIERS.map((t) => {
          const href = ctaHref(t.ctaEnv);
          const Icon = t.icon;
          return (
            <article
              key={t.id}
              className={`panel rounded-[2px] p-5 flex flex-col gap-4 ${
                t.accent
                  ? "border-[var(--color-phosphor)] shadow-[0_0_0_1px_var(--color-phosphor)_inset]"
                  : ""
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <Icon
                      size={14}
                      weight="duotone"
                      className={
                        t.accent
                          ? "text-[var(--color-phosphor)]"
                          : "text-[var(--color-muted)]"
                      }
                    />
                    <span className="font-mono text-[12px] uppercase tracking-widest text-[var(--color-text)]">
                      {t.name}
                    </span>
                  </div>
                  <div className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest mt-1">
                    {t.tagline}
                  </div>
                </div>
                {t.accent ? (
                  <span className="font-mono text-[9px] uppercase tracking-widest px-1.5 py-0.5 border border-[var(--color-phosphor)] text-[var(--color-phosphor)]">
                    popular
                  </span>
                ) : null}
              </div>

              <div className="flex items-baseline gap-1.5">
                <span
                  className={`font-mono text-2xl tabular-nums ${
                    t.accent
                      ? "text-[var(--color-phosphor)]"
                      : "text-[var(--color-text)]"
                  }`}
                >
                  {t.price}
                </span>
                <span className="font-mono text-[10px] text-[var(--color-dim)] uppercase tracking-widest">
                  {t.cadence}
                </span>
              </div>

              <div className="font-mono text-[11px] text-[var(--color-muted)] border-t border-[var(--color-line)] pt-3">
                <span className="text-[var(--color-text)] tabular-nums">
                  {t.quota}
                </span>
              </div>

              <ul className="space-y-1.5 flex-1">
                {t.features.map((f) => (
                  <li
                    key={f}
                    className="flex items-start gap-2 font-mono text-[11px] text-[var(--color-muted)] leading-snug"
                  >
                    <Check
                      size={12}
                      weight="duotone"
                      className={`mt-0.5 shrink-0 ${
                        t.accent
                          ? "text-[var(--color-phosphor)]"
                          : "text-[var(--color-muted)]"
                      }`}
                    />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              <PlanCta tier={t} href={href} />
            </article>
          );
        })}
      </section>

      <section
        className="panel rounded-[2px] p-5 space-y-3"
        aria-label="Frequently asked questions"
      >
        <div className="flex items-center gap-2">
          <Headphones
            size={12}
            weight="duotone"
            className="text-[var(--color-phosphor)]"
          />
          <span className="label-xs">faq</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4">
          {FAQ.map((f) => (
            <details
              key={f.q}
              className="group border-b border-[var(--color-line)] pb-3 last:border-0"
            >
              <summary className="cursor-pointer list-none font-mono text-[12px] text-[var(--color-text)] flex items-start justify-between gap-2">
                <span>{f.q}</span>
                <ArrowRight
                  size={12}
                  weight="duotone"
                  className="text-[var(--color-dim)] mt-1 transition-transform group-open:rotate-90"
                />
              </summary>
              <p className="mt-2 font-mono text-[11px] text-[var(--color-muted)] leading-relaxed">
                {f.a}
              </p>
            </details>
          ))}
        </div>
      </section>

      <footer className="flex items-center justify-between pt-3 border-t border-[var(--color-line)] font-mono text-[10px] uppercase tracking-widest text-[var(--color-dim)]">
        <span>self host? see the LICENSE at the repo root.</span>
        <Link
          href="/usage"
          className="hover:text-[var(--color-phosphor)] flex items-center gap-1"
        >
          see your usage
          <ArrowRight size={11} weight="duotone" />
        </Link>
      </footer>
    </div>
  );
}

function PlanCta({ tier, href }: { tier: Tier; href: string | null }) {
  if (tier.id === "free") {
    return (
      <Link
        href="/"
        className="btn-primary px-3 py-2 rounded-[2px] font-mono text-[11px] uppercase tracking-widest text-center"
      >
        start humming
      </Link>
    );
  }
  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={`px-3 py-2 rounded-[2px] font-mono text-[11px] uppercase tracking-widest text-center transition ${
          tier.accent
            ? "btn-primary"
            : "border border-[var(--color-line)] hover:border-[var(--color-phosphor)] hover:text-[var(--color-phosphor)]"
        }`}
      >
        {tier.ctaLabel}
      </a>
    );
  }
  // Fallback when no Stripe link is configured. Real mailto, no fake form.
  const subject = encodeURIComponent(`clawhum / ${tier.name} plan interest`);
  const body = encodeURIComponent(
    `Hi,\n\nI would like to know when the ${tier.name} plan is available.\n\nThanks.`,
  );
  return (
    <a
      href={`mailto:hello@clawhum.com?subject=${subject}&body=${body}`}
      className={`px-3 py-2 rounded-[2px] font-mono text-[11px] uppercase tracking-widest text-center transition ${
        tier.accent
          ? "btn-primary"
          : "border border-[var(--color-line)] hover:border-[var(--color-phosphor)] hover:text-[var(--color-phosphor)]"
      }`}
    >
      {tier.ctaLabel}
    </a>
  );
}
