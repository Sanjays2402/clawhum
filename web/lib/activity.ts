"use client";

// Shared keys for the activity feed's "last seen" cursor.
// The activity page writes here; the nav reads from it to compute
// the unread badge so both stay in sync without prop drilling.

export const ACTIVITY_LAST_SEEN_KEY = "clawhum.activity.lastSeen.v1";
export const ACTIVITY_LAST_SEEN_EVENT = "clawhum:activity:lastSeen";

export function getLastSeen(): number {
  if (typeof window === "undefined") return 0;
  try {
    return Number(window.localStorage.getItem(ACTIVITY_LAST_SEEN_KEY) || "0");
  } catch {
    return 0;
  }
}
