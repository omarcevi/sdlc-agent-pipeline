export function HowToRead() {
  return (
    <section className="space-y-2" aria-label="How to read this">
      <h2 className="text-sm font-semibold m-0">How to read this</h2>
      <ul className="m-0 space-y-1 text-sm">
        <li>
          Times are wall-clock times from the run's event log. A model call is shown when it started; the gap to its
          tool result covers both the model's reply and the tool run, which the log does not separate.
        </li>
        <li>Cost is what the budget plugin priced from the provider's token counts, recorded at each model call.</li>
        <li>
          "Resolved" is the benchmark's score: hidden tests, run after the run in fresh sandboxes. The agents never
          saw them.
        </li>
        <li>Each replay is one run. The manifest's note says how many runs these were chosen from.</li>
      </ul>
    </section>
  );
}
