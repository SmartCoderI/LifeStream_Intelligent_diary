import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { loadConfig, saveApiKey } from "../../lib/api";

/** On the deployed dashboard the API needs a key. Shows the pages straight away and swaps in a
 *  key form only if the server says the key is missing or wrong. */
export default function KeyGate({ children }: { children: React.ReactNode }) {
  const [needed, setNeeded] = useState(false);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const keyWorks = async () => (await fetch("/api/days")).status !== 401;

  useEffect(() => {
    loadConfig().then(async (config) => {
      if (config.auth && !(await keyWorks())) setNeeded(true);
    });
  }, []);

  if (!needed) return <>{children}</>;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    saveApiKey(key.trim());
    if (await keyWorks()) {
      window.location.reload();  // reload so every page fetches again with the key
    } else {
      setError("That key didn't work.");
    }
  };

  return (
    <form className="card key-form" onSubmit={submit}>
      <h2><KeyRound size={20} /> API key</h2>
      <p className="muted">This dashboard needs its API key. It's saved in this browser.</p>
      <label className="field">
        Key
        <input type="password" value={key} autoFocus onChange={(e) => setKey(e.target.value)} />
      </label>
      {error && <div className="error-text" role="alert">{error}</div>}
      <button className="btn btn-primary" disabled={!key.trim()}>Continue</button>
    </form>
  );
}
