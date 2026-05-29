export default function FeedbackPage() {
  return (
    <div className="px-6 py-16 max-w-2xl mx-auto">
      <h1 className="text-3xl font-bold mb-4">Feedback</h1>
      <p className="text-[var(--muted)]">
        Thumbs on each match write to feedback.jsonl. Future builds will use this
        for triplet fine-tuning of the embedder.
      </p>
    </div>
  );
}
