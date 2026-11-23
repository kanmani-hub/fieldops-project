import api from "./api";

export interface Dispatcher {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  name: string;
  role: string;
  tenant_id: string;
  organization_name: string | null;
  is_active: boolean;
  is_on_duty: boolean;
  phone_number: string | null;
  last_login: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * Get dispatchers belonging to the current active tenant.
 *
 * Backend is responsible for tenant isolation.
 * The JWT token is automatically attached by api.ts.
 */
export const getDispatchers = async (): Promise<Dispatcher[]> => {
  const response = await api.get<Dispatcher[]>("/dispatchers");
  return response.data;
};

/**
 * Request body for provisioning a technician.
 *
 * Tenant/organization and role are NOT sent from the frontend.
 * Backend should derive them from the authenticated dispatcher.
 */
export interface ProvisionTechnicianRequest {
  first_name: string;
  last_name: string;
  email: string;
  password: string;
}

/**
 * Provision a new technician account.
 */
export const provisionTechnician = async (
  data: ProvisionTechnicianRequest
) => {
  const response = await api.post(
    "/dispatchers/provision-technician",
    data
  );

  return response.data;
};

export interface JobStatusUpdatePayload {
  event: string;
  tenant_id: string;
  job_id: string;
  status: string;
  old_status?: string;
  timestamp?: string;
  correlation_id?: string;
  event_id?: string;
}

export interface DispatchUpdatePayload {
  event: string;
  tenant_id: string;
  job_id: string;
  assignment_id: string;
  technician_id?: string;
  technician_name?: string;
  previous_technician_id?: string;
  dispatch_action?: string;
  status?: string;
  timestamp?: string;
  correlation_id?: string;
  event_id?: string;
}

export interface SLAAlertPayload {
  event: string;
  tenant_id: string;
  job_id: string;
  severity: "AT_RISK" | "BREACHED" | "WARNING" | "CRITICAL" | "INFO" | "RESOLVED" | string;
  sla_type?: string;
  time_remaining_seconds?: number;
  target_time?: string;
  deadline?: string;
  customer_tier?: string;
  timestamp?: string;
  correlation_id?: string;
  event_id?: string;
}

export interface NotificationUpdatePayload {
  event: string;
  tenant_id: string;
  notification_id: string;
  status: "QUEUED" | "SENT" | "FAILED" | "RETRIED" | "DELIVERED" | "PENDING" | "READ" | string;
  channel?: string;
  recipient_id?: string;
  recipient_masked?: string;
  attempt?: number;
  job_id?: string;
  error_reason?: string;
  timestamp?: string;
  correlation_id?: string;
  event_id?: string;
}



/**
 * Helper to subscribe to real-time dispatch updates on a Socket.IO connection.
 */
export const subscribeDispatchUpdates = (
  socket: any,
  callback: (event: DispatchUpdatePayload) => void
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: DispatchUpdatePayload) => {
    callback(payload);
  };
  socket.on("dispatch.updated", handler);
  socket.on("job_assigned", handler);
  return () => {
    socket.off("dispatch.updated", handler);
    socket.off("job_assigned", handler);
  };
};

/**
 * Helper to subscribe to real-time job status updates on a Socket.IO connection.
 */
export const subscribeJobStatusUpdates = (
  socket: any,
  callback: (event: JobStatusUpdatePayload) => void
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: JobStatusUpdatePayload) => {
    callback(payload);
  };
  socket.on("job.status.updated", handler);
  return () => {
    socket.off("job.status.updated", handler);
  };
};

/**
 * Helper to subscribe to real-time SLA alerts on a Socket.IO connection.
 */
export const subscribeSLAAlerts = (
  socket: any,
  callback: (event: SLAAlertPayload) => void
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: SLAAlertPayload) => {
    callback(payload);
  };
  socket.on("sla.alert", handler);
  socket.on("sla_breached", handler);
  socket.on("sla_breach_warning", handler);
  return () => {
    socket.off("sla.alert", handler);
    socket.off("sla_breached", handler);
    socket.off("sla_breach_warning", handler);
  };
};

/**
 * Helper to subscribe to real-time notification lifecycle updates on a Socket.IO connection.
 */
export const subscribeNotificationUpdates = (
  socket: any,
  callback: (event: NotificationUpdatePayload) => void
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: NotificationUpdatePayload) => {
    callback(payload);
  };
  socket.on("notification.updated", handler);
  return () => {
    socket.off("notification.updated", handler);
  };
};

const dispatcherService = {
  getDispatchers,
  provisionTechnician,
  subscribeDispatchUpdates,
  subscribeJobStatusUpdates,
  subscribeSLAAlerts,
  subscribeNotificationUpdates,
};




export default dispatcherService;

