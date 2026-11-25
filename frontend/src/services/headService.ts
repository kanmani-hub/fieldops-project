import api from "./api";

export interface PlatformHeadStats {
  total_organizations: number;
  total_jobs: number;
  active_jobs: number;
  completed_jobs: number;
  cancelled_jobs: number;
  total_payment: string;
  raw_payment_amount: number;
  collected_payment_amount: number;
  pending_payment_amount: number;
  payment_records: number;
  payment_status_records: number;
  period: string;
}

export interface PlatformOrganization {
  id: string;
  name: string;
  slug: string;
  status: string;
  subscription_plan: string;
  max_users: number;
  max_technicians: number;
  contact_email?: string | null;
  contact_phone?: string | null;
  super_admin_name?: string | null;
  super_admin_email?: string | null;
  user_count: number;
  dispatcher_count: number;
  technician_count?: number;
  job_count: number;
  completed_job_count?: number;
  active_job_count?: number;
  cancelled_job_count?: number;
  total_revenue?: string;
  total_payment?: string;
  raw_revenue?: number;
  services?: string[];
  created_at?: string | null;
}

export async function getPlatformHeadStats(params?: { tenant_id?: string; period?: "30d" | "90d" | "all" }): Promise<PlatformHeadStats> {
  const response = await api.get<PlatformHeadStats>("/organizations/platform-stats", { params });
  return response.data;
}

export async function getPlatformOrganizations(): Promise<{
  data: PlatformOrganization[];
  total: number;
  page: number;
  limit: number;
}> {
  const response = await api.get<{
    data: PlatformOrganization[];
    total: number;
    page: number;
    limit: number;
  }>("/organizations?limit=100");
  return response.data;
}

export interface PlatformJobItem {
  id: number;
  job_number: string;
  tenant_id: string;
  organization_name: string;
  customer_name: string;
  contact_number: string;
  service_type: string;
  issue_description: string;
  priority: string;
  status: string;
  technician_name: string;
  payment_amount?: number | null;
  formatted_payment?: string;
  payment_status?: string;
  location: string;
  preferred_service_date?: string | null;
  created_at?: string | null;
}

export interface PlatformJobsResponse {
  data: PlatformJobItem[];
  total: number;
  platform_total: number;
  platform_active: number;
  platform_completed: number;
  platform_cancelled: number;
  page: number;
  limit: number;
}

export async function getPlatformJobs(params?: {
  search?: string;
  status?: string;
  payment_status?: string;
  tenant_id?: string;
  period?: "30d" | "90d" | "all";
  page?: number;
  limit?: number;
}): Promise<PlatformJobsResponse> {
  const response = await api.get<PlatformJobsResponse>("/organizations/platform-jobs", {
    params: {
      search: params?.search || undefined,
      status: params?.status && params.status !== "ALL" ? params.status : undefined,
      payment_status: params?.payment_status && params.payment_status !== "ALL" ? params.payment_status : undefined,
      tenant_id: params?.tenant_id && params.tenant_id !== "ALL" ? params.tenant_id : undefined,
      period: params?.period || "all",
      page: params?.page || 1,
      limit: params?.limit || 15,
    },
  });
  return response.data;
}

export async function updatePlatformOrganization(
  orgId: string,
  payload: {
    name?: string;
    subscription_plan?: string;
    contact_email?: string;
    contact_phone?: string;
  }
): Promise<{ message: string; id: string }> {
  const response = await api.put<{ message: string; id: string }>(`/organizations/${orgId}`, payload);
  return response.data;
}

export async function deletePlatformOrganization(orgId: string): Promise<{ message: string; id: string }> {
  const response = await api.delete<{ message: string; id: string }>(`/organizations/${orgId}`);
  return response.data;
}

export async function togglePlatformOrganizationStatus(orgId: string): Promise<{ message: string; id: string; status: string }> {
  const response = await api.post<{ message: string; id: string; status: string }>(`/organizations/${orgId}/toggle-status`);
  return response.data;
}
