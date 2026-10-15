import { useEffect, useState, type Dispatch } from "react";
import { api, desktopBridge } from "./api";
import { EVENT_KINDS, type Action } from "./state/run";
import type { RunEvent } from "./types";


/** Follow a run's server-sent events; replays from the start, so reopening an old run rebuilds its view. */
export function useRunStream(runId: string | undefined, dispatch: Dispatch<Action>, onFinished: () => void): void {
  useEffect(() => {
    if (!runId) return;
    const source = api.events(runId);
    for (const kind of EVENT_KINDS) {
      source.addEventListener(kind, (message) => {
        const event = JSON.parse((message as MessageEvent).data) as RunEvent;
        dispatch({ type: "event", event });
        if (kind === "finished") {
          source.close();
          onFinished();
        }
      });
    }
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        dispatch({ type: "note", text: "Lost connection to the AutoCode backend. Restart the app and reopen the run.", tone: "bad" });
      }
    };
    return () => source.close();
  }, [runId, dispatch, onFinished]);
}

/** True once the pywebview bridge is injected (desktop app only). */
export function useDesktopBridge(): boolean {
  const [ready, setReady] = useState(() => desktopBridge() !== null);
  useEffect(() => {
    const onReady = () => setReady(desktopBridge() !== null);
    window.addEventListener("pywebviewready", onReady);
    return () => window.removeEventListener("pywebviewready", onReady);
  }, []);
  return ready;
}
