
import {
  CheckCircle2,
  FileText,
  MapPin,
  Phone,
  UserRound,
  Wrench,
  X,
} from "lucide-react";

export interface TechnicianForceAssignedJob {
  id: number | string;
  assigned_by?: string | null;
  customer_name?: string | null;
  contact_number?: string | null;
  location?: string | null;
  site_address?: string | null;
  service_type?: string | null;
  required_skill?: string | null;
  issue_description?: string | null;
  preferred_service_date?: string | null;
  preferred_service_time?: string | null;
  scheduled_at?: string | null;
}

interface TechnicianForceAssignPopupProps {
  job: TechnicianForceAssignedJob;
  onClose: () => void;
}

const formatDate = (value?: string | null): string | null => {
  if (!value) return null;

  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);

  const date = match
    ? new Date(
        Number(match[1]),
        Number(match[2]) - 1,
        Number(match[3])
      )
    : new Date(value);

  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
};

const formatTime = (value?: string | null): string | null => {
  if (!value) return null;

  const match = value.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return value;

  const hours = Number(match[1]);
  const minutes = Number(match[2]);

  if (hours > 23 || minutes > 59) return value;

  const date = new Date();
  date.setHours(hours, minutes, 0, 0);

  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
};

const formatSchedule = (job: TechnicianForceAssignedJob): string => {
  const date = formatDate(job.preferred_service_date);
  const time = formatTime(job.preferred_service_time);

  if (date && time) return `${date} · ${time}`;
  if (date || time) return date || time || "Not specified";

  if (job.scheduled_at) {
    const scheduledDate = new Date(job.scheduled_at);

    if (!Number.isNaN(scheduledDate.getTime())) {
      return new Intl.DateTimeFormat(undefined, {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(scheduledDate);
    }

    return job.scheduled_at;
  }

  return "Not specified";
};

const styles = {
  overlay: {
    position: "fixed" as const,
    inset: 0,
    zIndex: 10001,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
    background: "rgba(15, 23, 42, 0.62)",
    backdropFilter: "blur(5px)",
  },
  card: {
    width: "min(100%, 620px)",
    maxHeight: "min(90vh, 780px)",
    overflowY: "auto" as const,
    background: "#FFFFFF",
    borderRadius: 22,
    padding: 24,
    boxShadow: "0 24px 70px rgba(15,23,42,.28)",
    boxSizing: "border-box" as const,
  },
  item: {
    display: "flex",
    alignItems: "flex-start",
    gap: 12,
    padding: 14,
    border: "1px solid #E8EEEB",
    borderRadius: 14,
    minWidth: 0,
  },
  label: {
    display: "block",
    color: "#64748B",
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: ".04em",
  },
  value: {
    display: "block",
    marginTop: 4,
    color: "#273449",
    fontSize: 14,
    lineHeight: 1.5,
    overflowWrap: "anywhere" as const,
  },
};

export default function TechnicianForceAssignPopup({
  job,
  onClose,
}: TechnicianForceAssignPopupProps) {
  const phoneHref = job.contact_number
    ? `tel:${job.contact_number.replace(/[^+\d]/g, "")}`
    : undefined;

  return (
    <div style={styles.overlay}>
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="technician-force-assigned-title"
        style={styles.card}
      >
        <header
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 14,
            marginBottom: 20,
          }}
        >
          <div>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                padding: "7px 11px",
                borderRadius: 999,
                background: "#D1FAE5",
                color: "#065F46",
                fontSize: 11,
                fontWeight: 800,
                letterSpacing: ".06em",
              }}
            >
              <CheckCircle2 size={14} />
              FORCE ASSIGNED
            </span>

            <h2
              id="technician-force-assigned-title"
              style={{
                margin: "13px 0 4px",
                color: "#172033",
                fontSize: 24,
                fontWeight: 800,
              }}
            >
              Job assigned to you
            </h2>

            <p style={{ margin: 0, color: "#64748B", fontSize: 13 }}>
              This job was force-assigned and accepted. You can start it now.
            </p>
          </div>

          <button
            type="button"
            aria-label="Close force-assigned job details"
            onClick={onClose}
            style={{
              width: 36,
              height: 36,
              flex: "0 0 auto",
              border: 0,
              borderRadius: "50%",
              background: "#F1F5F4",
              color: "#475569",
              cursor: "pointer",
              display: "grid",
              placeItems: "center",
            }}
          >
            <X size={19} />
          </button>
        </header>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 10,
            marginBottom: 12,
          }}
        >
          <div
            style={{
              padding: 15,
              borderRadius: 14,
              background: "#ECFDF5",
              border: "1px solid #A7F3D0",
            }}
          >
            <span style={styles.label}>JOB ID</span>
            <strong
              style={{
                display: "block",
                marginTop: 5,
                color: "#075C43",
                fontSize: 21,
              }}
            >
              #{job.id}
            </strong>
          </div>

          <div
            style={{
              padding: 15,
              borderRadius: 14,
              background: "#F8FAFC",
              border: "1px solid #E2E8F0",
            }}
          >
            <span style={styles.label}>SCHEDULED DATE &amp; TIME</span>
            <strong style={{ ...styles.value, marginTop: 7 }}>
              {formatSchedule(job)}
            </strong>
          </div>
        </div>

        <div style={{ display: "grid", gap: 10 }}>
          <div style={styles.item}>
            <UserRound size={19} color="#64748B" />
            <div style={{ minWidth: 0 }}>
              <span style={styles.label}>CUSTOMER</span>
              <strong style={styles.value}>
                {job.customer_name || "Customer details unavailable"}
              </strong>

              {phoneHref && (
                <a
                  href={phoneHref}
                  style={{
                    display: "inline-block",
                    marginTop: 5,
                    color: "#1769E8",
                    fontSize: 13,
                  }}
                >
                  <Phone
                    size={13}
                    style={{
                      verticalAlign: "-2px",
                      marginRight: 4,
                    }}
                  />
                  {job.contact_number} · Call customer
                </a>
              )}
            </div>
          </div>

          <div style={styles.item}>
            <MapPin size={19} color="#64748B" />
            <div style={{ minWidth: 0 }}>
              <span style={styles.label}>SERVICE LOCATION</span>
              <strong style={styles.value}>
                {job.site_address || job.location || "Address not provided"}
              </strong>
            </div>
          </div>

          <div style={styles.item}>
            <Wrench size={19} color="#64748B" />
            <div style={{ minWidth: 0 }}>
              <span style={styles.label}>SERVICE</span>
              <strong style={styles.value}>
                {job.service_type || "Service Request"}
              </strong>

              {job.required_skill && (
                <span
                  style={{
                    display: "block",
                    marginTop: 4,
                    color: "#64748B",
                    fontSize: 12,
                  }}
                >
                  Required skill: {job.required_skill}
                </span>
              )}
            </div>
          </div>

          <div style={{ ...styles.item, background: "#F8FAFC" }}>
            <FileText size={19} color="#64748B" />
            <div style={{ minWidth: 0 }}>
              <span style={styles.label}>JOB DESCRIPTION</span>
              <p
                style={{
                  margin: "5px 0 0",
                  color: "#475569",
                  fontSize: 13,
                  lineHeight: 1.6,
                  whiteSpace: "pre-wrap",
                }}
              >
                {job.issue_description ||
                  "No additional service details provided."}
              </p>
            </div>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 9,
            marginTop: 16,
            padding: "12px 14px",
            borderRadius: 12,
            background: "#F0FDF4",
            color: "#166534",
            fontSize: 12,
            lineHeight: 1.5,
          }}
        >
          <CheckCircle2 size={17} style={{ flex: "0 0 auto" }} />
          You are already assigned to this job. It is available in your assigned jobs list.
        </div>

        <button
          type="button"
          onClick={onClose}
          style={{
            width: "100%",
            marginTop: 18,
            minHeight: 48,
            border: 0,
            borderRadius: 12,
            background: "#078A65",
            color: "#FFFFFF",
            fontSize: 14,
            fontWeight: 800,
            cursor: "pointer",
          }}
        >
          Got it — View Assigned Job
        </button>
      </section>
    </div>
  );
}
