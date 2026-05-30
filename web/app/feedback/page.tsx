export default function FeedbackPage() {
  return (
    <div className="px-4 py-4">
      <h1 className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] mb-3">
        feedback / triplet loss corpus
      </h1>
      <div className="panel rounded-[2px] p-6 space-y-3 max-w-2xl">
        <p className="font-mono text-[12px] text-[var(--color-text)]">
          confirm / reject votes on each candidate write to <span className="text-[var(--color-phosphor)]">feedback.jsonl</span>.
        </p>
        <p className="font-mono text-[11px] text-[var(--color-muted)]">
          schema / <code className="text-[var(--color-text)]">{`{ query_id, track_id, score, vote: -1 | 1 }`}</code>
        </p>
        <p className="font-mono text-[11px] text-[var(--color-muted)]">
          downstream / sampled as anchor+positive / anchor+negative pairs during triplet fine-tuning of the embedder.
        </p>
        <p className="font-mono text-[11px] text-[var(--color-dim)] pt-2 border-t border-[var(--color-line)]">
          submit votes from any match detail page.
        </p>
      </div>
    </div>
  );
}
