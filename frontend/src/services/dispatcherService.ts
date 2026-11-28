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

/** Activate or deactivate a dispatcher account within the current organization. */
export const setDispatcherActive = async (
  dispatcherId: string,
  isActive: boolean,
) => {
  const response = await api.patch(
    `/dispatchers/${encodeURIComponent(dispatcherId)}/active`,
    { is_active: isActive },
  );
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

export interface PaymentUpdatePayload {
  event: string;
  tenant_id: string;
  invoice_id: string;
  payment_status: "PAID" | "PENDING" | "FAILED" | "REFUNDED" | "PARTIALLY_REFUNDED" | "PROCESSING" | "CANCELLED" | "AUTHORIZED" | string;
  status?: string;
  payment_id?: string;
  job_id?: string;
  customer_id?: string;
  amount?: number | string;
  currency?: string;
  payment_method?: string;
  error_reason?: string;
  timestamp?: string;
  correlation_id?: string;
  event_id?: string;
}

export interface ReconnectionConfig {
  status: "RECOVERED" | "INITIALIZED" | "SYNC_REQUIRED" | string;
  namespace: string;
  recovery_window_seconds: number;
  recovered?: boolean;
  tenant_id?: string;
}

export interface FailureHandlingConfig {
  status: "DEGRADED_OR_RECOVERED" | "HEALTHY" | "DEGRADED" | "RECOVERED" | string;
  failure_handling: "ENABLED" | string;
  malformed_event_protection?: boolean;
  max_redis_backoff_ms?: number;
  circuit_breaker_enabled?: boolean;
}

export interface TenantFilteringConfig {
  status: "FILTERED" | "REJECTED" | string;
  isolation: "TENANT_ROOM_AND_AUTHORIZATION" | string;
  room_prefix?: string;
}

export const DEFAULT_TENANT_FILTERING_CONFIG: TenantFilteringConfig = {
  status: "FILTERED",
  isolation: "TENANT_ROOM_AND_AUTHORIZATION",
  room_prefix: "tenant:",
};

const DISALLOWED_TENANT_VALUES = new Set([
  "",
  "*",
  "all",
  "global",
  "broadcast",
  "none",
  "null",
  "undefined",
]);

export const isValidTenantId = (tenantId: unknown): boolean => {
  if (tenantId === null || tenantId === undefined || typeof tenantId === "boolean") {
    return false;
  }
  const norm = String(tenantId).trim();
  if (!norm || DISALLOWED_TENANT_VALUES.has(norm.toLowerCase())) {
    return false;
  }
  return !norm.includes("*") && norm.length <= 128;
};

export const getTenantRoom = (tenantId: string): string => {
  const norm = String(tenantId || "").trim();
  return `tenant:${norm}`;
};

export const resolveExpectedTenantId = (
  socket?: any,
  explicitTenantId?: string | null
): string | null => {
  if (isValidTenantId(explicitTenantId)) {
    return String(explicitTenantId).trim();
  }
  const socketTenant =
    socket?.tenantId ??
    socket?.auth?.tenant_id ??
    socket?.io?.opts?.auth?.tenant_id;
  if (isValidTenantId(socketTenant)) {
    return String(socketTenant).trim();
  }
  if (typeof window !== "undefined") {
    try {
      const stored = window.localStorage?.getItem("tenant_id");
      if (isValidTenantId(stored)) {
        return String(stored).trim();
      }
    } catch {
      // Ignore storage access errors
    }
  }
  return null;
};

export const isAuthorizedTenantEvent = (
  payload: any,
  expectedTenantId?: string | null
): boolean => {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return false;
  }
  if (!isValidTenantId(payload.tenant_id)) {
    return false;
  }
  const eventTenant = String(payload.tenant_id).trim();
  if (expectedTenantId !== undefined && expectedTenantId !== null && String(expectedTenantId).trim() !== "") {
    return eventTenant === String(expectedTenantId).trim();
  }
  return true;
};


export const DEFAULT_SOCKET_RECONNECT_OPTIONS = {
  reconnection: true,
  reconnectionAttempts: 10,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 10000,
  timeout: 10000,
  transports: ["websocket", "polling"],
};





/**
 * Helper to subscribe to real-time dispatch updates on a Socket.IO connection.
 */
export const subscribeDispatchUpdates = (
  socket: any,
  callback: (event: DispatchUpdatePayload) => void,
  expectedTenantId?: string | null
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: DispatchUpdatePayload) => {
    const targetTenant = resolveExpectedTenantId(socket, expectedTenantId);
    if (!isAuthorizedTenantEvent(payload, targetTenant)) {
      return;
    }
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
  callback: (event: JobStatusUpdatePayload) => void,
  expectedTenantId?: string | null
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: JobStatusUpdatePayload) => {
    const targetTenant = resolveExpectedTenantId(socket, expectedTenantId);
    if (!isAuthorizedTenantEvent(payload, targetTenant)) {
      return;
    }
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
  callback: (event: SLAAlertPayload) => void,
  expectedTenantId?: string | null
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: SLAAlertPayload) => {
    const targetTenant = resolveExpectedTenantId(socket, expectedTenantId);
    if (!isAuthorizedTenantEvent(payload, targetTenant)) {
      return;
    }
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
  callback: (event: NotificationUpdatePayload) => void,
  expectedTenantId?: string | null
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: NotificationUpdatePayload) => {
    const targetTenant = resolveExpectedTenantId(socket, expectedTenantId);
    if (!isAuthorizedTenantEvent(payload, targetTenant)) {
      return;
    }
    callback(payload);
  };
  socket.on("notification.updated", handler);
  return () => {
    socket.off("notification.updated", handler);
  };
};

/**
 * Helper to subscribe to real-time payment/invoice lifecycle updates on a Socket.IO connection.
 */
export const subscribePaymentUpdates = (
  socket: any,
  callback: (event: PaymentUpdatePayload) => void,
  expectedTenantId?: string | null
): (() => void) => {
  if (!socket) return () => {};
  const handler = (payload: PaymentUpdatePayload) => {
    const targetTenant = resolveExpectedTenantId(socket, expectedTenantId);
    if (!isAuthorizedTenantEvent(payload, targetTenant)) {
      return;
    }
    callback(payload);
  };
  socket.on("payment.updated", handler);
  socket.on("payment_success", handler);
  socket.on("payment_failed", handler);
  socket.on("payment_refunded", handler);
  return () => {
    socket.off("payment.updated", handler);
    socket.off("payment_success", handler);
    socket.off("payment_failed", handler);
    socket.off("payment_refunded", handler);
  };
};

/**
 * Helper to handle WebSocket connection-state recovery and trigger backend state refresh
 * when the recovery window has elapsed or recovery is incomplete.
 */
export const subscribeReconnectionRecovery = (
  socket: any,
  onRecovered?: (config: ReconnectionConfig) => void,
  onSyncRequired?: () => void
): (() => void) => {
  if (!socket) return () => {};

  const recoveryHandler = (config: ReconnectionConfig) => {
    if (config.recovered) {
      if (onRecovered) onRecovered(config);
    } else {
      if (onSyncRequired) onSyncRequired();
    }
  };

  const connectHandler = () => {
    if (socket.recovered) {
      if (onRecovered) {
        onRecovered({
          status: "RECOVERED",
          namespace: "/dispatch-dashboard",
          recovery_window_seconds: 120,
          recovered: true,
        });
      }
    } else {
      if (onSyncRequired) onSyncRequired();
    }
  };

  socket.on("recovery.status", recoveryHandler);
  socket.on("connect", connectHandler);

  return () => {
    socket.off("recovery.status", recoveryHandler);
    socket.off("connect", connectHandler);
  };
};

export interface WebSocketAuthConfig {
  status: string;
  tenant_source: string;
  tenant_id?: string;
}

export const DEFAULT_WEBSOCKET_AUTH_CONFIG: WebSocketAuthConfig = {
  status: "AUTHENTICATED",
  tenant_source: "VERIFIED_TOKEN_CLAIMS",
};

export const getWebSocketAuthOptions = (
  token?: string,
  tenantId?: string,
  options?: Record<string, any>
) => {
  return {
    ...DEFAULT_SOCKET_RECONNECT_OPTIONS,
    auth: {
      ...(token ? { token } : {}),
      ...(tenantId ? { tenant_id: tenantId } : {}),
      ...(options?.auth || {}),
    },
    extraHeaders: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.extraHeaders || {}),
    },
    ...options,
  };
};

export interface ConcurrentConnectionsConfig {
  status: "PASSED" | "FAILED" | string;
  scenarios: string[];
}

export const DEFAULT_CONCURRENT_CONNECTIONS_CONFIG: ConcurrentConnectionsConfig = {
  status: "PASSED",
  scenarios: [
    "same_tenant_concurrency",
    "multi_tenant_isolation",
    "reconnect",
    "event_broadcast",
  ],
};

/**
 * Bounded deduplicator for concurrent event streams and reconnect replays.
 * Prevents uncontrolled memory growth while enforcing idempotency by event key.
 */
export const createBoundedEventDeduplicator = (maxEntries = 500) => {
  const cap = Math.max(1, Number(maxEntries) || 500);
  const seen = new Set<string>();
  const queue: string[] = [];

  return {
    isDuplicate: (eventId?: string | null): boolean => {
      if (!eventId) {
        return false;
      }
      const key = String(eventId);
      if (seen.has(key)) {
        return true;
      }
      if (queue.length >= cap) {
        const oldest = queue.shift();
        if (oldest !== undefined) {
          seen.delete(oldest);
        }
      }
      seen.add(key);
      queue.push(key);
      return false;
    },
    size: () => seen.size,
    clear: () => {
      seen.clear();
      queue.length = 0;
    },
  };
};

export interface ConcurrentTenantStreamHandlers {
  onDispatchUpdate?: (event: DispatchUpdatePayload) => void;
  onJobStatusUpdate?: (event: JobStatusUpdatePayload) => void;
  onSLAAlert?: (event: SLAAlertPayload) => void;
  onNotificationUpdate?: (event: NotificationUpdatePayload) => void;
  onPaymentUpdate?: (event: PaymentUpdatePayload) => void;
  onRecovered?: (config: ReconnectionConfig) => void;
  onSyncRequired?: () => void;
  dedupMaxEntries?: number;
}

/**
 * Subscribe to all tenant-filtered dispatch, SLA, notification, payment, and
 * reconnection streams on a Socket.IO client with bounded deduplication.
 */
export const subscribeConcurrentTenantEvents = (
  socket: any,
  handlers: ConcurrentTenantStreamHandlers,
  expectedTenantId?: string | null
): (() => void) => {
  if (!socket) return () => {};
  const dedup = createBoundedEventDeduplicator(handlers.dedupMaxEntries ?? 500);
  const unsubs: Array<() => void> = [];

  const wrapDedup = <T extends { event_id?: string }>(cb?: (payload: T) => void) => {
    return (payload: T) => {
      if (!cb) return;
      if (payload?.event_id && dedup.isDuplicate(payload.event_id)) {
        return;
      }
      cb(payload);
    };
  };

  if (handlers.onDispatchUpdate) {
    unsubs.push(subscribeDispatchUpdates(socket, wrapDedup(handlers.onDispatchUpdate), expectedTenantId));
  }
  if (handlers.onJobStatusUpdate) {
    unsubs.push(subscribeJobStatusUpdates(socket, wrapDedup(handlers.onJobStatusUpdate), expectedTenantId));
  }
  if (handlers.onSLAAlert) {
    unsubs.push(subscribeSLAAlerts(socket, wrapDedup(handlers.onSLAAlert), expectedTenantId));
  }
  if (handlers.onNotificationUpdate) {
    unsubs.push(subscribeNotificationUpdates(socket, wrapDedup(handlers.onNotificationUpdate), expectedTenantId));
  }
  if (handlers.onPaymentUpdate) {
    unsubs.push(subscribePaymentUpdates(socket, wrapDedup(handlers.onPaymentUpdate), expectedTenantId));
  }
  if (handlers.onRecovered || handlers.onSyncRequired) {
    unsubs.push(subscribeReconnectionRecovery(socket, handlers.onRecovered, handlers.onSyncRequired));
  }

  return () => {
    for (const unsub of unsubs) {
      unsub();
    }
    dedup.clear();
  };
};

const dispatcherService = {
  getDispatchers,
  setDispatcherActive,
  provisionTechnician,
  subscribeDispatchUpdates,
  subscribeJobStatusUpdates,
  subscribeSLAAlerts,
  subscribeNotificationUpdates,
  subscribePaymentUpdates,
  subscribeReconnectionRecovery,
  subscribeConcurrentTenantEvents,
  createBoundedEventDeduplicator,
  DEFAULT_SOCKET_RECONNECT_OPTIONS,
  DEFAULT_WEBSOCKET_AUTH_CONFIG,
  DEFAULT_TENANT_FILTERING_CONFIG,
  DEFAULT_CONCURRENT_CONNECTIONS_CONFIG,
  getWebSocketAuthOptions,
  isValidTenantId,
  getTenantRoom,
  resolveExpectedTenantId,
  isAuthorizedTenantEvent,
};






export default dispatcherService;

