import { CalendarRange, Film, Loader2, MessageSquareText, Play, Search, SendHorizonal, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AnswerResult, type SearchStatus } from "../lib/api";
import { clock, longDate } from "../lib/format";

const EXAMPLES = [
  "What did we discuss about the frontend last week?",
  "Who is responsible for the logo fix?",
  "Why is John Smith missing school?",
];

function rangeLabel(r: AnswerResult): string | null {
  if (!r.date_from) return null;
  return r.date_from === r.date_to ? longDate(r.date_from) : `${longDate(r.date_from)} – ${longDate(r.date_to!)}`;
}

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<SearchStatus | null>(null);

  useEffect(() => {
    api.searchStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  const ask = async (q: string = question) => {
    const text = q.trim();
    if (!text || loading) return;
    setQuestion(text);
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.ask(text));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const range = result && rangeLabel(result);
  return (
    <>
      <header className="page-header">
        <h1>Ask Your Videos</h1>
        <p>Search across your videos, summaries and daily journal in plain language. Time phrases like "last week" narrow the search.</p>
      </header>

      {status && !status.available && (
        <div className="card notice" role="alert">
          Search isn't available: {status.error}
        </div>
      )}
      {status?.available && status.videos === 0 && (
        <div className="card notice">No videos are indexed yet. Finished videos are added automatically.</div>
      )}

      <form className="ask-box card" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <MessageSquareText size={22} className="ask-icon" />
        <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label="Question"
               placeholder="e.g. What did we decide about the dashboard charts?" maxLength={1000} />
        <button className="btn btn-primary" type="submit" disabled={!question.trim() || loading} aria-label="Ask">
          {loading ? <Loader2 size={18} className="spin" /> : <SendHorizonal size={18} />}
        </button>
      </form>

      {!result && !loading && !error && (
        <div className="examples">
          <span className="muted">Try:</span>
          {EXAMPLES.map((q) => (
            <button key={q} className="chip" onClick={() => ask(q)}><Search size={13} /> {q}</button>
          ))}
        </div>
      )}

      {loading && <div className="card empty"><Loader2 size={18} className="spin" /> Searching your videos…</div>}
      {error && <div className="card empty error-text" role="alert">{error}</div>}

      {result && (
        <section className="card answer-card">
          <h2><Sparkles size={20} /> Answer</h2>
          {range && <div className="muted range"><CalendarRange size={14} /> Searched {range}</div>}
          <p className={`answer${result.found ? "" : " muted"}`}>{result.answer}</p>

          {result.citations.length > 0 && (
            <>
              <h3 className="sources-title">Sources ({result.citations.length})</h3>
              {result.citations.map((c, i) => (
                <Link key={i} className="source" to={`/videos/${c.video_id}?t=${Math.floor(c.start)}`}>
                  <span className="thumb source-thumb">
                    {c.thumbnail ? <img src={c.thumbnail} alt="" /> : <Film size={22} />}
                  </span>
                  <span className="source-body">
                    <span className="source-title">{c.title}</span>
                    <span className="muted">{longDate(c.date)} · {c.section}</span>
                    <span className="source-snippet">{c.snippet}</span>
                  </span>
                  <span className="source-time">{clock(c.start)} – {clock(c.end)} <Play size={15} fill="currentColor" /></span>
                </Link>
              ))}
            </>
          )}
        </section>
      )}
    </>
  );
}
