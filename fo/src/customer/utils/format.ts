export function formatDate(value?: string | null): string {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "2-digit", year: "numeric" }).format(date);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "2-digit", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true }).format(date);
}

export function formatMoney(value?: number | string | null): string {
  const amount = Number(value ?? 0);
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(Number.isFinite(amount) ? amount : 0);
}

export function normalizedStatus(value?: string | null): string {
  return String(value ?? "UNKNOWN").trim().toUpperCase().replaceAll("-", "_").replaceAll(" ", "_");
}

/** Match the web customer tracker labels for service and linked job states. */
export function displayCustomerStatus(value?: string | null): string {
  switch (normalizedStatus(value)) {
    case "CREATED":
    case "PENDING":
    case "UNASSIGNED":
    case "QUEUED":
      return "UNASSIGNED";
    case "ASSIGNED":
    case "AWAITING_ACCEPTANCE":
    case "FORCE_ASSIGNED":
      return "AWAITING ACCEPTANCE";
    case "ACCEPTED":
      return "ASSIGNED";
    case "EN_ROUTE":
    case "ENROUTE":
      return "EN ROUTE";
    case "ON_SITE":
    case "ONSITE":
    case "IN_PROGRESS":
      return "IN PROGRESS";
    case "COMPLETED":
    case "CLOSED":
      return "COMPLETED";
    case "CANCELED":
    case "CANCELLED":
      return "CANCELLED";
    case "REJECTED":
    case "REJECTED_BY_TECHNICIAN":
      return "CANCELLED";
    default:
      return normalizedStatus(value).replaceAll("_", " ");
  }
}

export function canCancelRequest(requestStatus?: string | null, linkedJobStatus?: string | null, hasLinkedJob = false): boolean {
  if (hasLinkedJob) {
    // The backend only permits cancellation of a linked job while it is CREATED, ASSIGNED or ACCEPTED.
    return ["CREATED", "ASSIGNED", "ACCEPTED"].includes(normalizedStatus(linkedJobStatus));
  }
  return ["UNASSIGNED", "PENDING", "CREATED", "ASSIGNED"].includes(normalizedStatus(requestStatus));
}

export function isWithinCancellationWindow(createdAt?: string | null, now = Date.now()): boolean {
  if (!createdAt) return false;
  const createdTime = new Date(createdAt).getTime();
  const age = now - createdTime;
  return Number.isFinite(createdTime) && age >= 0 && age <= 3 * 60 * 60 * 1000;
}

export function canEditRequest(requestStatus?: string | null, linkedJobStatus?: string | null, hasLinkedJob = false): boolean {
  if (hasLinkedJob) {
    // The backend permits editing a linked request only while its job is still CREATED.
    return normalizedStatus(linkedJobStatus) === "CREATED";
  }
  return normalizedStatus(requestStatus) === "UNASSIGNED";
}

export function customerDisplayName(firstName?: string | null, fullName?: string | null): string {
  const name = fullName?.trim() || firstName?.trim() || "there";
  return name.split(/\s+/)[0] || "there";
}
