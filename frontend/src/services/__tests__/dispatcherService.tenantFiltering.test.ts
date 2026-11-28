import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_CONCURRENT_CONNECTIONS_CONFIG,
  DEFAULT_TENANT_FILTERING_CONFIG,
  createBoundedEventDeduplicator,
  getTenantRoom,
  isAuthorizedTenantEvent,
  isValidTenantId,
  subscribeConcurrentTenantEvents,
  subscribeDispatchUpdates,
  subscribeJobStatusUpdates,
  subscribeNotificationUpdates,
  subscribePaymentUpdates,
  subscribeSLAAlerts,
} from "../dispatcherService";

class MockSocket {
  auth: Record<string, any>;
  private listeners: Record<string, Array<(payload: any) => void>> = {};

  constructor(tenantId?: string) {
    this.auth = tenantId ? { tenant_id: tenantId } : {};
  }

  on(event: string, fn: (payload: any) => void) {
    if (!this.listeners[event]) {
      this.listeners[event] = [];
    }
    this.listeners[event].push(fn);
  }

  off(event: string, fn: (payload: any) => void) {
    this.listeners[event] = (this.listeners[event] || []).filter((h) => h !== fn);
  }

  emitLocal(event: string, payload: any) {
    for (const fn of this.listeners[event] || []) {
      fn(payload);
    }
  }
}

describe("dispatcherService tenant-based event filtering", () => {
  it("exposes canonical tenant filtering contract and room formatting", () => {
    expect(DEFAULT_TENANT_FILTERING_CONFIG.status).toBe("FILTERED");
    expect(DEFAULT_TENANT_FILTERING_CONFIG.isolation).toBe("TENANT_ROOM_AND_AUTHORIZATION");
    expect(getTenantRoom("tenant-1")).toBe("tenant:tenant-1");
  });

  it("rejects missing, wildcard, and cross-tenant event payloads", () => {
    expect(isValidTenantId("tenant-alpha")).toBe(true);
    expect(isValidTenantId("*")).toBe(false);
    expect(isValidTenantId("all")).toBe(false);
    expect(isValidTenantId("")).toBe(false);

    expect(isAuthorizedTenantEvent({ tenant_id: "tenant-a" }, "tenant-a")).toBe(true);
    expect(isAuthorizedTenantEvent({ tenant_id: "tenant-b" }, "tenant-a")).toBe(false);
    expect(isAuthorizedTenantEvent({ tenant_id: "*" }, "tenant-a")).toBe(false);
    expect(isAuthorizedTenantEvent({ job_id: "123" }, "tenant-a")).toBe(false);
  });

  it("delivers only authorized same-tenant events across all dispatch/tracking subscriptions", () => {
    const socket = new MockSocket("tenant-alpha");

    const dispatchCb = vi.fn();
    const statusCb = vi.fn();
    const slaCb = vi.fn();
    const notifCb = vi.fn();
    const payCb = vi.fn();

    const unsub1 = subscribeDispatchUpdates(socket, dispatchCb);
    const unsub2 = subscribeJobStatusUpdates(socket, statusCb);
    const unsub3 = subscribeSLAAlerts(socket, slaCb);
    const unsub4 = subscribeNotificationUpdates(socket, notifCb);
    const unsub5 = subscribePaymentUpdates(socket, payCb);

    // Cross-tenant payloads must be dropped
    socket.emitLocal("dispatch.updated", { event: "dispatch.updated", tenant_id: "tenant-beta", job_id: "1", assignment_id: "2" });
    socket.emitLocal("job.status.updated", { event: "job.status.updated", tenant_id: "tenant-beta", job_id: "1", status: "ASSIGNED" });
    socket.emitLocal("sla.alert", { event: "sla.alert", tenant_id: "*", job_id: "1", severity: "BREACHED" });
    socket.emitLocal("notification.updated", { event: "notification.updated", notification_id: "n1", status: "SENT" });
    socket.emitLocal("payment.updated", { event: "payment.updated", tenant_id: "tenant-beta", invoice_id: "100", payment_status: "PAID" });

    expect(dispatchCb).not.toHaveBeenCalled();
    expect(statusCb).not.toHaveBeenCalled();
    expect(slaCb).not.toHaveBeenCalled();
    expect(notifCb).not.toHaveBeenCalled();
    expect(payCb).not.toHaveBeenCalled();

    // Same-tenant payloads must be delivered
    socket.emitLocal("dispatch.updated", { event: "dispatch.updated", tenant_id: "tenant-alpha", job_id: "1", assignment_id: "2" });
    socket.emitLocal("job.status.updated", { event: "job.status.updated", tenant_id: "tenant-alpha", job_id: "1", status: "ASSIGNED" });
    socket.emitLocal("sla.alert", { event: "sla.alert", tenant_id: "tenant-alpha", job_id: "1", severity: "AT_RISK" });
    socket.emitLocal("notification.updated", { event: "notification.updated", tenant_id: "tenant-alpha", notification_id: "n1", status: "SENT" });
    socket.emitLocal("payment.updated", { event: "payment.updated", tenant_id: "tenant-alpha", invoice_id: "100", payment_status: "PAID" });

    expect(dispatchCb).toHaveBeenCalledTimes(1);
    expect(statusCb).toHaveBeenCalledTimes(1);
    expect(slaCb).toHaveBeenCalledTimes(1);
    expect(notifCb).toHaveBeenCalledTimes(1);
    expect(payCb).toHaveBeenCalledTimes(1);

    unsub1();
    unsub2();
    unsub3();
    unsub4();
    unsub5();
  });

  it("supports concurrent multi-client streams, bounded deduplication, and reconnects without cross-tenant leaks", () => {
    expect(DEFAULT_CONCURRENT_CONNECTIONS_CONFIG.status).toBe("PASSED");
    expect(DEFAULT_CONCURRENT_CONNECTIONS_CONFIG.scenarios).toEqual([
      "same_tenant_concurrency",
      "multi_tenant_isolation",
      "reconnect",
      "event_broadcast",
    ]);

    // Bounded deduplicator evicts oldest entries when reaching maxEntries
    const dedup = createBoundedEventDeduplicator(3);
    expect(dedup.isDuplicate("e1")).toBe(false);
    expect(dedup.isDuplicate("e1")).toBe(true);
    expect(dedup.isDuplicate("e2")).toBe(false);
    expect(dedup.isDuplicate("e3")).toBe(false);
    expect(dedup.size()).toBe(3);
    expect(dedup.isDuplicate("e4")).toBe(false);
    expect(dedup.size()).toBe(3);
    expect(dedup.isDuplicate("e1")).toBe(false); // e1 evicted when e4 entered

    // Concurrent sockets: 2 clients in tenant-alpha, 1 client in tenant-beta
    const alpha1 = new MockSocket("tenant-alpha");
    const alpha2 = new MockSocket("tenant-alpha");
    const beta1 = new MockSocket("tenant-beta");

    const alpha1Dispatch = vi.fn();
    const alpha2Dispatch = vi.fn();
    const alpha1Recovered = vi.fn();
    const alpha1Sync = vi.fn();
    const betaPayment = vi.fn();

    const unsubA1 = subscribeConcurrentTenantEvents(alpha1, {
      onDispatchUpdate: alpha1Dispatch,
      onRecovered: alpha1Recovered,
      onSyncRequired: alpha1Sync,
    });
    const unsubA2 = subscribeConcurrentTenantEvents(alpha2, {
      onDispatchUpdate: alpha2Dispatch,
    });
    const unsubB1 = subscribeConcurrentTenantEvents(beta1, {
      onPaymentUpdate: betaPayment,
    });

    // Duplicate event_id delivered once per socket; cross-tenant dropped
    const evt = {
      event: "dispatch.updated",
      tenant_id: "tenant-alpha",
      job_id: "job-1",
      assignment_id: "asgn-1",
      event_id: "evt-conc-1",
      correlation_id: "corr-conc-1",
    };
    alpha1.emitLocal("dispatch.updated", evt);
    alpha1.emitLocal("dispatch.updated", evt);
    alpha2.emitLocal("dispatch.updated", evt);
    beta1.emitLocal("payment.updated", {
      event: "payment.updated",
      tenant_id: "tenant-alpha",
      invoice_id: "inv-leak",
      payment_status: "PAID",
      event_id: "evt-leak-1",
    });

    // Simulate reconnect recovery and sync fallback
    alpha1.emitLocal("recovery.status", {
      status: "RECOVERED",
      namespace: "/dispatch-dashboard",
      recovery_window_seconds: 120,
      recovered: true,
    });
    alpha1.emitLocal("recovery.status", {
      status: "SYNC_REQUIRED",
      namespace: "/dispatch-dashboard",
      recovery_window_seconds: 120,
      recovered: false,
    });

    expect(alpha1Dispatch).toHaveBeenCalledTimes(1);
    expect(alpha2Dispatch).toHaveBeenCalledTimes(1);
    expect(betaPayment).not.toHaveBeenCalled();
    expect(alpha1Recovered).toHaveBeenCalledTimes(1);
    expect(alpha1Sync).toHaveBeenCalledTimes(1);

    unsubA1();
    unsubA2();
    unsubB1();
  });
});
