"use client";

/** Settings (M09): the hybrid/local-only profile toggle, persisted
 * via PATCH /settings and reflected in /health. */

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Profile = "hybrid" | "local-only";

export default function SettingsPage() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [activeProfile, setActiveProfile] = useState<Profile | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    async function load() {
      const { data } = await api.GET("/api/v1/settings");
      if (data) {
        setProfile(data.profile);
        setActiveProfile(data.active_profile);
      }
    }
    void load();
  }, []);

  const save = useCallback(
    async (next: Profile) => {
      const previous = profile;
      // Optimistic: the radio must reflect the choice at click time, not
      // after the PATCH round-trip — revert if the save fails.
      setProfile(next);
      setSaved(false);
      const { data } = await api.PATCH("/api/v1/settings", { body: { profile: next } });
      if (data) {
        setProfile(data.profile);
        setActiveProfile(data.active_profile);
        setSaved(true);
      } else {
        setProfile(previous);
      }
    },
    [profile],
  );

  return (
    <section data-testid="settings-screen" className="max-w-lg">
      <h1 className="mb-4 text-xl font-semibold">Settings</h1>
      <fieldset className="mb-4 space-y-2">
        <legend className="mb-2 text-sm font-medium">Provider profile</legend>
        {(["hybrid", "local-only"] as const).map((option) => (
          <label key={option} className="flex items-start gap-2 text-sm">
            <input
              type="radio"
              name="profile"
              value={option}
              checked={profile === option}
              onChange={() => void save(option)}
              data-testid={`profile-${option}`}
            />
            <span>
              <span className="font-medium">{option}</span>
              <span className="block text-xs text-zinc-500 dark:text-zinc-400">
                {option === "hybrid"
                  ? "Local data and indexing; cloud models for reasoning."
                  : "No bytes leave this machine. Local models only."}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
      {saved && (
        <p data-testid="settings-saved" className="text-sm text-emerald-700 dark:text-emerald-300">
          Saved. Model routing applies the new profile on next restart
          {activeProfile !== null ? ` (this process is running "${activeProfile}").` : "."}
        </p>
      )}
    </section>
  );
}
