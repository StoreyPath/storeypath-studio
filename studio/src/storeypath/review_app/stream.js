// A page's stream of a project's events (GET /api/projects/<code>/events, web/events.py):
// what changes, who is on which floor, and jobs' progress, as they happen. The browser
// opens it again by itself when it is lost (``reopened`` is then called: what changed
// meanwhile was not heard).

export class ProjectStream {
  /** On a project (``floor``: the floor the page shows); ``ask(path)`` is the page's
   * GET, for a job no word comes of. */
  constructor(code, floor, ask) {
    this.ask = ask;
    this.jobs = new Map(); // job id → its progress as last told
    this.waiting = new Map(); // job id → [resolve]
    this.broken = false;
    this.reopened = () => {};
    const q = floor ? `?floor=${encodeURIComponent(floor)}` : "";
    this.source = new EventSource(`/api/projects/${encodeURIComponent(code)}/events${q}`);
    this.source.addEventListener("open", () => {
      if (this.broken) this.reopened();
      this.broken = false;
    });
    this.source.addEventListener("error", () => { this.broken = true; });
    this.on("job", (j) => this.heardJob(j));
  }

  /** ``fn(data)`` for each event of ``kind`` (change, presence, job, deleted). */
  on(kind, fn) {
    this.source.addEventListener(kind, (e) => fn(JSON.parse(e.data)));
    return this;
  }

  close() {
    this.source.close();
    for (const waiting of this.waiting.values()) for (const resolve of waiting) resolve(null);
    this.waiting.clear();
  }

  heardJob(j) {
    const had = this.jobs.get(j.id);
    const job = { ...had, ...j, log: had ? had.log.slice(0, j.from).concat(j.log) : j.log };
    this.jobs.set(j.id, job);
    if (job.state === "done" || job.state === "failed") {
      for (const resolve of this.waiting.get(j.id) || []) resolve(job);
      this.waiting.delete(j.id);
    }
  }

  /** A job followed to its end, its progress said with ``say(lines)`` (its log so far):
   * as the stream tells it, else (no word of it for a while, or no stream) asked for.
   * Resolves with the job, done or failed. */
  async follow(job, say = () => {}) {
    say(job.log || []);
    let asked = Date.now();
    while (job.state === "waiting" || job.state === "running") {
      const heard = this.jobs.get(job.id);
      if (heard && (heard.state === "done" || heard.state === "failed")) return heard;
      const ended = new Promise((resolve) => this.waiting.set(job.id, [...(this.waiting.get(job.id) || []), resolve]));
      const got = await Promise.race([ended, new Promise((r) => setTimeout(r, 700))]);
      if (got) return got;
      const now = this.jobs.get(job.id);
      if (now) say(now.log);
      if (this.broken || this.source.readyState === EventSource.CLOSED || (!now && Date.now() - asked > 3000)) {
        job = await this.ask(`jobs/${job.id}`); // (as before there was a stream)
        say(job.log);
        asked = Date.now();
      }
    }
    return job;
  }
}
