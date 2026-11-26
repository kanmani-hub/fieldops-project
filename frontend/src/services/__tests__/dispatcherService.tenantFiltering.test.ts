import { describe, expect, it, vi } from "vitest";
import {
  DEFAULT_TENANT_FILTERING_CONFIG,
  getTenantRoom,
  isAuthorizedTenantEvent,
  isValidTenantId,
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
});
