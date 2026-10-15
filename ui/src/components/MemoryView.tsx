import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { MemoryItem, StrategyStat } from "../types";

interface Props {
  dimensions: string[];
  /** Changes when a run finishes, so the view refreshes with what was just learned. */
  refreshKey: string;
}

/** What AutoCode has learned: lessons in memory and how each strategy scores per dimension. */
export function MemoryView({ dimensions, refreshKey }: Props) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [count, setCount] = useState(0);
  const [backend, setBackend] = useState("");
  const [stats, setStats] = useState<StrategyStat[]>([]);
  const [strategies, setStrategies] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const load = useCallback(async (q: string) => {
    try {
      const [memory, learning] = await Promise.all([api.memory(q), api.learning()]);
      setItems(memory.items);
      setCount(memory.count);
      setBackend(memory.backend);
      setStats(learning.stats);
      setStrategies(learning.strategies);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load("");
  }, [load, refreshKey]);

  const search = (event: FormEvent) => {
    event.preventDefault();
    void load(query.trim());
  };

  const remove = async (id: number) => {
    await api.deleteMemory(id);
    void load(query.trim());
  };

  const clearAll = async () => {
    if (!window.confirm("Forget every lesson and strategy score? AutoCode will start learning from scratch.")) return;
    await api.clearMemory();
    void load("");
  };

  const arms = Object.keys(strategies);
  const byDimension = new Map(stats.map((row) => [`${row.dimension}/${row.arm}`, row]));

  return (
    <div className="memory-view">
      <section>
        <div className="memory-head">
          <h3>Lessons</h3>
          <span className="field-hint">
            {count} stored in {backend === "postgres" ? "Postgres (pgvector)" : "a local database"}
          </span>
          <button type="button" className="danger" onClick={() => void clearAll()} disabled={!count && !stats.length}>
            Forget everything
          </button>
        </div>
        <form className="memory-search" onSubmit={search}>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find lessons for a task, e.g. login API with JWT"
            aria-label="Search memory"
          />
          <button type="submit">Search</button>
        </form>
        {error && <div className="error-text">{error}</div>}
        {items.length === 0 ? (
          <div className="empty">
            Nothing learned yet. After each run, a critic writes down what each step could have done better, and later
            runs on similar tasks recall it.
          </div>
        ) : (
          <ul className="lessons">
            {items.map((item) => (
              <li key={item.id}>
                <div className="lesson-meta">
                  <span className="lesson-dim">{item.dimension}</span>
                  <span>{item.kind === "episode" ? "run outcome" : `${item.step} step`}</span>
                  {item.similarity > 0 && <span>match {Math.round(item.similarity * 100)}%</span>}
                  <button type="button" className="link" onClick={() => void remove(item.id)} aria-label="Forget this lesson">
                    Forget
                  </button>
                </div>
                <p>{item.content}</p>
                <p className="lesson-task">From: {item.requirement}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3>Strategy scores</h3>
        <p className="field-hint">
          For each dimension, AutoCode tries implementation strategies and keeps score using the reviewers' grades (Thompson
          sampling). Higher scoring strategies get chosen more often.
        </p>
        <table className="strategy-table">
          <thead>
            <tr>
              <th scope="col">Dimension</th>
              {arms.map((arm) => (
                <th scope="col" key={arm} title={strategies[arm]}>
                  {arm.replace("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dimensions.map((dimension) => {
              const rows = arms.map((arm) => byDimension.get(`${dimension}/${arm}`));
              const best = Math.max(...rows.map((row) => row?.expected ?? 0));
              return (
                <tr key={dimension}>
                  <th scope="row">{dimension}</th>
                  {rows.map((row, i) => (
                    <td key={arms[i]} className={row && row.expected === best && row.pulls ? "best" : ""}>
                      {row?.pulls ? `${Math.round((row.mean_reward ?? 0) * 100)} avg, ${row.pulls} runs` : "untried"}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
