// Poll only the redacted snapshot. The caller owns authentication and rendering.
export function createPanelPoll({read, apply, failed, available, epoch}) {
  let timer = null, controller = null, generation = 0, failures = 0;
  function reset() {
    generation++;
    clearTimeout(timer); timer = null;
    controller?.abort(); controller = null;
    failures = 0;
  }
  function schedule(delay = 5000) {
    clearTimeout(timer);
    timer = setTimeout(tick, delay);
  }
  async function tick() {
    timer = null;
    if (controller) return;
    if (!available()) { schedule(); return; }
    const current = generation, owner = epoch(), request = new AbortController();
    controller = request;
    try {
      const value = await read(request.signal);
      if (current !== generation || owner !== epoch() || !available()) return;
      apply(value); failures = 0;
    } catch (error) {
      if (current !== generation || owner !== epoch() || request.signal.aborted) return;
      failures = Math.min(failures + 1, 3); failed(error);
    } finally {
      if (current === generation) {
        controller = null;
        schedule(Math.min(30000, 5000 * 2 ** failures));
      }
    }
  }
  return {reset, start: () => { reset(); schedule(); }, resume: () => { reset(); void tick(); }};
}
