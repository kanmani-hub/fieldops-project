
import { useEffect, useMemo, useState } from "react";

export const ACCEPTANCE_WINDOW_SECONDS = 5 * 60;

export interface AcceptanceCountdownJob {
  id?: number | string;
  status?: string | null;

  // Timestamp when the technician's offer was created.
  assigned_at?: string | null;

  // Preferred: exact expiration timestamp from backend.
  acceptance_expires_at?: string | null;
}

export const isAwaitingAcceptance = (
  job: AcceptanceCountdownJob | null | undefined,
): boolean => {
  const status = String(job?.status ?? "")
    .trim()
    .toUpperCase()
    .replace(/[-\s]+/g, "_");

  return [
    "ASSIGNED",
    "PENDING",
    "AWAITING_ACCEPTANCE",
  ].includes(status);
};

export const getAcceptanceDeadline = (
  job: AcceptanceCountdownJob | null | undefined,
): number | null => {
  if (!job || !isAwaitingAcceptance(job)) {
    return null;
  }

  // Always prefer the backend-calculated deadline.
  if (job.acceptance_expires_at) {
    const deadline = new Date(
      job.acceptance_expires_at,
    ).getTime();

    if (Number.isFinite(deadline)) {
      return deadline;
    }
  }

  // Fallback when backend does not provide expiration.
  if (!job.assigned_at) {
    return null;
  }

  const assignedAt = new Date(job.assigned_at).getTime();

  if (!Number.isFinite(assignedAt)) {
    return null;
  }

  return assignedAt + ACCEPTANCE_WINDOW_SECONDS * 1000;
};

export const getRemainingAcceptanceSeconds = (
  job: AcceptanceCountdownJob | null | undefined,
  now = Date.now(),
): number | null => {
  const deadline = getAcceptanceDeadline(job);

  if (deadline === null) {
    return null;
  }

  return Math.max(
    0,
    Math.ceil((deadline - now) / 1000),
  );
};

export const formatAcceptanceCountdown = (
  seconds: number,
): string => {
  const safeSeconds = Math.max(0, seconds);

  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds % 60;

  return `${String(minutes).padStart(2, "0")}:${String(
    remainingSeconds,
  ).padStart(2, "0")}`;
};

export const useAcceptanceCountdowns = (
  jobs: readonly AcceptanceCountdownJob[],
): Record<string, number | null> => {
  const timerSignature = useMemo(() => {
    return jobs
      .filter(isAwaitingAcceptance)
      .map((job) => {
        if (job.id === undefined || job.id === null) {
          return null;
        }

        const deadline = getAcceptanceDeadline(job);

        return deadline === null
          ? null
          : `${job.id}:${deadline}`;
      })
      .filter((value): value is string => value !== null)
      .sort()
      .join("|");
  }, [jobs]);

  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    setNow(Date.now());

    if (!timerSignature) {
      return;
    }

    const interval = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);

    return () => {
      window.clearInterval(interval);
    };
  }, [timerSignature]);

  return useMemo(() => {
    const countdowns: Record<string, number | null> = {};

    for (const job of jobs) {
      if (job.id === undefined || job.id === null) {
        continue;
      }

      countdowns[String(job.id)] =
        getRemainingAcceptanceSeconds(job, now);
    }

    return countdowns;
  }, [jobs, now]);
};
