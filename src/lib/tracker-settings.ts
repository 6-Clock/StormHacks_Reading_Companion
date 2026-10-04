"use client";

import { useEffect, useRef, useState } from "react";
import { getTrackerSettings, setBlinkOnly, type TrackerSettings } from "@/lib/api";

export type TrackerSettingsControl = ReturnType<typeof useTrackerSettings>;

export function useTrackerSettings() {
  const [data, setData] = useState<TrackerSettings | null>(null);
  const [connected, setConnected] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const writing = useRef(false);
  const generation = useRef(0);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      const version = generation.current;
      try {
        const next = await getTrackerSettings(controller.signal);
        if (!stopped && !writing.current && version === generation.current) {
          setData(next);
          setConnected(true);
        }
      } catch {
        if (!stopped && version === generation.current) setConnected(false);
      } finally {
        if (!stopped) timer = setTimeout(poll, 1_000);
      }
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, []);

  async function toggle(enabled: boolean) {
    if (writing.current) return;
    writing.current = true;
    generation.current += 1;
    setSaving(true);
    setError(null);
    try {
      const next = await setBlinkOnly(enabled);
      generation.current += 1;
      setData(next);
      setConnected(true);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Tracker setting could not be saved.");
    } finally {
      writing.current = false;
      setSaving(false);
    }
  }

  const applied = connected && !!data?.tracker_connected && data.applied_revision === data.revision;
  return { data, connected, saving, error, applied, toggle };
}
