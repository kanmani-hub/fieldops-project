import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import {
  LayoutDashboard,
  Building2,
  Briefcase,
  Workflow,
  Settings,
  LogOut,
  Search,
  Bell,
  ShieldCheck,
  Activity,
  CheckCircle2,
  XCircle,
  CreditCard,
  TrendingUp,
  RefreshCw,
  AlertCircle,
  Users,
  Calendar,
  Globe,
  X,
  Plus,
  Lock,
  Key,
  Mail,
  Database,
  FileText,
  Cpu,
  ChevronRight,
  Check,
  SlidersHorizontal,
  ArrowRight,
  Sparkles,
  Shield,
  Layers,
  Server,
  Sliders,
  Eye,
  Pencil,
  Trash2,
  ArrowLeft,
  Power,
  PowerOff,
  Phone,
  UserCheck,
  Wrench,
  Zap,
  Droplets,
  Wind,
  Wifi,
  Paintbrush,
  Clock,
  ArrowUpRight,
  BarChart2,
} from "lucide-react";
import logo from "../../assets/logo.png";
import sidebarDiorama from "../../assets/sidebar-diorama.png";
import orgDetailBanner from "../../assets/org-detail-banner.jpg";
import {
  getPlatformHeadStats,
  getPlatformOrganizations,
  getPlatformJobs,
  updatePlatformOrganization,
  deletePlatformOrganization,
  togglePlatformOrganizationStatus,
  PlatformHeadStats,
  PlatformOrganization,
  PlatformJobItem,
  PlatformJobsResponse,
} from "../../services/headService";
import AgentTrackingPage from "../AgentTrackingPage";

const OrganizationOnboardingPage = lazy(() => import("../OrganizationOnboardingPage"));

export type HeadTab =
  | "dashboard"
  | "organizations"
  | "jobs"
  | "agent_tracking"
  | "settings";

interface HeadPortalProps {
  user: any;
  onLogout: () => void;
  notifications?: any[];
  onOpenNotifications?: () => void;
}

export const HeadPortal: React.FC<HeadPortalProps> = ({
  user,
  onLogout,
  notifications = [],
  onOpenNotifications,
}) => {
  const [activeTab, setActiveTab] = useState<HeadTab>("dashboard");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");

  // Platform Aggregate Stats
  const [stats, setStats] = useState<PlatformHeadStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [statsError, setStatsError] = useState("");
  const [dashboardOrgFilter, setDashboardOrgFilter] = useState("ALL");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);

  // Dashboard Analytics Controls
  const [analyticsTimeframe, setAnalyticsTimeframe] = useState<"30d" | "90d" | "all">("all");
  const [hoveredTrendIndex, setHoveredTrendIndex] = useState<number | null>(null);

  // Organizations List
  const [orgs, setOrgs] = useState<PlatformOrganization[]>([]);
  const [orgsLoading, setOrgsLoading] = useState(false);
  const [orgsError, setOrgsError] = useState("");
  const [orgSearch, setOrgSearch] = useState("");

  // Organization Modals & Action States
  const [viewingOrg, setViewingOrg] = useState<PlatformOrganization | null>(null);
  const [selectedOrgDetail, setSelectedOrgDetail] = useState<PlatformOrganization | null>(null);
  const [orgJobs, setOrgJobs] = useState<PlatformJobItem[]>([]);
  const [orgJobsLoading, setOrgJobsLoading] = useState(false);
  const [orgJobsSearch, setOrgJobsSearch] = useState("");
  const [orgJobsStatusFilter, setOrgJobsStatusFilter] = useState("ALL");
  const [editingOrg, setEditingOrg] = useState<PlatformOrganization | null>(null);
  const [deletingOrg, setDeletingOrg] = useState<PlatformOrganization | null>(null);
  const [editForm, setEditForm] = useState({
    name: "",
    subscription_plan: "FREE",
    contact_email: "",
    contact_phone: "",
  });
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<{ text: string; type: "success" | "error" } | null>(null);

  // Load jobs specific to selected organization detail page
  const loadOrgJobs = useCallback(async (tenantId: string, isSilent = false) => {
    if (!isSilent) {
      setOrgJobsLoading((prev) => (orgJobs.length === 0 ? true : prev));
    }
    try {
      const res = await getPlatformJobs({ tenant_id: tenantId, limit: 200 });
      setOrgJobs(res.data || []);
    } catch (err) {
      console.error("Failed to load org jobs:", err);
      if (!isSilent) setOrgJobs([]);
    } finally {
      if (!isSilent) setOrgJobsLoading(false);
    }
  }, [orgJobs.length]);

  useEffect(() => {
    if (selectedOrgDetail) {
      void loadOrgJobs(selectedOrgDetail.id, false);
    } else {
      setOrgJobs([]);
      setOrgJobsSearch("");
      setOrgJobsStatusFilter("ALL");
    }
  }, [selectedOrgDetail, loadOrgJobs]);

  const getServiceCategoryIcon = (serviceName: string) => {
    const s = (serviceName || "").toLowerCase();
    if (s.includes("hvac") || s.includes("air") || s.includes("cooling") || s.includes("ac")) {
      return <Wind size={20} color="#0284C7" />;
    }
    if (s.includes("electr") || s.includes("power")) {
      return <Zap size={20} color="#D97706" />;
    }
    if (s.includes("plumb") || s.includes("pipe") || s.includes("water")) {
      return <Droplets size={20} color="#2563EB" />;
    }
    if (s.includes("cctv") || s.includes("secur")) {
      return <ShieldCheck size={20} color="#059669" />;
    }
    if (s.includes("net") || s.includes("wifi")) {
      return <Wifi size={20} color="#7C3AED" />;
    }
    if (s.includes("appliance") || s.includes("electron") || s.includes("device") || s.includes("machan")) {
      return <Cpu size={20} color="#EA580C" />;
    }
    if (s.includes("paint") || s.includes("waterproof")) {
      return <Paintbrush size={20} color="#DB2777" />;
    }
    return <Wrench size={20} color="#059669" />;
  };

  const handleOpenEdit = (org: PlatformOrganization) => {
    setEditingOrg(org);
    setEditForm({
      name: org.name || "",
      subscription_plan: org.subscription_plan || "FREE",
      contact_email: org.contact_email || org.super_admin_email || "",
      contact_phone: org.contact_phone || "",
    });
    setActionMessage(null);
  };

  const handleSaveEdit = async () => {
    if (!editingOrg) return;
    setActionLoading(true);
    setActionMessage(null);
    try {
      await updatePlatformOrganization(editingOrg.id, {
        name: editForm.name,
        subscription_plan: editForm.subscription_plan,
        contact_email: editForm.contact_email || undefined,
        contact_phone: editForm.contact_phone || undefined,
      });
      setActionMessage({ text: "Organization updated successfully!", type: "success" });
      await loadOrganizations();
      setTimeout(() => {
        setEditingOrg(null);
        setActionMessage(null);
      }, 700);
    } catch (err: any) {
      setActionMessage({
        text: err?.response?.data?.detail || "Failed to update organization.",
        type: "error",
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!deletingOrg) return;
    setActionLoading(true);
    setActionMessage(null);
    try {
      const isCurrentlyActive = (deletingOrg.status || "").toUpperCase() === "ACTIVE";
      await togglePlatformOrganizationStatus(deletingOrg.id);
      setActionMessage({
        text: isCurrentlyActive ? "Organization marked as INACTIVE!" : "Organization marked as ACTIVE!",
        type: "success",
      });
      await loadOrganizations();
      setTimeout(() => {
        setDeletingOrg(null);
        setActionMessage(null);
      }, 700);
    } catch (err: any) {
      setActionMessage({
        text: err?.response?.data?.detail || "Failed to update organization status.",
        type: "error",
      });
    } finally {
      setActionLoading(false);
    }
  };

  // Load Platform Stats
  const loadStats = useCallback(async () => {
    setStatsError("");
    try {
      const data = await getPlatformHeadStats({ tenant_id: dashboardOrgFilter, period: analyticsTimeframe });
      setStats(data);
      setLastUpdatedAt(new Date());
    } catch (err: any) {
      setStatsError(err?.response?.data?.detail || "Could not refresh dashboard metrics.");
    } finally {
      setStatsLoading(false);
    }
  }, [dashboardOrgFilter, analyticsTimeframe]);

  // Load Organizations List
  const loadOrganizations = useCallback(async (isSilent = false) => {
    if (!isSilent) setOrgsLoading(true);
    setOrgsError("");
    try {
      const res = await getPlatformOrganizations();
      const orgList = res.data || [];
      setOrgs(orgList);
      if (orgList.length > 0) {
        setStats((prev) =>
          prev
            ? { ...prev, total_organizations: res.total || orgList.length }
            : prev,
        );
      }
    } catch (err: any) {
      if (!isSilent) {
        setOrgsError(err?.response?.data?.detail || "Failed to load organizations. Please try again.");
      }
    } finally {
      if (!isSilent) setOrgsLoading(false);
    }
  }, []);

  // Jobs List State
  const [jobs, setJobs] = useState<PlatformJobItem[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState("");
  const [jobsTotal, setJobsTotal] = useState(0);
  const [jobsActiveCount, setJobsActiveCount] = useState(0);
  const [jobsCompletedCount, setJobsCompletedCount] = useState(0);
  const [jobsCancelledCount, setJobsCancelledCount] = useState(0);
  const [jobsSearch, setJobsSearch] = useState("");
  const [jobsStatusFilter, setJobsStatusFilter] = useState("ALL");
  const [jobsPaymentFilter, setJobsPaymentFilter] = useState("ALL");
  const [jobsPeriod, setJobsPeriod] = useState<"30d" | "90d" | "all">("all");
  const [jobsOrgFilter, setJobsOrgFilter] = useState("ALL");
  const [jobsPage, setJobsPage] = useState(1);
  const jobsLimit = 15;

  // Settings & Organization Creation Modal State
  const [isCreateOrgModalOpen, setIsCreateOrgModalOpen] = useState(false);
  const [settingsFilter, setSettingsFilter] = useState<string>("all");
  const [activeConfigModal, setActiveConfigModal] = useState<{
    id: string;
    title: string;
    desc: string;
    category: string;
    fields: { key: string; label: string; value: string; type?: string }[];
  } | null>(null);
  const [configValues, setConfigValues] = useState<Record<string, string>>({
    portalName: "FieldOps Commander",
    timezone: "Asia/Kolkata (IST UTC+05:30)",
    currency: "INR (₹ Indian Rupee)",
    language: "English (en-US)",
    jwtExpiry: "60 minutes",
    refreshExpiry: "7 days",
    lockoutAttempts: "5 attempts / 15m",
    smtpServer: "smtp.fieldops.internal",
    smtpPort: "587",
    smsProvider: "Twilio Gateway",
    auditRetention: "90 Days",
  });
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsCreateOrgModalOpen(false);
        setActiveConfigModal(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Load Platform Jobs
  const loadJobs = useCallback(async (isSilent = false) => {
    if (!isSilent) setJobsLoading(true);
    setJobsError("");
    try {
      const res = await getPlatformJobs({
        search: jobsSearch,
        status: jobsStatusFilter,
        payment_status: jobsPaymentFilter,
        period: jobsPeriod,
        tenant_id: jobsOrgFilter,
        page: jobsPage,
        limit: jobsLimit,
      });
      setJobs(res.data || []);
      setJobsTotal(res.total);
      if (res.platform_total !== undefined) {
        setJobsActiveCount(res.platform_active);
        setJobsCompletedCount(res.platform_completed);
        setJobsCancelledCount(res.platform_cancelled);
        setStats((prev) =>
          prev
            ? {
                ...prev,
                total_jobs: res.platform_total,
                active_jobs: res.platform_active,
                completed_jobs: res.platform_completed,
                cancelled_jobs: res.platform_cancelled,
              }
            : prev,
        );
      }
    } catch (err: any) {
      if (!isSilent) {
        setJobsError(err?.response?.data?.detail || "Failed to load platform jobs. Please try again.");
      }
    } finally {
      if (!isSilent) setJobsLoading(false);
    }
  }, [jobsSearch, jobsStatusFilter, jobsPaymentFilter, jobsOrgFilter, jobsPeriod, jobsPage]);

  useEffect(() => {
    void loadStats();
    void loadOrganizations(false);
  }, [loadStats, loadOrganizations]);

  useEffect(() => {
    if (activeTab === "jobs") {
      void loadJobs(false);
    }
  }, [activeTab, loadJobs]);

  // Real-time Live Synchronization (Reflects new job creation & status transitions automatically)
  useEffect(() => {
    const pollUpdates = () => {
      void loadStats();
      if (activeTab === "organizations") {
        void loadOrganizations(true);
        if (selectedOrgDetail) {
          void loadOrgJobs(selectedOrgDetail.id, true);
        }
      } else if (activeTab === "jobs") {
        void loadJobs(true);
      } else if (activeTab === "dashboard") {
        void loadOrganizations(true);
      }
    };

    const intervalId = setInterval(pollUpdates, 5000);
    const onWindowFocus = () => {
      pollUpdates();
    };
    window.addEventListener("focus", onWindowFocus);

    return () => {
      clearInterval(intervalId);
      window.removeEventListener("focus", onWindowFocus);
    };
  }, [loadStats, loadOrganizations, loadJobs, loadOrgJobs, activeTab, selectedOrgDetail]);

  const unreadCount = notifications.filter(
    (n) => n.status === "UNREAD" || !n.read,
  ).length;

  const displayName =
    [user?.first_name, user?.last_name].filter(Boolean).join(" ").trim() ||
    user?.email?.split("@")[0] ||
    "Platform Head";

  const navItems = [
    { key: "dashboard" as HeadTab, label: "Dashboard", icon: <LayoutDashboard size={20} /> },
    { key: "organizations" as HeadTab, label: "Organization", icon: <Building2 size={20} /> },
    { key: "jobs" as HeadTab, label: "Jobs", icon: <Briefcase size={20} /> },
    { key: "agent_tracking" as HeadTab, label: "Agent Tracking", icon: <Workflow size={20} /> },
    { key: "settings" as HeadTab, label: "Settings", icon: <Settings size={20} /> },
  ];

  // Filter organizations by search
  const filteredOrgs = orgs.filter((org) => {
    const q = orgSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      (org.name || "").toLowerCase().includes(q) ||
      (org.id || "").toLowerCase().includes(q) ||
      (org.slug || "").toLowerCase().includes(q) ||
      (org.super_admin_name || "").toLowerCase().includes(q) ||
      (org.super_admin_email || "").toLowerCase().includes(q) ||
      (org.contact_email || "").toLowerCase().includes(q)
    );
  });

  const openDashboardJobs = () => {
    setJobsOrgFilter(dashboardOrgFilter);
    setJobsPaymentFilter("ALL");
    setJobsPeriod(analyticsTimeframe);
    setJobsPage(1);
    setActiveTab("jobs");
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        width: "100vw",
        overflow: "hidden",
        background: "#EEF9F1",
        fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      }}
    >
      {/* ──────────────────────────────────────────────────
          TOP NAVBAR
          Left: FieldOps Commander Logo
          Right: Search, Notification, Platform Head
         ────────────────────────────────────────────────── */}
      <header
        style={{
          height: "64px",
          minHeight: "64px",
          background: "#FFFFFF",
          borderBottom: "1px solid #E2E8F0",
          boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 20px",
          zIndex: 100,
          boxSizing: "border-box",
        }}
      >
        {/* Left Side: FieldOps Commander Logo */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <img
            src={logo}
            alt="FieldOps Commander"
            style={{
              height: "40px",
              maxWidth: "180px",
              objectFit: "contain",
              cursor: "pointer",
            }}
            onClick={() => setActiveTab("dashboard")}
          />
        </div>

        {/* Right Side: Search, Notification, Platform Head */}
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          {/* Search Box */}
          <div
            style={{
              position: "relative",
              display: "flex",
              alignItems: "center",
            }}
          >
            <Search
              size={16}
              style={{
                position: "absolute",
                left: "12px",
                color: "#94A3B8",
                pointerEvents: "none",
              }}
            />
            <input
              type="text"
              placeholder="Search..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                padding: "8px 14px 8px 36px",
                fontSize: "13px",
                borderRadius: "20px",
                border: "1px solid #CBD5E1",
                background: "#F8FAFC",
                color: "#1E293B",
                outline: "none",
                width: "220px",
                transition: "all 0.2s ease",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = "#10B981";
                e.currentTarget.style.background = "#FFFFFF";
                e.currentTarget.style.boxShadow = "0 0 0 2px rgba(16,185,129,0.15)";
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "#CBD5E1";
                e.currentTarget.style.background = "#F8FAFC";
                e.currentTarget.style.boxShadow = "none";
              }}
            />
          </div>

          {/* Notification Button */}
          <button
            type="button"
            onClick={onOpenNotifications}
            title="Notifications"
            style={{
              position: "relative",
              background: "#F8FAFC",
              border: "1px solid #E2E8F0",
              borderRadius: "10px",
              width: "38px",
              height: "38px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
              color: "#475569",
              transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "#EDF7F1";
              e.currentTarget.style.color = "#10B981";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "#F8FAFC";
              e.currentTarget.style.color = "#475569";
            }}
          >
            <Bell size={18} />
            {unreadCount > 0 && (
              <span
                style={{
                  position: "absolute",
                  top: "-4px",
                  right: "-4px",
                  background: "#EF4444",
                  color: "#FFFFFF",
                  fontSize: "10px",
                  fontWeight: 700,
                  borderRadius: "10px",
                  padding: "1px 5px",
                  minWidth: "16px",
                  textAlign: "center",
                  boxShadow: "0 1px 2px rgba(0,0,0,0.2)",
                }}
              >
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </button>

          {/* Platform Head User Badge */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "4px 12px 4px 6px",
              background: "#F0FDF4",
              border: "1px solid #BBF7D0",
              borderRadius: "24px",
            }}
          >
            <div
              style={{
                width: "32px",
                height: "32px",
                borderRadius: "50%",
                background: "#10B981",
                color: "#FFFFFF",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontWeight: 700,
                fontSize: "14px",
                boxShadow: "0 1px 3px rgba(16,185,129,0.3)",
              }}
            >
              <ShieldCheck size={18} />
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span
                style={{
                  fontSize: "11px",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "#059669",
                  lineHeight: "1.2",
                }}
              >
                Platform Head
              </span>
              <span
                style={{
                  fontSize: "13px",
                  fontWeight: 600,
                  color: "#0F172A",
                  lineHeight: "1.2",
                  maxWidth: "140px",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {displayName}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* ──────────────────────────────────────────────────
          MAIN BODY LAYOUT (SIDEBAR + MAIN CANVAS)
         ────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          position: "relative",
        }}
      >
        {/* Sidebar */}
        <aside
          style={{
            width: sidebarCollapsed ? "64px" : "240px",
            minWidth: sidebarCollapsed ? "64px" : "240px",
            backgroundColor: "#ECFBF4",
            backgroundImage: sidebarCollapsed
              ? "none"
              : `linear-gradient(180deg, #ECFBF4 0%, #ECFBF4 20%, rgba(236, 251, 244, 0) 45%), url(${sidebarDiorama})`,
            backgroundRepeat: "no-repeat",
            backgroundPosition: "bottom center",
            backgroundSize: "100% auto",
            borderRight: "1px solid #BFE5CF",
            boxShadow: "2px 0 12px rgba(16, 185, 129, 0.05)",
            display: "flex",
            flexDirection: "column",
            height: "100%",
            position: "relative",
            zIndex: 90,
            overflow: "hidden",
            transition: "width 0.25s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
            boxSizing: "border-box",
          }}
        >
          {/* Customer-style attached semi-circle toggle button with 3 lines */}
          <button
            type="button"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label="Toggle sidebar"
            style={{
              position: "absolute",
              right: "0px",
              top: "16px",
              width: "18px",
              height: "32px",
              borderTopLeftRadius: "16px",
              borderBottomLeftRadius: "16px",
              borderTopRightRadius: "0px",
              borderBottomRightRadius: "0px",
              background: "#FFFFFF",
              border: "1px solid #A7F3D0",
              borderRight: "none",
              boxShadow: "-2px 1px 6px rgba(16, 185, 129, 0.12)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
              zIndex: 300,
              padding: "0 0 0 2px",
              transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "#ECFDF5";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "#FFFFFF";
            }}
          >
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "2.5px",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <span
                style={{
                  width: "9px",
                  height: "1.5px",
                  backgroundColor: "#059669",
                  borderRadius: "1px",
                  display: "block",
                }}
              />
              <span
                style={{
                  width: "9px",
                  height: "1.5px",
                  backgroundColor: "#059669",
                  borderRadius: "1px",
                  display: "block",
                }}
              />
              <span
                style={{
                  width: "9px",
                  height: "1.5px",
                  backgroundColor: "#059669",
                  borderRadius: "1px",
                  display: "block",
                }}
              />
            </div>
          </button>

          {/* Navigation Links */}
          <nav
            style={{
              display: "flex",
              flexDirection: "column",
              padding: sidebarCollapsed ? "20px 8px" : "20px 12px",
              gap: "6px",
              position: "relative",
              zIndex: 10,
              flexShrink: 0,
            }}
          >
            {navItems.map((item) => {
              const isSelected = activeTab === item.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => {
                    if (activeTab === item.key) {
                      if (item.key === "organizations" && selectedOrgDetail) {
                        setSelectedOrgDetail(null);
                      }
                      return;
                    }
                    setActiveTab(item.key);
                    if (item.key === "organizations") {
                      setSelectedOrgDetail(null);
                    }
                  }}
                  title={item.label}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                    padding: sidebarCollapsed ? "12px 0" : "11px 14px",
                    justifyContent: sidebarCollapsed ? "center" : "flex-start",
                    width: "100%",
                    borderRadius: "10px",
                    border: "none",
                    background: isSelected
                      ? "linear-gradient(135deg, #10B981 0%, #059669 100%)"
                      : "transparent",
                    color: isSelected ? "#FFFFFF" : "#1F2937",
                    fontWeight: isSelected ? 700 : 500,
                    fontSize: "14px",
                    cursor: "pointer",
                    transition: "all 0.18s cubic-bezier(0.4, 0, 0.2, 1)",
                    boxShadow: isSelected
                      ? "0 4px 14px rgba(16, 185, 129, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.2)"
                      : "none",
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected) {
                      e.currentTarget.style.background = "rgba(16, 185, 129, 0.12)";
                      e.currentTarget.style.color = "#065F46";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) {
                      e.currentTarget.style.background = "transparent";
                      e.currentTarget.style.color = "#1F2937";
                    }
                  }}
                >
                  <span
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: isSelected ? "#FFFFFF" : "#059669",
                      flexShrink: 0,
                      transition: "color 0.15s ease",
                    }}
                  >
                    {item.icon}
                  </span>
                  {!sidebarCollapsed && (
                    <span
                      style={{
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        flex: 1,
                        textAlign: "left",
                      }}
                    >
                      {item.label}
                    </span>
                  )}
                  {isSelected && !sidebarCollapsed && (
                    <span
                      style={{
                        width: "6px",
                        height: "6px",
                        borderRadius: "50%",
                        background: "#A7F3D0",
                        boxShadow: "0 0 6px #A7F3D0",
                        display: "inline-block",
                        flexShrink: 0,
                      }}
                    />
                  )}
                </button>
              );
            })}
          </nav>

          {/* Spacer letting the 3D diorama background breathe */}
          <div style={{ flex: 1, minHeight: "40px" }} />

          {/* Logout at bottom */}
          <div
            style={{
              padding: sidebarCollapsed ? "16px 8px" : "12px 12px 20px 12px",
              background: "transparent",
              position: "relative",
              zIndex: 10,
              marginTop: "auto",
            }}
          >
            <button
              type="button"
              onClick={onLogout}
              title="Logout"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "12px",
                padding: sidebarCollapsed ? "12px 0" : "11px 14px",
                justifyContent: sidebarCollapsed ? "center" : "flex-start",
                width: "100%",
                borderRadius: "10px",
                border: "none",
                background: "transparent",
                color: "#DC2626",
                fontWeight: 600,
                fontSize: "14px",
                cursor: "pointer",
                transition: "all 0.18s cubic-bezier(0.4, 0, 0.2, 1)",
                boxSizing: "border-box",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "#FEE2E2";
                e.currentTarget.style.color = "#B91C1C";
                e.currentTarget.style.boxShadow = "0 2px 8px rgba(220, 38, 38, 0.12)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "#DC2626";
                e.currentTarget.style.boxShadow = "none";
              }}
            >
              <LogOut size={20} style={{ flexShrink: 0 }} />
              {!sidebarCollapsed && <span>Logout</span>}
            </button>
          </div>
        </aside>

        {/* ──────────────────────────────────────────────────
            MAIN CONTENT AREA
           ────────────────────────────────────────────────── */}
        <main
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            backgroundColor: "#EEF9F1",
            padding: activeTab === "agent_tracking" ? "0px" : "24px",
            boxSizing: "border-box",
          }}
        >
          {/* Agent Tracking */}
          {activeTab === "agent_tracking" && (
            <AgentTrackingPage />
          )}

          {/* Dashboard */}
          {activeTab === "dashboard" && (
            <div style={{ maxWidth: "1280px", margin: "0 auto" }}>
              {/* ──────────────────────────────────────────────
                  EXECUTIVE PLATFORM ANALYTICS & REVENUE INTELLIGENCE CONSOLE
                  (Replaces static discrete cards with dynamic analytics telemetry)
                 ────────────────────────────────────────────── */}
              <div
                style={{
                  background: "#FFFFFF",
                  borderRadius: "20px",
                  border: "1px solid #E2E8F0",
                  padding: "24px 28px",
                  boxShadow: "0 4px 16px -2px rgba(0, 0, 0, 0.04)",
                  marginBottom: "28px",
                }}
              >
                {/* Header with Title and Timeframe Toggle */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "24px",
                    flexWrap: "wrap",
                    gap: "14px",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "6px",
                          fontSize: "11px",
                          fontWeight: 700,
                          textTransform: "uppercase",
                          letterSpacing: "0.06em",
                          color: "#059669",
                          background: "#ECFDF5",
                          padding: "3px 10px",
                          borderRadius: "12px",
                          border: "1px solid #A7F3D0",
                        }}
                      >
                        <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: "#10B981" }} />
                        Platform Telemetry • Live Analytics
                      </span>
                    </div>
                    <h3
                      style={{
                        fontSize: "20px",
                        fontWeight: 800,
                        color: "#0F172A",
                        margin: 0,
                        display: "flex",
                        alignItems: "center",
                        gap: "10px",
                      }}
                    >
                      <TrendingUp size={22} color="#059669" />
                      Platform Analytics & Revenue Intelligence
                    </h3>
                    <p style={{ fontSize: "13px", color: "#64748B", margin: "4px 0 0 0" }}>
                      Organization scoped operational totals and recorded closure/payment reconciliation.
                    </p>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                    <select aria-label="Dashboard organization" value={dashboardOrgFilter} onChange={(e) => setDashboardOrgFilter(e.target.value)} style={{ padding: "8px 10px", borderRadius: "9px", border: "1px solid #CBD5E1", background: "#FFFFFF", color: "#334155", fontSize: "12px", maxWidth: "240px" }}>
                      <option value="ALL">All organizations</option>
                      {orgs.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
                    </select>
                    <div
                      style={{
                        display: "inline-flex",
                        background: "#F1F5F9",
                        padding: "3px",
                        borderRadius: "10px",
                        border: "1px solid #E2E8F0",
                      }}
                    >
                      {[
                        { key: "30d", label: "Last 30 Days" },
                        { key: "90d", label: "Last 90 Days" },
                        { key: "all", label: "Lifetime" },
                      ].map((tf) => (
                        <button
                          key={tf.key}
                          type="button"
                          onClick={() => setAnalyticsTimeframe(tf.key as any)}
                          style={{
                            padding: "6px 14px",
                            fontSize: "12px",
                            fontWeight: analyticsTimeframe === tf.key ? 700 : 500,
                            borderRadius: "8px",
                            border: "none",
                            background: analyticsTimeframe === tf.key ? "#FFFFFF" : "transparent",
                            color: analyticsTimeframe === tf.key ? "#065F46" : "#64748B",
                            boxShadow:
                              analyticsTimeframe === tf.key
                                ? "0 1px 3px rgba(0,0,0,0.08)"
                                : "none",
                            cursor: "pointer",
                            transition: "all 0.15s ease",
                          }}
                        >
                          {tf.label}
                        </button>
                      ))}
                    </div>

                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "6px",
                        fontSize: "12px",
                        fontWeight: 600,
                        color: "#059669",
                        background: "#ECFDF5",
                        padding: "6px 12px",
                        borderRadius: "10px",
                        border: "1px solid #A7F3D0",
                      }}
                    >
                      <RefreshCw size={13} className={statsLoading ? "is-spinning" : ""} />
                      {lastUpdatedAt ? `Updated ${lastUpdatedAt.toLocaleTimeString()}` : "Updating…"}
                    </span>
                  </div>
                </div>

                {statsError && (
                  <div role="status" style={{ margin: "-8px 0 18px", padding: "10px 12px", borderRadius: "8px", background: "#FEF2F2", color: "#991B1B", fontSize: "12px" }}>
                    {statsError} The last successfully loaded values remain visible.
                  </div>
                )}

                {/* Unified Analytical Telemetry Ribbon */}
                <div
                  style={{
                    background: "#F8FAFC",
                    border: "1px solid #E2E8F0",
                    borderRadius: "16px",
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
                    overflow: "hidden",
                    marginBottom: "24px",
                  }}
                >
                  {/* Segment 1: Revenue */}
                  <div
                    style={{
                      padding: "16px 20px",
                      borderRight: "1px solid #E2E8F0",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      transition: "background 0.15s ease",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.04em" }}>
                        Recorded Closure Value
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 700, color: "#16A34A", background: "#DCFCE7", padding: "2px 6px", borderRadius: "10px", display: "inline-flex", alignItems: "center", gap: "2px" }}>
                        Payment records
                      </span>
                    </div>
                    <div style={{ fontSize: "24px", fontWeight: 800, color: "#065F46", lineHeight: 1.1 }}>
                      {statsLoading ? "—" : stats?.total_payment || "—"}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
                      {dashboardOrgFilter === "ALL" ? "Across all organizations" : orgs.find((org) => org.id === dashboardOrgFilter)?.name || "Selected organization"}
                    </div>
                  </div>

                  {/* Segment 2: Total Jobs */}
                  <div
                    onClick={openDashboardJobs}
                    style={{
                      padding: "16px 20px",
                      borderRight: "1px solid #E2E8F0",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      cursor: "pointer",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F1F5F9")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.04em" }}>
                        Total Dispatches
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 700, color: "#2563EB", background: "#EFF6FF", padding: "2px 6px", borderRadius: "10px" }}>
                        {analyticsTimeframe === "all" ? "Lifetime" : analyticsTimeframe === "30d" ? "30 days" : "90 days"}
                      </span>
                    </div>
                    <div style={{ fontSize: "24px", fontWeight: 800, color: "#1E40AF", lineHeight: 1.1 }}>
                      {statsLoading ? "—" : stats?.total_jobs ?? "—"}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
                      Jobs in the selected organization and period
                    </div>
                  </div>

                  {/* Segment 3: Active Jobs */}
                  <div
                    onClick={openDashboardJobs}
                    style={{
                      padding: "16px 20px",
                      borderRight: "1px solid #E2E8F0",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      cursor: "pointer",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F1F5F9")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.04em" }}>
                        Active Workload
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 700, color: "#B45309", background: "#FEF3C7", padding: "2px 6px", borderRadius: "10px" }}>
                        Live Fleet
                      </span>
                    </div>
                    <div style={{ fontSize: "24px", fontWeight: 800, color: "#B45309", lineHeight: 1.1 }}>
                      {statsLoading ? "—" : stats?.active_jobs ?? "—"}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
                      In progress & on-site
                    </div>
                  </div>

                  {/* Segment 4: Completed Jobs */}
                  <div
                    onClick={openDashboardJobs}
                    style={{
                      padding: "16px 20px",
                      borderRight: "1px solid #E2E8F0",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      cursor: "pointer",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F1F5F9")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.04em" }}>
                        Jobs Completed
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 700, color: "#15803D", background: "#DCFCE7", padding: "2px 6px", borderRadius: "10px" }}>
                        Closed
                      </span>
                    </div>
                    <div style={{ fontSize: "24px", fontWeight: 800, color: "#15803D", lineHeight: 1.1 }}>
                      {statsLoading ? "—" : stats?.completed_jobs ?? "—"}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
                      Successfully signed off
                    </div>
                  </div>

                  {/* Segment 5: Cancelled Jobs */}
                  <div
                    onClick={openDashboardJobs}
                    style={{
                      padding: "16px 20px",
                      borderRight: "1px solid #E2E8F0",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      cursor: "pointer",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F1F5F9")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.04em" }}>
                        Cancelled Jobs
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 700, color: "#B91C1C", background: "#FEE2E2", padding: "2px 6px", borderRadius: "10px" }}>
                        Void
                      </span>
                    </div>
                    <div style={{ fontSize: "24px", fontWeight: 800, color: "#B91C1C", lineHeight: 1.1 }}>
                      {statsLoading ? "—" : stats?.cancelled_jobs ?? "—"}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
                      Discontinued requests
                    </div>
                  </div>

                  {/* Segment 6: Partner Organizations */}
                  <div
                    onClick={() => setActiveTab("organizations")}
                    style={{
                      padding: "16px 20px",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      cursor: "pointer",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F1F5F9")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.04em" }}>
                        Super Admin Orgs
                      </span>
                      <span style={{ fontSize: "10px", fontWeight: 700, color: "#059669", background: "#ECFDF5", padding: "2px 6px", borderRadius: "10px" }}>
                        Networks
                      </span>
                    </div>
                    <div style={{ fontSize: "24px", fontWeight: 800, color: "#065F46", lineHeight: 1.1 }}>
                      {statsLoading ? "—" : stats?.total_organizations ?? orgs.length}
                    </div>
                    <div style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
                      Verified partner tenants
                    </div>
                  </div>
                </div>

                {/* Verified payment summary and organization comparison */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "18px", marginBottom: "28px" }}>
                  <div style={{ background: "#FFFFFF", border: "1px solid #E2E8F0", borderRadius: "16px", padding: "20px" }}>
                    <h3 style={{ margin: "0 0 6px", fontSize: "15px", color: "#0F172A" }}>Payment reconciliation</h3>
                    <p style={{ margin: "0 0 16px", fontSize: "12px", color: "#64748B" }}>Closure totals and persisted payment status records for the selected scope.</p>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                      <div><div style={{ fontSize: "11px", color: "#64748B" }}>Recorded closure value</div><strong style={{ fontSize: "20px", color: "#0F766E" }}>{statsLoading ? "—" : `₹${(stats?.raw_payment_amount || 0).toLocaleString("en-IN")}`}</strong></div>
                      <div><div style={{ fontSize: "11px", color: "#64748B" }}>Subtotal with successful status</div><strong style={{ fontSize: "20px", color: "#15803D" }}>{statsLoading ? "—" : `₹${(stats?.collected_payment_amount || 0).toLocaleString("en-IN")}`}</strong></div>
                      <div><div style={{ fontSize: "11px", color: "#64748B" }}>Subtotal with pending status</div><strong style={{ fontSize: "18px", color: "#B45309" }}>{statsLoading ? "—" : `₹${(stats?.pending_payment_amount || 0).toLocaleString("en-IN")}`}</strong></div>
                      <div><div style={{ fontSize: "11px", color: "#64748B" }}>Closures with status</div><strong style={{ fontSize: "18px", color: "#334155" }}>{statsLoading ? "—" : `${stats?.payment_status_records || 0} / ${stats?.payment_records || 0}`}</strong></div>
                    </div>
                    <button type="button" onClick={() => { setJobsOrgFilter(dashboardOrgFilter); setJobsPeriod(analyticsTimeframe); setJobsPage(1); setActiveTab("jobs"); }} style={{ marginTop: "16px", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1", background: "#F8FAFC", color: "#0F172A", cursor: "pointer", fontWeight: 600 }}>Review job payment records</button>
                  </div>
                  <div style={{ background: "#FFFFFF", border: "1px solid #E2E8F0", borderRadius: "16px", padding: "20px" }}>
                    <h3 style={{ margin: "0 0 6px", fontSize: "15px", color: "#0F172A" }}>Organization comparison</h3>
                    <p style={{ margin: "0 0 12px", fontSize: "12px", color: "#64748B" }}>Lifetime closure totals from each organization. Select one above to inspect its filtered job metrics.</p>
                    <div style={{ maxHeight: "190px", overflowY: "auto" }}>
                      {orgs.length ? orgs.map((org) => (
                        <button key={org.id} type="button" onClick={() => setDashboardOrgFilter(org.id)} style={{ width: "100%", display: "flex", justifyContent: "space-between", gap: "12px", padding: "9px 0", border: 0, borderBottom: "1px solid #F1F5F9", background: "transparent", textAlign: "left", cursor: "pointer" }}>
                          <span style={{ color: "#334155", fontSize: "12px", fontWeight: 600 }}>{org.name}<small style={{ display: "block", color: "#94A3B8", fontWeight: 400 }}>{org.job_count} jobs • {org.completed_job_count || 0} completed</small></span>
                          <strong style={{ color: "#0F766E", fontSize: "12px", whiteSpace: "nowrap" }}>{org.total_revenue || "—"}</strong>
                        </button>
                      )) : <div style={{ padding: "16px 0", color: "#64748B", fontSize: "13px" }}>Organization data is unavailable.</div>}
                    </div>
                  </div>
                </div>

              </div>

              {/* Section 2: Partner Organizations Capacity & Fleet Table */}
              <div
                style={{
                  background: "#FFFFFF",
                  borderRadius: "20px",
                  border: "1px solid #E2E8F0",
                  padding: "24px 28px",
                  boxShadow: "0 4px 16px -2px rgba(0, 0, 0, 0.04)",
                  marginBottom: "28px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "18px", flexWrap: "wrap", gap: "12px" }}>
                  <div>
                    <h3 style={{ fontSize: "18px", fontWeight: 700, color: "#0F172A", margin: 0, display: "flex", alignItems: "center", gap: "10px" }}>
                      <Building2 size={20} color="#059669" />
                      Partner Organizations Fleet & Service Capacity
                    </h3>
                    <p style={{ fontSize: "13px", color: "#64748B", margin: "4px 0 0 0" }}>
                      Verified partner organizations registered under Super Admin management
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setActiveTab("organizations")}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      padding: "8px 16px",
                      borderRadius: "10px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#0F172A",
                      fontSize: "13px",
                      fontWeight: 600,
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F8FAFC")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "#FFFFFF")}
                  >
                    <span>View All ({stats?.total_organizations ?? orgs.length})</span>
                    <ArrowRight size={14} />
                  </button>
                </div>

                {/* Table Preview */}
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
                    <thead>
                      <tr style={{ borderBottom: "1.5px solid #F1F5F9", background: "#F8FAFC" }}>
                        <th style={{ padding: "12px 16px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.05em" }}>Organization</th>
                        <th style={{ padding: "12px 16px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.05em" }}>Super Admin</th>
                        <th style={{ padding: "12px 16px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.05em" }}>Technicians</th>
                        <th style={{ padding: "12px 16px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.05em" }}>Dispatches</th>
                        <th style={{ padding: "12px 16px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.05em" }}>Status</th>
                        <th style={{ padding: "12px 16px", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.05em", textAlign: "right" }}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(orgs.slice(0, 5)).map((org) => (
                        <tr
                          key={org.id}
                          style={{ borderBottom: "1px solid #F1F5F9", transition: "background 0.15s ease" }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = "#F8FAFC")}
                          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                        >
                          <td style={{ padding: "14px 16px" }}>
                            <div style={{ fontWeight: 700, color: "#0F172A", fontSize: "14px" }}>{org.name}</div>
                            <div style={{ fontSize: "11px", color: "#64748B", fontFamily: "monospace" }}>{org.id}</div>
                          </td>
                          <td style={{ padding: "14px 16px" }}>
                            <div style={{ fontWeight: 600, color: "#334155", fontSize: "13px" }}>{org.super_admin_name || "Super Admin"}</div>
                            <div style={{ fontSize: "11px", color: "#059669" }}>{org.contact_email || org.super_admin_email || "—"}</div>
                          </td>
                          <td style={{ padding: "14px 16px" }}>
                            <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "13px", fontWeight: 700, color: "#0F172A" }}>
                              <Users size={15} color="#059669" />
                              {org.technician_count ?? (org as any).technicians_count ?? 1} Techs
                            </span>
                          </td>
                          <td style={{ padding: "14px 16px" }}>
                            <span style={{ fontSize: "13px", fontWeight: 700, color: "#2563EB" }}>
                              {org.job_count || (org as any).jobs_count || 0} Jobs
                            </span>
                          </td>
                          <td style={{ padding: "14px 16px" }}>
                            <span
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "5px",
                                fontSize: "11px",
                                fontWeight: 700,
                                padding: "4px 10px",
                                borderRadius: "14px",
                                background: org.status === "ACTIVE" ? "#ECFDF5" : "#FEF2F2",
                                color: org.status === "ACTIVE" ? "#059669" : "#DC2626",
                              }}
                            >
                              <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: org.status === "ACTIVE" ? "#10B981" : "#EF4444" }} />
                              {org.status || "ACTIVE"}
                            </span>
                          </td>
                          <td style={{ padding: "14px 16px", textAlign: "right" }}>
                            <button
                              type="button"
                              onClick={() => {
                                setSelectedOrgDetail(org);
                                setActiveTab("organizations");
                              }}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "6px",
                                padding: "6px 14px",
                                borderRadius: "8px",
                                border: "1px solid #E2E8F0",
                                background: "#FFFFFF",
                                color: "#059669",
                                fontSize: "12px",
                                fontWeight: 700,
                                cursor: "pointer",
                                transition: "all 0.15s ease",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "#ECFDF5";
                                e.currentTarget.style.borderColor = "#A7F3D0";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "#FFFFFF";
                                e.currentTarget.style.borderColor = "#E2E8F0";
                              }}
                            >
                              <Eye size={13} />
                              <span>View Org</span>
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Section 3: Platform Infrastructure & Gateways Telemetry */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                  gap: "18px",
                }}
              >
                <div
                  style={{
                    background: "#FFFFFF",
                    borderRadius: "16px",
                    border: "1px solid #E2E8F0",
                    padding: "20px",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "16px",
                  }}
                >
                  <div style={{ width: "42px", height: "42px", borderRadius: "12px", background: "#ECFDF5", color: "#059669", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <ShieldCheck size={22} />
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                      <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>Multi-Tenant Security</span>
                      <span style={{ fontSize: "11px", fontWeight: 700, color: "#059669", background: "#ECFDF5", padding: "2px 8px", borderRadius: "10px" }}>Active</span>
                    </div>
                    <div style={{ fontSize: "12px", color: "#64748B", lineHeight: 1.5 }}>
                      Strict database tenant isolation enforced with cryptographic session authentication and immutable audit trail.
                    </div>
                  </div>
                </div>

                <div
                  style={{
                    background: "#FFFFFF",
                    borderRadius: "16px",
                    border: "1px solid #E2E8F0",
                    padding: "20px",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "16px",
                  }}
                >
                  <div style={{ width: "42px", height: "42px", borderRadius: "12px", background: "#EFF6FF", color: "#2563EB", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <Cpu size={22} />
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                      <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>Dispatch Engine</span>
                      <span style={{ fontSize: "11px", fontWeight: 700, color: "#2563EB", background: "#EFF6FF", padding: "2px 8px", borderRadius: "10px" }}>Operational</span>
                    </div>
                    <div style={{ fontSize: "12px", color: "#64748B", lineHeight: 1.5 }}>
                      Automated proximity routing and technician assignment daemon running with real-time WebSocket state distribution.
                    </div>
                  </div>
                </div>

                <div
                  style={{
                    background: "#FFFFFF",
                    borderRadius: "16px",
                    border: "1px solid #E2E8F0",
                    padding: "20px",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "16px",
                  }}
                >
                  <div style={{ width: "42px", height: "42px", borderRadius: "12px", background: "#FFFBEB", color: "#D97706", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <Zap size={22} />
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                      <span style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A" }}>Gateway Infrastructure</span>
                      <span style={{ fontSize: "11px", fontWeight: 700, color: "#D97706", background: "#FFFBEB", padding: "2px 8px", borderRadius: "10px" }}>Connected</span>
                    </div>
                    <div style={{ fontSize: "12px", color: "#64748B", lineHeight: 1.5 }}>
                      Real-time SMS gateway, SMTP notifications, and Ola Maps reverse-geocoding endpoints operating within nominal latency.
                    </div>
                  </div>
                </div>
              </div>

            </div>
          )}

          {/* ──────────────────────────────────────────────────
              ORGANIZATION DETAIL VIEW (NEW PAGE INSTEAD OF POPUP)
              Rendered when user clicks View (Eye) on an organization
             ────────────────────────────────────────────────── */}
          {activeTab === "organizations" && selectedOrgDetail && (() => {
            const displayedServices =
              selectedOrgDetail.services && selectedOrgDetail.services.length > 0
                ? selectedOrgDetail.services
                : orgJobs.length > 0
                ? Array.from(new Set(orgJobs.map((j) => j.service_type).filter(Boolean)))
                : [];

            const filteredOrgJobs = orgJobs.filter((job) => {
              if (orgJobsStatusFilter !== "ALL") {
                const s = job.status.toUpperCase();
                if (orgJobsStatusFilter === "ACTIVE") {
                  if (["COMPLETED", "CLOSED", "CANCELLED", "CANCELED"].includes(s)) return false;
                } else if (orgJobsStatusFilter === "COMPLETED") {
                  if (!["COMPLETED", "CLOSED"].includes(s)) return false;
                } else if (orgJobsStatusFilter === "CANCELLED") {
                  if (!["CANCELLED", "CANCELED"].includes(s)) return false;
                } else if (s !== orgJobsStatusFilter) {
                  return false;
                }
              }
              if (orgJobsSearch.trim()) {
                const q = orgJobsSearch.toLowerCase().trim();
                const matchNumber = job.job_number.toLowerCase().includes(q);
                const matchCustomer = job.customer_name.toLowerCase().includes(q);
                const matchService = job.service_type.toLowerCase().includes(q);
                const matchTech = job.technician_name.toLowerCase().includes(q);
                const matchDesc = (job.issue_description || "").toLowerCase().includes(q);
                if (!matchNumber && !matchCustomer && !matchService && !matchTech && !matchDesc) {
                  return false;
                }
              }
              return true;
            });

            const formattedOrgPayment = selectedOrgDetail.total_payment || selectedOrgDetail.total_revenue || "—";

            return (
              <div style={{ maxWidth: "1280px", margin: "0 auto" }}>
                {/* Back to Organizations Navigation Button */}
                <div style={{ marginBottom: "20px" }}>
                  <button
                    type="button"
                    onClick={() => setSelectedOrgDetail(null)}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "8px",
                      padding: "8px 16px",
                      borderRadius: "10px",
                      border: "1px solid #A7F3D0",
                      background: "#ECFDF5",
                      color: "#065F46",
                      fontSize: "13px",
                      fontWeight: 700,
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                      boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "#D1FAE5";
                      e.currentTarget.style.borderColor = "#6EE7B7";
                      e.currentTarget.style.transform = "translateX(-2px)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "#ECFDF5";
                      e.currentTarget.style.borderColor = "#A7F3D0";
                      e.currentTarget.style.transform = "translateX(0)";
                    }}
                  >
                    <ArrowLeft size={16} />
                    <span>Back to Organizations</span>
                  </button>
                </div>

                {/* Hero Panoramic Organization Banner */}
                <div
                  style={{
                    position: "relative",
                    borderRadius: "20px",
                    overflow: "hidden",
                    marginBottom: "24px",
                    boxShadow: "0 10px 30px -5px rgba(16, 185, 129, 0.18)",
                    border: "1px solid rgba(16, 185, 129, 0.25)",
                    minHeight: "180px",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "flex-end",
                  }}
                >
                  {/* Banner Image */}
                  <img
                    src={orgDetailBanner}
                    alt="Organization Banner"
                    style={{
                      position: "absolute",
                      inset: 0,
                      width: "100%",
                      height: "100%",
                      objectFit: "cover",
                      objectPosition: "center",
                    }}
                  />
                  {/* Gradient Tint Overlay */}
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      background: "linear-gradient(135deg, rgba(6, 78, 59, 0.88) 0%, rgba(15, 118, 110, 0.78) 50%, rgba(16, 185, 129, 0.65) 100%)",
                      backdropFilter: "blur(1px)",
                    }}
                  />

                  {/* Banner Content Container */}
                  <div
                    style={{
                      position: "relative",
                      zIndex: 2,
                      padding: "24px 28px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      flexWrap: "wrap",
                      gap: "20px",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "18px" }}>
                      <div
                        style={{
                          width: "64px",
                          height: "64px",
                          borderRadius: "16px",
                          background: "#FFFFFF",
                          color: "#059669",
                          fontSize: "28px",
                          fontWeight: 800,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          boxShadow: "0 8px 20px rgba(0, 0, 0, 0.2)",
                          border: "2px solid rgba(255, 255, 255, 0.8)",
                          flexShrink: 0,
                        }}
                      >
                        {(selectedOrgDetail.name || "O").charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "6px" }}>
                          <h1
                            style={{
                              fontSize: "26px",
                              fontWeight: 800,
                              color: "#FFFFFF",
                              margin: 0,
                              textShadow: "0 2px 4px rgba(0,0,0,0.2)",
                            }}
                          >
                            {selectedOrgDetail.name}
                          </h1>
                          <span
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "5px",
                              padding: "4px 12px",
                              borderRadius: "20px",
                              fontSize: "11px",
                              fontWeight: 700,
                              background: (selectedOrgDetail.status || "").toUpperCase() === "ACTIVE" ? "rgba(220, 252, 231, 0.95)" : "rgba(254, 243, 199, 0.95)",
                              color: (selectedOrgDetail.status || "").toUpperCase() === "ACTIVE" ? "#15803D" : "#B45309",
                              boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
                            }}
                          >
                            <span
                              style={{
                                width: "6px",
                                height: "6px",
                                borderRadius: "50%",
                                backgroundColor: (selectedOrgDetail.status || "").toUpperCase() === "ACTIVE" ? "#16A34A" : "#D97706",
                              }}
                            />
                            {(selectedOrgDetail.status || "ACTIVE").toUpperCase()}
                          </span>
                          <span
                            style={{
                              padding: "4px 12px",
                              borderRadius: "20px",
                              fontSize: "11px",
                              fontWeight: 700,
                              background: "rgba(255, 255, 255, 0.22)",
                              color: "#FFFFFF",
                              backdropFilter: "blur(6px)",
                              border: "1px solid rgba(255, 255, 255, 0.3)",
                            }}
                          >
                            {(selectedOrgDetail.subscription_plan || "FREE").toUpperCase()} PLAN
                          </span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "16px", fontSize: "13px", color: "rgba(255, 255, 255, 0.9)", flexWrap: "wrap" }}>
                          <span>
                            Tenant ID: <code style={{ color: "#FFFFFF", fontWeight: 700, background: "rgba(0,0,0,0.25)", padding: "2px 8px", borderRadius: "6px", fontFamily: "monospace" }}>{selectedOrgDetail.id}</code>
                          </span>
                          <span>•</span>
                          <span>
                            Super Admin: <strong style={{ color: "#FFFFFF" }}>{selectedOrgDetail.super_admin_name || "—"}</strong>
                          </span>
                          <span>•</span>
                          <span>
                            Contact: <strong style={{ color: "#FFFFFF" }}>{selectedOrgDetail.contact_email || selectedOrgDetail.super_admin_email || "—"}</strong>
                          </span>
                        </div>
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: "10px" }}>
                      <button
                        type="button"
                        onClick={() => handleOpenEdit(selectedOrgDetail)}
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "8px",
                          padding: "10px 18px",
                          borderRadius: "12px",
                          border: "1px solid rgba(255, 255, 255, 0.4)",
                          background: "rgba(255, 255, 255, 0.2)",
                          color: "#FFFFFF",
                          fontSize: "13px",
                          fontWeight: 700,
                          cursor: "pointer",
                          backdropFilter: "blur(8px)",
                          transition: "all 0.15s ease",
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255, 255, 255, 0.35)")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(255, 255, 255, 0.2)")}
                      >
                        <Pencil size={15} />
                        Edit Tenant
                      </button>
                    </div>
                  </div>
                </div>

                {/* 5 Professional Metric Cards */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: "18px",
                    marginBottom: "28px",
                  }}
                >
                  {/* Card 1: Technicians */}
                  <div
                    style={{
                      background: "#FFFFFF",
                      borderRadius: "16px",
                      border: "1px solid #E2E8F0",
                      padding: "20px 22px",
                      boxShadow: "0 4px 16px -2px rgba(16, 185, 129, 0.08)",
                      display: "flex",
                      alignItems: "center",
                      gap: "16px",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "4px",
                        height: "100%",
                        background: "linear-gradient(180deg, #10B981 0%, #059669 100%)",
                      }}
                    />
                    <div
                      style={{
                        width: "48px",
                        height: "48px",
                        borderRadius: "12px",
                        background: "#ECFDF5",
                        color: "#059669",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <Users size={24} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.06em", marginBottom: "4px" }}>
                        Technicians
                      </div>
                      <div style={{ fontSize: "24px", fontWeight: 800, color: "#0F172A", lineHeight: 1.1, marginBottom: "4px" }}>
                        {selectedOrgDetail.technician_count ?? (selectedOrgDetail as any).technicians_count ?? (selectedOrgDetail as any).stats?.technician_count ?? 1}
                      </div>
                      <div style={{ fontSize: "12px", color: "#059669", fontWeight: 600 }}>
                        Active field force
                      </div>
                    </div>
                  </div>

                  {/* Card 2: Total Jobs Assigned */}
                  <div
                    style={{
                      background: "#FFFFFF",
                      borderRadius: "16px",
                      border: "1px solid #E2E8F0",
                      padding: "20px 22px",
                      boxShadow: "0 4px 16px -2px rgba(37, 99, 235, 0.08)",
                      display: "flex",
                      alignItems: "center",
                      gap: "16px",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "4px",
                        height: "100%",
                        background: "linear-gradient(180deg, #3B82F6 0%, #2563EB 100%)",
                      }}
                    />
                    <div
                      style={{
                        width: "48px",
                        height: "48px",
                        borderRadius: "12px",
                        background: "#EFF6FF",
                        color: "#2563EB",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <Briefcase size={24} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.06em", marginBottom: "4px" }}>
                        Total Jobs Assigned
                      </div>
                      <div style={{ fontSize: "24px", fontWeight: 800, color: "#0F172A", lineHeight: 1.1, marginBottom: "4px" }}>
                        {selectedOrgDetail.job_count}
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748B" }}>
                        {selectedOrgDetail.active_job_count ?? "—"} active • {selectedOrgDetail.completed_job_count ?? "—"} completed • {selectedOrgDetail.cancelled_job_count ?? "—"} cancelled
                      </div>
                    </div>
                  </div>

                  {/* Card 3: Payment / Revenue */}
                  <div
                    style={{
                      background: "#FFFFFF",
                      borderRadius: "16px",
                      border: "1px solid #E2E8F0",
                      padding: "20px 22px",
                      boxShadow: "0 4px 16px -2px rgba(16, 185, 129, 0.08)",
                      display: "flex",
                      alignItems: "center",
                      gap: "16px",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "4px",
                        height: "100%",
                        background: "linear-gradient(180deg, #10B981 0%, #059669 100%)",
                      }}
                    />
                    <div
                      style={{
                        width: "48px",
                        height: "48px",
                        borderRadius: "12px",
                        background: "#ECFDF5",
                        color: "#059669",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <CreditCard size={24} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.06em", marginBottom: "4px" }}>
                        Recorded Closure Value
                      </div>
                      <div style={{ fontSize: "24px", fontWeight: 800, color: "#059669", lineHeight: 1.1, marginBottom: "4px" }}>
                        {formattedOrgPayment}
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748B" }}>
                        {selectedOrgDetail.completed_job_count ?? 0} completed jobs
                      </div>
                    </div>
                  </div>

                  {/* Card 4: Subscription Plan */}
                  <div
                    style={{
                      background: "#FFFFFF",
                      borderRadius: "16px",
                      border: "1px solid #E2E8F0",
                      padding: "20px 22px",
                      boxShadow: "0 4px 16px -2px rgba(124, 58, 237, 0.08)",
                      display: "flex",
                      alignItems: "center",
                      gap: "16px",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "4px",
                        height: "100%",
                        background: "linear-gradient(180deg, #8B5CF6 0%, #7C3AED 100%)",
                      }}
                    />
                    <div
                      style={{
                        width: "48px",
                        height: "48px",
                        borderRadius: "12px",
                        background: "#F5F3FF",
                        color: "#7C3AED",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <CreditCard size={24} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.06em", marginBottom: "4px" }}>
                        Subscription Plan
                      </div>
                      <div style={{ fontSize: "22px", fontWeight: 800, color: "#0F172A", lineHeight: 1.1, marginBottom: "4px" }}>
                        {(selectedOrgDetail.subscription_plan || "FREE").toUpperCase()}
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748B" }}>
                        Max Users: {selectedOrgDetail.max_users || 50} • Techs: {selectedOrgDetail.max_technicians || 50}
                      </div>
                    </div>
                  </div>

                  {/* Card 5: Super Admin */}
                  <div
                    style={{
                      background: "#FFFFFF",
                      borderRadius: "16px",
                      border: "1px solid #E2E8F0",
                      padding: "20px 22px",
                      boxShadow: "0 4px 16px -2px rgba(217, 119, 6, 0.08)",
                      display: "flex",
                      alignItems: "center",
                      gap: "16px",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "4px",
                        height: "100%",
                        background: "linear-gradient(180deg, #F59E0B 0%, #D97706 100%)",
                      }}
                    />
                    <div
                      style={{
                        width: "48px",
                        height: "48px",
                        borderRadius: "12px",
                        background: "#FFFBEB",
                        color: "#D97706",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <ShieldCheck size={24} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#64748B", letterSpacing: "0.06em", marginBottom: "4px" }}>
                        Super Admin
                      </div>
                      <div style={{ fontSize: "18px", fontWeight: 800, color: "#0F172A", lineHeight: 1.2, marginBottom: "4px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {selectedOrgDetail.super_admin_name || "—"}
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748B", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {selectedOrgDetail.contact_email || selectedOrgDetail.super_admin_email || "—"}
                      </div>
                    </div>
                  </div>
                </div>

                {/* ──────────────────────────────────────────────────
                    SERVICES PROVIDED SECTION
                   ────────────────────────────────────────────────── */}
                <div
                  style={{
                    background: "#FFFFFF",
                    borderRadius: "16px",
                    border: "1px solid #E2E8F0",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                    padding: "24px",
                    marginBottom: "24px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px", flexWrap: "wrap", gap: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <div
                        style={{
                          width: "36px",
                          height: "36px",
                          borderRadius: "10px",
                          background: "#ECFDF5",
                          color: "#059669",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <Layers size={20} />
                      </div>
                      <div>
                        <h2 style={{ fontSize: "16px", fontWeight: 800, color: "#0F172A", margin: "0 0 2px 0" }}>
                          Services Provided
                        </h2>
                        <p style={{ fontSize: "12px", color: "#64748B", margin: 0 }}>
                          Specialized field service offerings and maintenance capabilities supported by {selectedOrgDetail.name}
                        </p>
                      </div>
                    </div>
                    <span
                      style={{
                        fontSize: "12px",
                        fontWeight: 700,
                        padding: "4px 12px",
                        borderRadius: "20px",
                        background: "#ECFDF5",
                        color: "#059669",
                        border: "1px solid #A7F3D0",
                      }}
                    >
                      {displayedServices.length} Observed Services
                    </span>
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                      gap: "14px",
                    }}
                  >
                    {displayedServices.map((service, idx) => {
                      const serviceJobsCount = orgJobs.filter(
                        (j) => j.service_type && j.service_type.toLowerCase() === service.toLowerCase()
                      ).length;
                      return (
                        <div
                          key={idx}
                          style={{
                            borderRadius: "12px",
                            border: "1px solid #E2E8F0",
                            background: "#FAFDFB",
                            padding: "16px",
                            display: "flex",
                            alignItems: "flex-start",
                            gap: "12px",
                            transition: "all 0.15s ease",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.borderColor = "#10B981";
                            e.currentTarget.style.boxShadow = "0 4px 12px rgba(16, 185, 129, 0.1)";
                            e.currentTarget.style.transform = "translateY(-1px)";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.borderColor = "#E2E8F0";
                            e.currentTarget.style.boxShadow = "none";
                            e.currentTarget.style.transform = "translateY(0)";
                          }}
                        >
                          <div
                            style={{
                              width: "40px",
                              height: "40px",
                              borderRadius: "10px",
                              background: "#FFFFFF",
                              border: "1px solid #E2E8F0",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              flexShrink: 0,
                              boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                            }}
                          >
                            {getServiceCategoryIcon(service)}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: "14px", fontWeight: 700, color: "#0F172A", marginBottom: "4px" }}>
                              {service}
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                              <span
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                  fontSize: "11px",
                                  fontWeight: 600,
                                  color: "#059669",
                                  background: "#ECFDF5",
                                  padding: "2px 8px",
                                  borderRadius: "6px",
                                }}
                              >
                                <CheckCircle2 size={11} />
                                Observed in job history
                              </span>
                              {serviceJobsCount > 0 && (
                                <span style={{ fontSize: "11px", color: "#64748B", fontWeight: 500 }}>
                                  {serviceJobsCount} {serviceJobsCount === 1 ? "job" : "jobs"}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                    {!displayedServices.length && <div style={{ padding: "16px", color: "#64748B", fontSize: "13px" }}>No service types are recorded for this organization yet.</div>}
                  </div>
                </div>

                {/* ──────────────────────────────────────────────────
                    ORGANIZATION JOBS LIST SECTION
                   ────────────────────────────────────────────────── */}
                <div
                  style={{
                    background: "#FFFFFF",
                    borderRadius: "16px",
                    border: "1px solid #E2E8F0",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      padding: "20px 24px",
                      borderBottom: "1px solid #F1F5F9",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: "12px",
                      background: "#FAFDFB",
                    }}
                  >
                    <div>
                      <h2 style={{ fontSize: "16px", fontWeight: 800, color: "#0F172A", margin: "0 0 2px 0" }}>
                        Recent Organization Jobs ({orgJobs.length} of {selectedOrgDetail.job_count})
                      </h2>
                      <p style={{ fontSize: "12px", color: "#64748B", margin: 0 }}>
                        Latest 200 job records for {selectedOrgDetail.name}; full job totals are shown in the organization metrics above.
                      </p>
                    </div>

                    {/* Filters */}
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                      {/* Search */}
                      <div style={{ position: "relative" }}>
                        <Search
                          size={14}
                          style={{
                            position: "absolute",
                            left: "10px",
                            top: "9px",
                            color: "#94A3B8",
                          }}
                        />
                        <input
                          type="text"
                          placeholder="Search jobs..."
                          value={orgJobsSearch}
                          onChange={(e) => setOrgJobsSearch(e.target.value)}
                          style={{
                            padding: "6px 12px 6px 30px",
                            fontSize: "12px",
                            borderRadius: "8px",
                            border: "1px solid #CBD5E1",
                            background: "#FFFFFF",
                            outline: "none",
                            width: "180px",
                          }}
                        />
                      </div>

                      {/* Status Filter */}
                      <select
                        value={orgJobsStatusFilter}
                        onChange={(e) => setOrgJobsStatusFilter(e.target.value)}
                        style={{
                          padding: "6px 10px",
                          fontSize: "12px",
                          borderRadius: "8px",
                          border: "1px solid #CBD5E1",
                          background: "#FFFFFF",
                          color: "#334155",
                          outline: "none",
                          cursor: "pointer",
                          fontWeight: 600,
                        }}
                      >
                        <option value="ALL">All Statuses ({orgJobs.length})</option>
                        <option value="ACTIVE">Active</option>
                        <option value="COMPLETED">Completed</option>
                        <option value="CANCELLED">Cancelled</option>
                      </select>
                    </div>
                  </div>

                  {/* Jobs Table or Empty/Loading */}
                  {orgJobsLoading && orgJobs.length === 0 ? (
                    <div style={{ padding: "48px 24px", textAlign: "center", color: "#10B981", fontSize: "14px", fontWeight: 500 }}>
                      Loading organization jobs...
                    </div>
                  ) : filteredOrgJobs.length > 0 ? (
                    <div style={{ overflowX: "auto" }}>
                      <table
                        style={{
                          width: "100%",
                          borderCollapse: "collapse",
                          textAlign: "left",
                          fontSize: "13px",
                        }}
                      >
                        <thead>
                          <tr
                            style={{
                              background: "#F8FAFC",
                              borderBottom: "1px solid #E2E8F0",
                              color: "#475569",
                              fontWeight: 600,
                              textTransform: "uppercase",
                              fontSize: "11px",
                              letterSpacing: "0.05em",
                            }}
                          >
                            <th style={{ padding: "12px 18px" }}>Job #</th>
                            <th style={{ padding: "12px 18px" }}>Customer Name</th>
                            <th style={{ padding: "12px 18px" }}>Contact</th>
                            <th style={{ padding: "12px 18px" }}>Service Type</th>
                            <th style={{ padding: "12px 18px" }}>Technician</th>
                            <th style={{ padding: "12px 18px" }}>Priority</th>
                            <th style={{ padding: "12px 18px" }}>Status</th>
                            <th style={{ padding: "12px 18px" }}>Closure Subtotal / Payment Status</th>
                            <th style={{ padding: "12px 18px" }}>Date</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredOrgJobs.map((job) => {
                            const s = job.status.toUpperCase();
                            const isCompleted = s === "COMPLETED" || s === "CLOSED";
                            const isCancelled = s === "CANCELLED" || s === "CANCELED";

                            let statusBg = "#FEF3C7";
                            let statusColor = "#D97706";
                            if (isCompleted) {
                              statusBg = "#DCFCE7";
                              statusColor = "#16A34A";
                            } else if (isCancelled) {
                              statusBg = "#FEE2E2";
                              statusColor = "#EF4444";
                            }

                            return (
                              <tr
                                key={job.id}
                                style={{
                                  borderBottom: "1px solid #F1F5F9",
                                  transition: "background 0.1s ease",
                                }}
                                onMouseEnter={(e) => (e.currentTarget.style.background = "#F8FAFC")}
                                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                              >
                                <td style={{ padding: "14px 18px" }}>
                                  <span
                                    style={{
                                      fontWeight: 700,
                                      color: "#2563EB",
                                      fontFamily: "monospace",
                                      background: "#EFF6FF",
                                      padding: "3px 8px",
                                      borderRadius: "6px",
                                      border: "1px solid #DBEAFE",
                                      fontSize: "12px",
                                    }}
                                  >
                                    {job.id}
                                  </span>
                                </td>
                                <td style={{ padding: "14px 18px" }}>
                                  <div style={{ fontWeight: 700, color: "#0F172A" }}>
                                    {job.customer_name}
                                  </div>
                                  {job.issue_description && (
                                    <div
                                      style={{
                                        fontSize: "12px",
                                        color: "#64748B",
                                        maxWidth: "240px",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap",
                                      }}
                                    >
                                      {job.issue_description}
                                    </div>
                                  )}
                                </td>
                                <td style={{ padding: "14px 18px", color: "#475569", fontFamily: "monospace", fontSize: "12px" }}>
                                  {job.contact_number || "—"}
                                </td>
                                <td style={{ padding: "14px 18px" }}>
                                  <span
                                    style={{
                                      display: "inline-block",
                                      padding: "3px 9px",
                                      borderRadius: "6px",
                                      background: "#F1F5F9",
                                      color: "#334155",
                                      fontWeight: 600,
                                      fontSize: "12px",
                                    }}
                                  >
                                    {job.service_type || "General Service"}
                                  </span>
                                </td>
                                <td style={{ padding: "14px 18px" }}>
                                  <span
                                    style={{
                                      color: job.technician_name === "Unassigned" ? "#94A3B8" : "#0F172A",
                                      fontWeight: job.technician_name === "Unassigned" ? 500 : 600,
                                    }}
                                  >
                                    {job.technician_name}
                                  </span>
                                </td>
                                <td style={{ padding: "14px 18px" }}>
                                  <span
                                    style={{
                                      fontSize: "11px",
                                      fontWeight: 700,
                                      padding: "2px 7px",
                                      borderRadius: "4px",
                                      background:
                                        job.priority === "HIGH" || job.priority === "CRITICAL"
                                          ? "#FEE2E2"
                                          : job.priority === "MEDIUM"
                                          ? "#FEF3C7"
                                          : "#F1F5F9",
                                      color:
                                        job.priority === "HIGH" || job.priority === "CRITICAL"
                                          ? "#B91C1C"
                                          : job.priority === "MEDIUM"
                                          ? "#B45309"
                                          : "#475569",
                                    }}
                                  >
                                    {job.priority}
                                  </span>
                                </td>
                                <td style={{ padding: "14px 18px" }}>
                                  <span
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: "5px",
                                      padding: "3px 10px",
                                      borderRadius: "12px",
                                      fontSize: "11px",
                                      fontWeight: 700,
                                      background: statusBg,
                                      color: statusColor,
                                    }}
                                  >
                                    <span
                                      style={{
                                        width: "6px",
                                        height: "6px",
                                        borderRadius: "50%",
                                        backgroundColor: statusColor,
                                      }}
                                    />
                                    {job.status.replace("_", " ")}
                                  </span>
                                </td>
                                <td style={{ padding: "14px 18px" }}>
                                  <strong style={{ display: "block", color: job.payment_amount != null ? "#0F766E" : "#94A3B8", fontFamily: "monospace", fontSize: "12px" }}>{job.formatted_payment && job.formatted_payment !== "—" ? job.formatted_payment : "—"}</strong>
                                  <small style={{ color: job.payment_status === "SUCCESSFUL" ? "#15803D" : job.payment_status === "PENDING" ? "#B45309" : "#64748B" }}>{job.payment_status || "UNAVAILABLE"}</small>
                                </td>
                                <td style={{ padding: "14px 18px", color: "#64748B", fontSize: "12px" }}>
                                  {job.preferred_service_date || (job.created_at ? new Date(job.created_at).toLocaleDateString() : "—")}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div style={{ padding: "48px 24px", textAlign: "center", color: "#64748B" }}>
                      <Briefcase size={32} style={{ color: "#CBD5E1", margin: "0 auto 8px" }} />
                      <div style={{ fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                        No jobs found for this organization
                      </div>
                      <div style={{ fontSize: "13px" }}>
                        {orgJobsSearch ? "Try adjusting your search criteria." : "No jobs have been assigned to this partner organization yet."}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })()}

          {/* ──────────────────────────────────────────────────
              ORGANIZATION LIST TABLE VIEW
              Rendered when user clicks "Organization" in sidebar (selectedOrgDetail is null)
             ────────────────────────────────────────────────── */}
          {activeTab === "organizations" && !selectedOrgDetail && (
            <div style={{ maxWidth: "1280px", margin: "0 auto" }}>
              {/* Header */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "20px",
                  flexWrap: "wrap",
                  gap: "12px",
                }}
              >
                <div>
                  <h1
                    style={{
                      fontSize: "22px",
                      fontWeight: 800,
                      color: "#0F172A",
                      margin: "0 0 4px 0",
                    }}
                  >
                    Platform Organizations
                  </h1>
                  <p style={{ fontSize: "13px", color: "#64748B", margin: 0 }}>
                    Overview and status of all partner organizations registered across the platform.
                  </p>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  {/* Search Organizations */}
                  <div style={{ position: "relative" }}>
                    <Search
                      size={15}
                      style={{
                        position: "absolute",
                        left: "10px",
                        top: "10px",
                        color: "#94A3B8",
                      }}
                    />
                    <input
                      type="text"
                      placeholder="Search organizations..."
                      value={orgSearch}
                      onChange={(e) => setOrgSearch(e.target.value)}
                      style={{
                        padding: "8px 12px 8px 32px",
                        fontSize: "13px",
                        borderRadius: "8px",
                        border: "1px solid #CBD5E1",
                        background: "#FFFFFF",
                        outline: "none",
                        width: "220px",
                      }}
                    />
                  </div>

                  {/* Refresh Button */}
                  <button
                    type="button"
                    onClick={() => {
                      setOrgSearch("");
                      void loadOrganizations();
                    }}
                    disabled={orgsLoading}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      padding: "8px 14px",
                      borderRadius: "8px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#334155",
                      fontSize: "13px",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    <RefreshCw size={15} className={orgsLoading ? "is-spinning" : ""} />
                    Refresh
                  </button>
                </div>
              </div>

              {/* Table or Empty/Loading State */}
              <div
                style={{
                  background: "#FFFFFF",
                  borderRadius: "14px",
                  border: "1px solid #E2E8F0",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                  overflow: "hidden",
                }}
              >
                {orgsLoading && orgs.length === 0 ? (
                  <div
                    style={{
                      padding: "48px 24px",
                      textAlign: "center",
                      color: "#10B981",
                      fontSize: "14px",
                      fontWeight: 500,
                    }}
                  >
                    Loading organizations...
                  </div>
                ) : orgsError && orgs.length === 0 ? (
                  <div
                    style={{
                      padding: "36px 24px",
                      textAlign: "center",
                      color: "#EF4444",
                    }}
                  >
                    <AlertCircle size={28} style={{ margin: "0 auto 8px" }} />
                    <div style={{ fontWeight: 600, marginBottom: "8px" }}>{orgsError}</div>
                    <button
                      type="button"
                      onClick={() => void loadOrganizations()}
                      style={{
                        padding: "6px 14px",
                        borderRadius: "6px",
                        border: "1px solid #EF4444",
                        background: "#FEF2F2",
                        color: "#B91C1C",
                        cursor: "pointer",
                        fontWeight: 600,
                        fontSize: "13px",
                      }}
                    >
                      Try Again
                    </button>
                  </div>
                ) : filteredOrgs.length > 0 ? (
                  <div style={{ overflowX: "auto" }}>
                    <table
                      style={{
                        width: "100%",
                        borderCollapse: "collapse",
                        textAlign: "left",
                        fontSize: "13px",
                      }}
                    >
                      <thead>
                        <tr
                          style={{
                            background: "#F8FAFC",
                            borderBottom: "1px solid #E2E8F0",
                            color: "#475569",
                            fontWeight: 600,
                            textTransform: "uppercase",
                            fontSize: "11px",
                            letterSpacing: "0.05em",
                          }}
                        >
                          <th style={{ padding: "14px 18px" }}>Organization</th>
                          <th style={{ padding: "14px 18px" }}>Super Admin</th>
                          <th style={{ padding: "14px 18px" }}>Tenant ID</th>
                          <th style={{ padding: "14px 18px" }}>Status</th>
                          <th style={{ padding: "14px 18px", textAlign: "center" }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredOrgs.map((org) => {
                          const isOrgActive = (org.status || "").toUpperCase() === "ACTIVE";
                          return (
                            <tr
                              key={org.id}
                              style={{
                                borderBottom: "1px solid #F1F5F9",
                                transition: "background 0.15s ease",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "#F8FAFC";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "transparent";
                              }}
                            >
                              {/* 1. Organization Name & Slug */}
                              <td
                                style={{ padding: "14px 18px", cursor: "pointer" }}
                                onClick={() => setSelectedOrgDetail(org)}
                                title="Click to view organization details"
                              >
                                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                                  <div
                                    style={{
                                      width: "32px",
                                      height: "32px",
                                      borderRadius: "8px",
                                      background: "#ECFDF5",
                                      color: "#059669",
                                      display: "flex",
                                      alignItems: "center",
                                      justifyContent: "center",
                                      fontWeight: 700,
                                      fontSize: "13px",
                                      flexShrink: 0,
                                    }}
                                  >
                                    {(org.name || "O").charAt(0).toUpperCase()}
                                  </div>
                                  <div>
                                    <strong style={{ color: "#0F172A", display: "block" }}>
                                      {org.name || "Unnamed Organization"}
                                    </strong>
                                    <small style={{ color: "#94A3B8" }}>{org.slug || "—"}</small>
                                  </div>
                                </div>
                              </td>

                              {/* 2. Super Admin */}
                              <td style={{ padding: "14px 18px" }}>
                                <div>
                                  <strong style={{ color: "#0F172A", display: "block" }}>
                                    {org.super_admin_name || "—"}
                                  </strong>
                                  <small style={{ color: "#059669", fontWeight: 600 }}>
                                    {org.super_admin_email || "—"}
                                  </small>
                                </div>
                              </td>

                              {/* 3. Tenant ID */}
                              <td style={{ padding: "14px 18px" }}>
                                <span
                                  style={{
                                    fontFamily: "monospace",
                                    fontSize: "12px",
                                    padding: "2px 6px",
                                    background: "#F1F5F9",
                                    borderRadius: "4px",
                                    color: "#334155",
                                    fontWeight: 600,
                                  }}
                                >
                                  {org.id}
                                </span>
                              </td>

                              {/* 4. Status */}
                              <td style={{ padding: "14px 18px" }}>
                                {(() => {
                                  const rawStatus = (org.status || "ACTIVE").toUpperCase();
                                  const isActive = rawStatus === "ACTIVE";
                                  const isInactive = rawStatus === "INACTIVE";
                                  const bg = isActive ? "#DCFCE7" : isInactive ? "#FEF3C7" : "#FEE2E2";
                                  const fg = isActive ? "#166534" : isInactive ? "#92400E" : "#991B1B";
                                  const dot = isActive ? "#16A34A" : isInactive ? "#D97706" : "#EF4444";
                                  return (
                                    <span
                                      style={{
                                        display: "inline-flex",
                                        alignItems: "center",
                                        gap: "4px",
                                        fontSize: "11px",
                                        fontWeight: 700,
                                        padding: "2px 8px",
                                        borderRadius: "12px",
                                        background: bg,
                                        color: fg,
                                      }}
                                    >
                                      <span
                                        style={{
                                          width: "6px",
                                          height: "6px",
                                          borderRadius: "50%",
                                          background: dot,
                                        }}
                                      />
                                      {rawStatus}
                                    </span>
                                  );
                                })()}
                              </td>

                              {/* 5. Actions: 3 Icons (View, Edit, Delete) */}
                              <td style={{ padding: "14px 18px", textAlign: "center" }}>
                                <div style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
                                  {/* View Details Icon */}
                                  <button
                                    type="button"
                                    onClick={() => setSelectedOrgDetail(org)}
                                    title="View Organization Details"
                                    style={{
                                      width: "32px",
                                      height: "32px",
                                      borderRadius: "8px",
                                      border: "1px solid #E2E8F0",
                                      background: "#F8FAFC",
                                      color: "#0284C7",
                                      display: "flex",
                                      alignItems: "center",
                                      justifyContent: "center",
                                      cursor: "pointer",
                                      transition: "all 0.15s ease",
                                    }}
                                    onMouseEnter={(e) => {
                                      e.currentTarget.style.background = "#E0F2FE";
                                      e.currentTarget.style.borderColor = "#7DD3FC";
                                      e.currentTarget.style.transform = "translateY(-1px)";
                                    }}
                                    onMouseLeave={(e) => {
                                      e.currentTarget.style.background = "#F8FAFC";
                                      e.currentTarget.style.borderColor = "#E2E8F0";
                                      e.currentTarget.style.transform = "translateY(0)";
                                    }}
                                  >
                                    <Eye size={16} />
                                  </button>

                                  {/* Edit Icon */}
                                  <button
                                    type="button"
                                    onClick={() => handleOpenEdit(org)}
                                    title="Edit Organization"
                                    style={{
                                      width: "32px",
                                      height: "32px",
                                      borderRadius: "8px",
                                      border: "1px solid #E2E8F0",
                                      background: "#F8FAFC",
                                      color: "#D97706",
                                      display: "flex",
                                      alignItems: "center",
                                      justifyContent: "center",
                                      cursor: "pointer",
                                      transition: "all 0.15s ease",
                                    }}
                                    onMouseEnter={(e) => {
                                      e.currentTarget.style.background = "#FEF3C7";
                                      e.currentTarget.style.borderColor = "#FCD34D";
                                      e.currentTarget.style.transform = "translateY(-1px)";
                                    }}
                                    onMouseLeave={(e) => {
                                      e.currentTarget.style.background = "#F8FAFC";
                                      e.currentTarget.style.borderColor = "#E2E8F0";
                                      e.currentTarget.style.transform = "translateY(0)";
                                    }}
                                  >
                                    <Pencil size={15} />
                                  </button>

                                  {/* Deactivate / Activate Icon */}
                                  {(() => {
                                    const isCurActive = (org.status || "").toUpperCase() === "ACTIVE";
                                    return (
                                      <button
                                        type="button"
                                        onClick={() => setDeletingOrg(org)}
                                        title={isCurActive ? "Deactivate Organization" : "Activate Organization"}
                                        style={{
                                          width: "32px",
                                          height: "32px",
                                          borderRadius: "8px",
                                          border: "1px solid #E2E8F0",
                                          background: "#F8FAFC",
                                          color: isCurActive ? "#D97706" : "#16A34A",
                                          display: "flex",
                                          alignItems: "center",
                                          justifyContent: "center",
                                          cursor: "pointer",
                                          transition: "all 0.15s ease",
                                        }}
                                        onMouseEnter={(e) => {
                                          e.currentTarget.style.background = isCurActive ? "#FEF3C7" : "#DCFCE7";
                                          e.currentTarget.style.borderColor = isCurActive ? "#FCD34D" : "#86EFAC";
                                          e.currentTarget.style.transform = "translateY(-1px)";
                                        }}
                                        onMouseLeave={(e) => {
                                          e.currentTarget.style.background = "#F8FAFC";
                                          e.currentTarget.style.borderColor = "#E2E8F0";
                                          e.currentTarget.style.transform = "translateY(0)";
                                        }}
                                      >
                                        {isCurActive ? <PowerOff size={15} /> : <CheckCircle2 size={15} />}
                                      </button>
                                    );
                                  })()}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div
                    style={{
                      padding: "48px 24px",
                      textAlign: "center",
                      color: "#64748B",
                    }}
                  >
                    <Building2 size={32} style={{ color: "#CBD5E1", margin: "0 auto 8px" }} />
                    <div style={{ fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                      No organizations found
                    </div>
                    <div style={{ fontSize: "13px" }}>
                      {orgSearch ? "Try adjusting your search criteria." : "No partner organizations are registered yet."}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ──────────────────────────────────────────────────
              MODAL: EDIT ORGANIZATION
             ────────────────────────────────────────────────── */}
          {editingOrg && (
            <div
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 9999,
                backgroundColor: "rgba(15, 23, 42, 0.6)",
                backdropFilter: "blur(4px)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: "16px",
                boxSizing: "border-box",
              }}
              onClick={() => setEditingOrg(null)}
            >
              <div
                style={{
                  background: "#FFFFFF",
                  borderRadius: "18px",
                  width: "100%",
                  maxWidth: "480px",
                  boxShadow: "0 20px 40px -8px rgba(0, 0, 0, 0.2)",
                  border: "1px solid #E2E8F0",
                  overflow: "hidden",
                }}
                onClick={(e) => e.stopPropagation()}
              >
                <div
                  style={{
                    padding: "18px 24px",
                    borderBottom: "1px solid #F1F5F9",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                  }}
                >
                  <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#0F172A" }}>
                    Edit Organization: {editingOrg.name}
                  </h3>
                  <button
                    type="button"
                    onClick={() => setEditingOrg(null)}
                    style={{
                      border: "none",
                      background: "transparent",
                      color: "#94A3B8",
                      cursor: "pointer",
                    }}
                  >
                    <X size={18} />
                  </button>
                </div>

                <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: "14px" }}>
                  {actionMessage && (
                    <div
                      style={{
                        padding: "10px 14px",
                        borderRadius: "8px",
                        fontSize: "12px",
                        fontWeight: 600,
                        background: actionMessage.type === "success" ? "#DCFCE7" : "#FEE2E2",
                        color: actionMessage.type === "success" ? "#166534" : "#991B1B",
                        border: `1px solid ${actionMessage.type === "success" ? "#86EFAC" : "#FCA5A5"}`,
                      }}
                    >
                      {actionMessage.text}
                    </div>
                  )}

                  <div>
                    <label style={{ display: "block", fontSize: "12px", fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
                      Organization Name
                    </label>
                    <input
                      type="text"
                      value={editForm.name}
                      onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                      style={{
                        width: "100%",
                        padding: "9px 12px",
                        borderRadius: "8px",
                        border: "1px solid #CBD5E1",
                        fontSize: "13px",
                        outline: "none",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>

                  <div>
                    <label style={{ display: "block", fontSize: "12px", fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
                      Subscription Plan
                    </label>
                    <select
                      value={editForm.subscription_plan}
                      onChange={(e) => setEditForm({ ...editForm, subscription_plan: e.target.value })}
                      style={{
                        width: "100%",
                        padding: "9px 12px",
                        borderRadius: "8px",
                        border: "1px solid #CBD5E1",
                        fontSize: "13px",
                        outline: "none",
                        background: "#FFFFFF",
                        boxSizing: "border-box",
                      }}
                    >
                      <option value="FREE">FREE</option>
                      <option value="PRO">PRO</option>
                      <option value="ENTERPRISE">ENTERPRISE</option>
                    </select>
                  </div>

                  <div>
                    <label style={{ display: "block", fontSize: "12px", fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
                      Contact Email
                    </label>
                    <input
                      type="email"
                      value={editForm.contact_email}
                      onChange={(e) => setEditForm({ ...editForm, contact_email: e.target.value })}
                      style={{
                        width: "100%",
                        padding: "9px 12px",
                        borderRadius: "8px",
                        border: "1px solid #CBD5E1",
                        fontSize: "13px",
                        outline: "none",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>

                  <div>
                    <label style={{ display: "block", fontSize: "12px", fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
                      Contact Phone
                    </label>
                    <input
                      type="text"
                      value={editForm.contact_phone}
                      onChange={(e) => setEditForm({ ...editForm, contact_phone: e.target.value })}
                      style={{
                        width: "100%",
                        padding: "9px 12px",
                        borderRadius: "8px",
                        border: "1px solid #CBD5E1",
                        fontSize: "13px",
                        outline: "none",
                        boxSizing: "border-box",
                      }}
                    />
                  </div>
                </div>

                <div
                  style={{
                    padding: "14px 24px",
                    background: "#F8FAFC",
                    borderTop: "1px solid #E2E8F0",
                    display: "flex",
                    justifyContent: "flex-end",
                    gap: "10px",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => setEditingOrg(null)}
                    disabled={actionLoading}
                    style={{
                      padding: "8px 16px",
                      borderRadius: "8px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#334155",
                      fontSize: "13px",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleSaveEdit()}
                    disabled={actionLoading}
                    style={{
                      padding: "8px 18px",
                      borderRadius: "8px",
                      border: "none",
                      background: "#00875A",
                      color: "#FFFFFF",
                      fontSize: "13px",
                      fontWeight: 700,
                      cursor: "pointer",
                    }}
                  >
                    {actionLoading ? "Saving..." : "Save Changes"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ──────────────────────────────────────────────────
              MODAL: DELETE ORGANIZATION CONFIRMATION
             ────────────────────────────────────────────────── */}
          {deletingOrg && (
            <div
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 9999,
                backgroundColor: "rgba(15, 23, 42, 0.6)",
                backdropFilter: "blur(4px)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: "16px",
                boxSizing: "border-box",
              }}
              onClick={() => setDeletingOrg(null)}
            >
              <div
                style={{
                  background: "#FFFFFF",
                  borderRadius: "18px",
                  width: "100%",
                  maxWidth: "440px",
                  boxShadow: "0 20px 40px -8px rgba(0, 0, 0, 0.2)",
                  border: "1px solid #E2E8F0",
                  padding: "24px",
                }}
                onClick={(e) => e.stopPropagation()}
              >
                {(() => {
                  const isCurrentlyActive = (deletingOrg.status || "").toUpperCase() === "ACTIVE";
                  return (
                    <>
                      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "14px" }}>
                        <div
                          style={{
                            width: "42px",
                            height: "42px",
                            borderRadius: "10px",
                            background: isCurrentlyActive ? "#FEF3C7" : "#DCFCE7",
                            color: isCurrentlyActive ? "#D97706" : "#16A34A",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                          }}
                        >
                          {isCurrentlyActive ? <PowerOff size={22} /> : <CheckCircle2 size={22} />}
                        </div>
                        <div>
                          <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#0F172A" }}>
                            {isCurrentlyActive ? "Deactivate Organization" : "Activate Organization"}
                          </h3>
                          <span style={{ fontSize: "12px", color: "#64748B" }}>
                            {isCurrentlyActive ? "Mark status as INACTIVE" : "Reactivate organization access"}
                          </span>
                        </div>
                      </div>

                      {actionMessage && (
                        <div
                          style={{
                            padding: "10px 14px",
                            borderRadius: "8px",
                            fontSize: "12px",
                            fontWeight: 600,
                            marginBottom: "12px",
                            background: actionMessage.type === "success" ? "#DCFCE7" : "#FEE2E2",
                            color: actionMessage.type === "success" ? "#166534" : "#991B1B",
                            border: `1px solid ${actionMessage.type === "success" ? "#86EFAC" : "#FCA5A5"}`,
                          }}
                        >
                          {actionMessage.text}
                        </div>
                      )}

                      <p style={{ fontSize: "13px", color: "#475569", lineHeight: "1.5", margin: "0 0 20px 0" }}>
                        Are you sure you want to {isCurrentlyActive ? "deactivate" : "activate"}{" "}
                        <strong style={{ color: "#0F172A" }}>{deletingOrg.name}</strong> (Tenant ID:{" "}
                        <code style={{ color: "#059669" }}>{deletingOrg.id}</code>)?
                        {isCurrentlyActive
                          ? " The organization will remain in your records, with its status updated to INACTIVE."
                          : " The organization status will be restored to ACTIVE."}
                      </p>

                      <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                        <button
                          type="button"
                          onClick={() => setDeletingOrg(null)}
                          disabled={actionLoading}
                          style={{
                            padding: "8px 16px",
                            borderRadius: "8px",
                            border: "1px solid #CBD5E1",
                            background: "#FFFFFF",
                            color: "#334155",
                            fontSize: "13px",
                            fontWeight: 600,
                            cursor: "pointer",
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleConfirmDelete()}
                          disabled={actionLoading}
                          style={{
                            padding: "8px 18px",
                            borderRadius: "8px",
                            border: "none",
                            background: isCurrentlyActive ? "#D97706" : "#059669",
                            color: "#FFFFFF",
                            fontSize: "13px",
                            fontWeight: 700,
                            cursor: "pointer",
                          }}
                        >
                          {actionLoading ? "Processing..." : isCurrentlyActive ? "Confirm Deactivate" : "Confirm Activate"}
                        </button>
                      </div>
                    </>
                  );
                })()}
              </div>
            </div>
          )}

          {/* ──────────────────────────────────────────────────
              JOBS VIEW (All jobs across all organizations)
             ────────────────────────────────────────────────── */}
          {activeTab === "jobs" && (
            <div style={{ maxWidth: "1280px", margin: "0 auto" }}>
              {/* Header */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "20px",
                  flexWrap: "wrap",
                  gap: "12px",
                }}
              >
                <div>
                  <h1
                    style={{
                      fontSize: "22px",
                      fontWeight: 800,
                      color: "#0F172A",
                      margin: "0 0 4px 0",
                    }}
                  >
                    Platform Jobs ({jobsTotal})
                  </h1>
                  <p style={{ fontSize: "13px", color: "#64748B", margin: 0 }}>
                    Showing {jobsOrgFilter === "ALL" ? "all organizations" : orgs.find((org) => org.id === jobsOrgFilter)?.name || "selected organization"} for {jobsPeriod === "all" ? "all dates" : `the last ${jobsPeriod === "30d" ? "30" : "90"} days`} ({jobsTotal} matching jobs).
                  </p>
                </div>

                {/* Filters */}
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  {/* Search */}
                  <div style={{ position: "relative" }}>
                    <Search
                      size={15}
                      style={{
                        position: "absolute",
                        left: "10px",
                        top: "10px",
                        color: "#94A3B8",
                      }}
                    />
                    <input
                      type="text"
                      placeholder="Search jobs, customer, org..."
                      value={jobsSearch}
                      onChange={(e) => {
                        setJobsSearch(e.target.value);
                        setJobsPage(1);
                      }}
                      style={{
                        padding: "8px 12px 8px 32px",
                        fontSize: "13px",
                        borderRadius: "8px",
                        border: "1px solid #CBD5E1",
                        background: "#FFFFFF",
                        outline: "none",
                        width: "220px",
                      }}
                    />
                  </div>

                  {/* Organization Filter */}
                  <select
                    value={jobsOrgFilter}
                    onChange={(e) => {
                      setJobsOrgFilter(e.target.value);
                      setJobsPage(1);
                    }}
                    style={{
                      padding: "8px 12px",
                      fontSize: "13px",
                      borderRadius: "8px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#334155",
                      outline: "none",
                      cursor: "pointer",
                      fontWeight: 500,
                    }}
                  >
                    <option value="ALL">All Organizations</option>
                    {orgs.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name} ({o.id})
                      </option>
                    ))}
                  </select>

                  {/* Status Filter */}
                  <select
                    value={jobsStatusFilter}
                    onChange={(e) => {
                      setJobsStatusFilter(e.target.value);
                      setJobsPage(1);
                    }}
                    style={{
                      padding: "8px 12px",
                      fontSize: "13px",
                      borderRadius: "8px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#334155",
                      outline: "none",
                      cursor: "pointer",
                      fontWeight: 500,
                    }}
                  >
                    <option value="ALL">All Statuses</option>
                    <option value="ACTIVE">Active ({jobsActiveCount})</option>
                    <option value="COMPLETED">Completed ({jobsCompletedCount})</option>
                    <option value="CANCELLED">Cancelled ({jobsCancelledCount})</option>
                    <option value="CREATED">Created</option>
                    <option value="ASSIGNED">Assigned</option>
                    <option value="ACCEPTED">Accepted</option>
                    <option value="EN_ROUTE">En Route</option>
                    <option value="ON_SITE">On Site</option>
                    <option value="IN_PROGRESS">In Progress</option>
                    <option value="CLOSED">Closed</option>
                  </select>

                  <select aria-label="Job date range" value={jobsPeriod} onChange={(e) => { setJobsPeriod(e.target.value as "30d" | "90d" | "all"); setJobsPage(1); }} style={{ padding: "8px 12px", fontSize: "13px", borderRadius: "8px", border: "1px solid #CBD5E1", background: "#FFFFFF", color: "#334155", cursor: "pointer", fontWeight: 500 }}>
                    <option value="all">All dates</option>
                    <option value="30d">Last 30 days</option>
                    <option value="90d">Last 90 days</option>
                  </select>

                  <select aria-label="Payment status filter" value={jobsPaymentFilter} onChange={(e) => { setJobsPaymentFilter(e.target.value); setJobsPage(1); }} style={{ padding: "8px 12px", fontSize: "13px", borderRadius: "8px", border: "1px solid #CBD5E1", background: "#FFFFFF", color: "#334155", cursor: "pointer", fontWeight: 500 }}>
                    <option value="ALL">All payment states</option>
                    <option value="SUCCESSFUL">Successful</option>
                    <option value="PENDING">Pending</option>
                    <option value="FAILED">Failed</option>
                    <option value="UNAVAILABLE">Unavailable / not recorded</option>
                  </select>

                  {/* Refresh Button */}
                  <button
                    type="button"
                    onClick={() => {
                      setJobsSearch("");
                      setJobsOrgFilter("ALL");
                      setJobsStatusFilter("ALL");
                      setJobsPaymentFilter("ALL");
                      setJobsPeriod("all");
                      setJobsPage(1);
                      void loadJobs();
                    }}
                    disabled={jobsLoading}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      padding: "8px 14px",
                      borderRadius: "8px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#334155",
                      fontSize: "13px",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    <RefreshCw size={15} className={jobsLoading ? "is-spinning" : ""} />
                    Refresh
                  </button>
                </div>
              </div>

              {/* Summary Metrics Pills */}
              <div style={{ margin: "-8px 0 12px", color: "#64748B", fontSize: "12px" }}>Job status totals for the selected organization and date range; search and payment filters affect the table below.</div>
              <div
                style={{
                  display: "flex",
                  gap: "12px",
                  marginBottom: "18px",
                  flexWrap: "wrap",
                }}
              >
                <div
                  style={{
                    background: "#FFFFFF",
                    padding: "10px 16px",
                    borderRadius: "10px",
                    border: "1px solid #E2E8F0",
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    fontSize: "13px",
                  }}
                >
                  <Briefcase size={16} color="#2563EB" />
                  <span style={{ color: "#64748B" }}>Total Jobs:</span>
                  <strong style={{ color: "#0F172A", fontWeight: 700 }}>{jobsTotal}</strong>
                </div>

                <div
                  style={{
                    background: "#FFFFFF",
                    padding: "10px 16px",
                    borderRadius: "10px",
                    border: "1px solid #E2E8F0",
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    fontSize: "13px",
                  }}
                >
                  <Activity size={16} color="#D97706" />
                  <span style={{ color: "#64748B" }}>Active Jobs:</span>
                  <strong style={{ color: "#D97706", fontWeight: 700 }}>{jobsActiveCount}</strong>
                </div>

                <div
                  style={{
                    background: "#FFFFFF",
                    padding: "10px 16px",
                    borderRadius: "10px",
                    border: "1px solid #E2E8F0",
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    fontSize: "13px",
                  }}
                >
                  <CheckCircle2 size={16} color="#16A34A" />
                  <span style={{ color: "#64748B" }}>Completed:</span>
                  <strong style={{ color: "#16A34A", fontWeight: 700 }}>{jobsCompletedCount}</strong>
                </div>

                <div
                  style={{
                    background: "#FFFFFF",
                    padding: "10px 16px",
                    borderRadius: "10px",
                    border: "1px solid #E2E8F0",
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    fontSize: "13px",
                  }}
                >
                  <XCircle size={16} color="#EF4444" />
                  <span style={{ color: "#64748B" }}>Cancelled:</span>
                  <strong style={{ color: "#EF4444", fontWeight: 700 }}>{jobsCancelledCount}</strong>
                </div>
              </div>

              {/* Table or Empty/Loading State */}
              <div
                style={{
                  background: "#FFFFFF",
                  borderRadius: "14px",
                  border: "1px solid #E2E8F0",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
                  overflow: "hidden",
                }}
              >
                {jobsLoading && jobs.length === 0 ? (
                  <div
                    style={{
                      padding: "48px 24px",
                      textAlign: "center",
                      color: "#10B981",
                      fontSize: "14px",
                      fontWeight: 500,
                    }}
                  >
                    Loading platform jobs...
                  </div>
                ) : jobsError && jobs.length === 0 ? (
                  <div
                    style={{
                      padding: "36px 24px",
                      textAlign: "center",
                      color: "#EF4444",
                    }}
                  >
                    <AlertCircle size={28} style={{ margin: "0 auto 8px" }} />
                    <div style={{ fontWeight: 600, marginBottom: "8px" }}>{jobsError}</div>
                    <button
                      type="button"
                      onClick={() => void loadJobs()}
                      style={{
                        padding: "6px 14px",
                        borderRadius: "6px",
                        border: "1px solid #EF4444",
                        background: "#FEF2F2",
                        color: "#B91C1C",
                        cursor: "pointer",
                        fontWeight: 600,
                        fontSize: "13px",
                      }}
                    >
                      Try Again
                    </button>
                  </div>
                ) : jobs.length > 0 ? (
                  <div style={{ overflowX: "auto" }}>
                    <table
                      style={{
                        width: "100%",
                        borderCollapse: "collapse",
                        textAlign: "left",
                        fontSize: "13px",
                      }}
                    >
                      <thead>
                        <tr
                          style={{
                            background: "#F8FAFC",
                            borderBottom: "1px solid #E2E8F0",
                            color: "#475569",
                            fontWeight: 600,
                            textTransform: "uppercase",
                            fontSize: "11px",
                            letterSpacing: "0.05em",
                          }}
                        >
                          <th style={{ padding: "14px 18px" }}>Job #</th>
                          <th style={{ padding: "14px 18px" }}>Organization</th>
                          <th style={{ padding: "14px 18px" }}>Customer</th>
                          <th style={{ padding: "14px 18px" }}>Service & Issue</th>
                          <th style={{ padding: "14px 18px" }}>Technician</th>
                          <th style={{ padding: "14px 18px" }}>Priority</th>
                          <th style={{ padding: "14px 18px" }}>Status</th>
                          <th style={{ padding: "14px 18px" }}>Closure Subtotal / Payment Status</th>
                          <th style={{ padding: "14px 18px" }}>Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {jobs.map((job) => {
                          const s = job.status.toUpperCase();
                          const isCompleted = s === "COMPLETED" || s === "CLOSED";
                          const isCancelled = s === "CANCELLED" || s === "CANCELED";

                          let badgeBg = "#EFF6FF";
                          let badgeColor = "#1D4ED8";
                          if (isCompleted) {
                            badgeBg = "#DCFCE7";
                            badgeColor = "#166534";
                          } else if (isCancelled) {
                            badgeBg = "#FEE2E2";
                            badgeColor = "#991B1B";
                          } else if (s === "IN_PROGRESS" || s === "ON_SITE") {
                            badgeBg = "#FEF3C7";
                            badgeColor = "#92400E";
                          }

                          return (
                            <tr
                              key={job.id}
                              style={{
                                borderBottom: "1px solid #F1F5F9",
                                transition: "background 0.15s ease",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "#F8FAFC";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "transparent";
                              }}
                            >
                              {/* Job Number */}
                              <td style={{ padding: "14px 18px" }}>
                                <span
                                  style={{
                                    fontFamily: "monospace",
                                    fontWeight: 700,
                                    color: "#0F172A",
                                    background: "#F1F5F9",
                                    padding: "3px 8px",
                                    borderRadius: "6px",
                                    fontSize: "12px",
                                  }}
                                >
                                  {job.id}
                                </span>
                              </td>

                              {/* Organization */}
                              <td style={{ padding: "14px 18px" }}>
                                <div>
                                  <strong style={{ color: "#0F172A", display: "block" }}>
                                    {job.organization_name}
                                  </strong>
                                  <span
                                    style={{
                                      fontFamily: "monospace",
                                      fontSize: "11px",
                                      color: "#64748B",
                                    }}
                                  >
                                    {job.tenant_id}
                                  </span>
                                </div>
                              </td>

                              {/* Customer */}
                              <td style={{ padding: "14px 18px" }}>
                                <div>
                                  <strong style={{ color: "#0F172A", display: "block" }}>
                                    {job.customer_name}
                                  </strong>
                                  <small style={{ color: "#94A3B8" }}>{job.contact_number}</small>
                                </div>
                              </td>

                              {/* Service & Issue */}
                              <td style={{ padding: "14px 18px", maxWidth: "220px" }}>
                                <div style={{ fontWeight: 600, color: "#334155" }}>
                                  {job.service_type}
                                </div>
                                <div
                                  style={{
                                    fontSize: "12px",
                                    color: "#64748B",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                  }}
                                  title={job.issue_description}
                                >
                                  {job.issue_description}
                                </div>
                              </td>

                              {/* Technician */}
                              <td style={{ padding: "14px 18px" }}>
                                <span
                                  style={{
                                    fontSize: "12px",
                                    fontWeight: 600,
                                    color: job.technician_name === "Unassigned" ? "#94A3B8" : "#0F172A",
                                  }}
                                >
                                  {job.technician_name}
                                </span>
                              </td>

                              {/* Priority */}
                              <td style={{ padding: "14px 18px" }}>
                                <span
                                  style={{
                                    fontSize: "11px",
                                    fontWeight: 700,
                                    padding: "2px 8px",
                                    borderRadius: "12px",
                                    background:
                                      job.priority === "HIGH" || job.priority === "URGENT"
                                        ? "#FEE2E2"
                                        : "#F1F5F9",
                                    color:
                                      job.priority === "HIGH" || job.priority === "URGENT"
                                        ? "#B91C1C"
                                        : "#475569",
                                  }}
                                >
                                  {job.priority}
                                </span>
                              </td>

                              {/* Status */}
                              <td style={{ padding: "14px 18px" }}>
                                <span
                                  style={{
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: "4px",
                                    fontSize: "11px",
                                    fontWeight: 700,
                                    padding: "3px 10px",
                                    borderRadius: "12px",
                                    background: badgeBg,
                                    color: badgeColor,
                                  }}
                                >
                                  <span
                                    style={{
                                      width: "6px",
                                      height: "6px",
                                      borderRadius: "50%",
                                      background: badgeColor,
                                    }}
                                  />
                                  {job.status.replace("_", " ")}
                                </span>
                              </td>

                              {/* Payment */}
                              <td style={{ padding: "14px 18px" }}>
                                <strong style={{ display: "block", color: job.payment_amount != null ? "#0F766E" : "#94A3B8", fontFamily: "monospace", fontSize: "12px" }}>{job.formatted_payment && job.formatted_payment !== "—" ? job.formatted_payment : "—"}</strong>
                                <small style={{ color: job.payment_status === "SUCCESSFUL" ? "#15803D" : job.payment_status === "PENDING" ? "#B45309" : "#64748B" }}>{job.payment_status || "UNAVAILABLE"}</small>
                              </td>

                              {/* Created Date */}
                              <td style={{ padding: "14px 18px", color: "#64748B", fontSize: "12px" }}>
                                {job.created_at
                                  ? new Date(job.created_at).toLocaleDateString(undefined, {
                                      month: "short",
                                      day: "numeric",
                                      year: "numeric",
                                    })
                                  : "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div
                    style={{
                      padding: "48px 24px",
                      textAlign: "center",
                      color: "#64748B",
                    }}
                  >
                    No jobs found matching your filters.
                  </div>
                )}

                {/* Pagination */}
                {jobsTotal > jobsLimit && (
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "14px 18px",
                      borderTop: "1px solid #E2E8F0",
                      background: "#FAFDFB",
                      fontSize: "13px",
                      color: "#64748B",
                      flexWrap: "wrap",
                      gap: "10px",
                    }}
                  >
                    <div>
                      Showing {(jobsPage - 1) * jobsLimit + 1} to{" "}
                      {Math.min(jobsPage * jobsLimit, jobsTotal)} of {jobsTotal} jobs
                    </div>

                    <div style={{ display: "flex", gap: "6px" }}>
                      <button
                        type="button"
                        onClick={() => setJobsPage((p) => Math.max(1, p - 1))}
                        disabled={jobsPage === 1 || jobsLoading}
                        style={{
                          padding: "6px 12px",
                          borderRadius: "6px",
                          border: "1px solid #CBD5E1",
                          background: "#FFFFFF",
                          color: jobsPage === 1 ? "#94A3B8" : "#334155",
                          cursor: jobsPage === 1 ? "not-allowed" : "pointer",
                          fontWeight: 600,
                          fontSize: "12px",
                        }}
                      >
                        Previous
                      </button>

                      {Array.from(
                        { length: Math.min(6, Math.ceil(jobsTotal / jobsLimit)) },
                        (_, i) => {
                          const pageNum = i + 1;
                          const isCurrent = pageNum === jobsPage;
                          return (
                            <button
                              key={pageNum}
                              type="button"
                              onClick={() => setJobsPage(pageNum)}
                              style={{
                                padding: "6px 12px",
                                borderRadius: "6px",
                                border: isCurrent ? "1px solid #10B981" : "1px solid #CBD5E1",
                                background: isCurrent ? "#10B981" : "#FFFFFF",
                                color: isCurrent ? "#FFFFFF" : "#334155",
                                cursor: "pointer",
                                fontWeight: 700,
                                fontSize: "12px",
                              }}
                            >
                              {pageNum}
                            </button>
                          );
                        },
                      )}

                      <button
                        type="button"
                        onClick={() =>
                          setJobsPage((p) =>
                            Math.min(Math.ceil(jobsTotal / jobsLimit), p + 1),
                          )
                        }
                        disabled={jobsPage >= Math.ceil(jobsTotal / jobsLimit) || jobsLoading}
                        style={{
                          padding: "6px 12px",
                          borderRadius: "6px",
                          border: "1px solid #CBD5E1",
                          background: "#FFFFFF",
                          color:
                            jobsPage >= Math.ceil(jobsTotal / jobsLimit)
                              ? "#94A3B8"
                              : "#334155",
                          cursor:
                            jobsPage >= Math.ceil(jobsTotal / jobsLimit)
                              ? "not-allowed"
                              : "pointer",
                          fontWeight: 600,
                          fontSize: "12px",
                        }}
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Settings Tab */}
          {activeTab === "settings" && (
            <div style={{ maxWidth: "1280px", margin: "0 auto", paddingBottom: "40px" }}>
              {/* Header */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  flexWrap: "wrap",
                  gap: "16px",
                  marginBottom: "24px",
                }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        color: "#059669",
                        background: "#ECFDF5",
                        padding: "3px 10px",
                        borderRadius: "10px",
                        letterSpacing: "0.05em",
                        textTransform: "uppercase",
                      }}
                    >
                      Platform Administration
                    </span>
                    <span style={{ fontSize: "12px", color: "#94A3B8" }}>•</span>
                    <span style={{ fontSize: "12px", color: "#64748B", fontWeight: 500 }}>System Tenant: __platform__</span>
                  </div>
                  <h1
                    style={{
                      fontSize: "24px",
                      fontWeight: 800,
                      color: "#0F172A",
                      margin: 0,
                      letterSpacing: "-0.02em",
                    }}
                  >
                    Settings & Platform Configuration
                  </h1>
                  <p style={{ fontSize: "13px", color: "#64748B", margin: "4px 0 0 0" }}>
                    Manage partner organizations, security protocols, system localization, and dispatch gateways.
                  </p>
                </div>

                {/* Primary Action Button: Create Organization */}
                <button
                  type="button"
                  onClick={() => setIsCreateOrgModalOpen(true)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "11px 22px",
                    borderRadius: "12px",
                    border: "none",
                    background: "linear-gradient(135deg, #10B981, #059669)",
                    color: "#FFFFFF",
                    fontSize: "14px",
                    fontWeight: 700,
                    cursor: "pointer",
                    boxShadow: "0 4px 14px rgba(16, 185, 129, 0.32)",
                    transition: "all 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.transform = "translateY(-1px)";
                    e.currentTarget.style.boxShadow = "0 6px 18px rgba(16, 185, 129, 0.4)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.transform = "translateY(0)";
                    e.currentTarget.style.boxShadow = "0 4px 14px rgba(16, 185, 129, 0.32)";
                  }}
                >
                  <Plus size={18} />
                  <span>Create Organization</span>
                </button>
              </div>

              {/* Toast Feedback */}
              {saveSuccessMsg && (
                <div
                  style={{
                    marginBottom: "20px",
                    background: "#ECFDF5",
                    border: "1px solid #A7F3D0",
                    borderRadius: "12px",
                    padding: "12px 18px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    color: "#065F46",
                    fontSize: "13px",
                    fontWeight: 600,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <CheckCircle2 size={18} color="#059669" />
                    <span>{saveSuccessMsg}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSaveSuccessMsg(null)}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "#065F46", padding: 0 }}
                  >
                    <X size={16} />
                  </button>
                </div>
              )}

              {/* Hero Organization Card */}
              <div
                style={{
                  background: "linear-gradient(135deg, #FFFFFF 0%, #F4FBF7 100%)",
                  borderRadius: "18px",
                  border: "1.5px solid #A7F3D0",
                  padding: "28px",
                  marginBottom: "28px",
                  boxShadow: "0 4px 20px -2px rgba(16, 185, 129, 0.08)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  flexWrap: "wrap",
                  gap: "24px",
                }}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: "18px", maxWidth: "720px" }}>
                  <div
                    style={{
                      width: "56px",
                      height: "56px",
                      borderRadius: "16px",
                      background: "linear-gradient(135deg, #ECFDF5, #D1FAE5)",
                      color: "#059669",
                      border: "1.5px solid #A7F3D0",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <Building2 size={30} />
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                      <h2 style={{ fontSize: "18px", fontWeight: 800, color: "#0F172A", margin: 0 }}>
                        Organization Onboarding & Multi-Tenant Provisioning
                      </h2>
                      <span
                        style={{
                          fontSize: "11px",
                          fontWeight: 700,
                          color: "#059669",
                          background: "#ECFDF5",
                          padding: "2px 8px",
                          borderRadius: "8px",
                          border: "1px solid #A7F3D0",
                        }}
                      >
                        Core Action
                      </span>
                    </div>
                    <p style={{ fontSize: "13px", color: "#475569", lineHeight: "1.55", margin: "0 0 12px 0" }}>
                      Register partner organizations, provision isolated tenant workspaces, and set up dedicated Super Admin credentials with OTP verification.
                    </p>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "6px",
                          fontSize: "12px",
                          fontWeight: 600,
                          color: "#065F46",
                          background: "#D1FAE5",
                          padding: "4px 10px",
                          borderRadius: "8px",
                        }}
                      >
                        <span
                          style={{
                            width: "7px",
                            height: "7px",
                            borderRadius: "50%",
                            background: "#059669",
                          }}
                        />
                        {stats?.total_organizations ?? 14} Partner Organizations Active
                      </span>
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "6px",
                          fontSize: "12px",
                          fontWeight: 500,
                          color: "#334155",
                          background: "#F1F5F9",
                          padding: "4px 10px",
                          borderRadius: "8px",
                        }}
                      >
                        <ShieldCheck size={14} color="#059669" />
                        Multi-Tenant Data Isolation
                      </span>
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "6px",
                          fontSize: "12px",
                          fontWeight: 500,
                          color: "#334155",
                          background: "#F1F5F9",
                          padding: "4px 10px",
                          borderRadius: "8px",
                        }}
                      >
                        <CheckCircle2 size={14} color="#059669" />
                        OTP Verification Guard
                      </span>
                    </div>
                  </div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "10px", minWidth: "200px" }}>
                  <button
                    type="button"
                    onClick={() => setIsCreateOrgModalOpen(true)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: "8px",
                      padding: "12px 24px",
                      borderRadius: "12px",
                      border: "none",
                      background: "linear-gradient(135deg, #10B981, #059669)",
                      color: "#FFFFFF",
                      fontSize: "14px",
                      fontWeight: 700,
                      cursor: "pointer",
                      boxShadow: "0 4px 12px rgba(16, 185, 129, 0.28)",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <Plus size={18} />
                    Create Organization
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab("organizations")}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: "6px",
                      padding: "10px 18px",
                      borderRadius: "12px",
                      border: "1px solid #CBD5E1",
                      background: "#FFFFFF",
                      color: "#334155",
                      fontSize: "13px",
                      fontWeight: 600,
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "#F8FAFC";
                      e.currentTarget.style.borderColor = "#94A3B8";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "#FFFFFF";
                      e.currentTarget.style.borderColor = "#CBD5E1";
                    }}
                  >
                    <span>View All Organizations</span>
                    <ArrowRight size={15} />
                  </button>
                </div>
              </div>

              {/* Create Organization Modal Dialog */}
              {isCreateOrgModalOpen && (
                <div
                  style={{
                    position: "fixed",
                    inset: 0,
                    zIndex: 9999,
                    backgroundColor: "rgba(15, 23, 42, 0.75)",
                    backdropFilter: "blur(8px)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: "20px",
                    overflowY: "auto",
                  }}
                  onClick={(e) => {
                    if (e.target === e.currentTarget) {
                      setIsCreateOrgModalOpen(false);
                    }
                  }}
                >
                  <div
                    style={{
                      position: "relative",
                      maxWidth: "640px",
                      width: "100%",
                      maxHeight: "92vh",
                      overflowY: "auto",
                      borderRadius: "20px",
                      boxShadow: "0 25px 60px -15px rgba(0, 0, 0, 0.5)",
                    }}
                  >
                    <Suspense
                      fallback={
                        <div
                          style={{
                            padding: "48px 24px",
                            textAlign: "center",
                            color: "#10B981",
                            fontSize: "14px",
                            fontWeight: 500,
                            background: "rgba(15,35,23,.95)",
                            borderRadius: "20px",
                          }}
                        >
                          Loading organization creation wizard...
                        </div>
                      }
                    >
                      <OrganizationOnboardingPage
                        isHeadPortal={true}
                        embedded={true}
                        onBackToLogin={() => setIsCreateOrgModalOpen(false)}
                        onSuccessHead={() => {
                          void loadOrganizations();
                          void loadStats();
                          setIsCreateOrgModalOpen(false);
                          setActiveTab("organizations");
                        }}
                      />
                    </Suspense>
                  </div>
                </div>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
};
