/** Format user-facing dates consistently as DD/MM/YYYY. */
export function formatDateDDMMYYYY(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") {
    const isoDate = value.trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (isoDate) return `${isoDate[3]}/${isoDate[2]}/${isoDate[1]}`;
  }
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleDateString("en-GB", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Format timestamps with a DD/MM/YYYY date and localized time. */
export function formatDateTimeDDMMYYYY(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("en-GB", { day: "2-digit", month: "2-digit", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true });
}
