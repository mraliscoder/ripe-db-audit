export class AccessRequired extends Error {}
export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (response.status === 401) throw new AccessRequired("Sign in to continue.");
  if (!response.ok) {
    let message = `Request failed (${response.status}). Please try again.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string" && !/[А-Яа-я]/.test(body.detail))
        message = body.detail;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
export const terminal = new Set([
  "complete",
  "partial",
  "failed",
  "interrupted",
]);
export const date = (value?: string | number | null) =>
  value
    ? new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }).format(new Date(typeof value === "number" ? value * 1000 : value)) +
      " UTC"
    : "—";
export const englishError = (message?: string | null) =>
  message && !/[А-Яа-я]/.test(message)
    ? message
    : "The analysis could not be completed. Please try again.";
export const stageNames: Record<string, string> = {
  queued: "Waiting to start",
  resolve: "Resolving LIR",
  relations: "Discovering related resources",
  registrations: "Loading registrations",
  organisations: "Loading organisations",
  routing: "Checking BGP announcements",
  asn: "Checking ASN activity",
  analysis: "Analysing registrations",
  complete: "Analysis complete",
  failed: "Analysis failed",
};
