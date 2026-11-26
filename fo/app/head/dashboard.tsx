import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Building2, BriefcaseBusiness, LayoutDashboard, LogOut, RefreshCw, TrendingUp, Workflow } from "lucide-react-native";
import { apiClient } from "../../src/api/client";
import { useAuthStore } from "../../src/auth/authStore";
import { fullName } from "../../src/auth/roles";
import { HeadAgentTracking } from "../../src/head/HeadAgentTracking";

type Tab = "dashboard" | "organizations" | "jobs" | "agent_tracking" | "performance";
type Org = { id: string; name: string; status?: string; subscription_plan?: string; contact_email?: string; user_count?: number; job_count?: number; technician_count?: number };
type Job = { id: number; job_number?: string; organization_name?: string; customer_name?: string; service_type?: string; status?: string; technician_name?: string; location?: string };
type Stats = { total_organizations: number; total_jobs: number; active_jobs: number; completed_jobs: number; cancelled_jobs: number; total_payment?: string };
type OrganizationPerformance = { organization_id: string; organization_name: string; total_jobs: number; accepted_jobs: number; rejected_jobs: number; payment_amount: number };
type Performance = { data: OrganizationPerformance[]; totals: { payment_amount: number; accepted_jobs: number; rejected_jobs: number } };
const initialStats: Stats = { total_organizations: 0, total_jobs: 0, active_jobs: 0, completed_jobs: 0, cancelled_jobs: 0 };

export default function HeadDashboard() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [tab, setTab] = useState<Tab>("dashboard");
  const [stats, setStats] = useState<Stats>(initialStats);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const [statsResult, orgResult, jobResult, performanceResult] = await Promise.allSettled([
        apiClient.get<Stats>("/organizations/platform-stats"),
        apiClient.get<{ data?: Org[]; total?: number }>("/organizations?limit=100"),
        apiClient.get<{ data?: Job[]; platform_total?: number; platform_active?: number; platform_completed?: number; platform_cancelled?: number }>("/organizations/platform-jobs", { params: { page: 1, limit: 50 } }),
        apiClient.get<Performance>("/organizations/platform-performance"),
      ]);
      if (statsResult.status === "fulfilled") setStats(statsResult.value.data);
      else throw statsResult.reason;
      if (orgResult.status === "fulfilled") setOrgs(orgResult.value.data.data || []);
      if (jobResult.status === "fulfilled") {
        setJobs(jobResult.value.data.data || []);
        const j = jobResult.value.data;
        setStats((previous) => ({ ...previous, total_jobs: j.platform_total ?? previous.total_jobs, active_jobs: j.platform_active ?? previous.active_jobs, completed_jobs: j.platform_completed ?? previous.completed_jobs, cancelled_jobs: j.platform_cancelled ?? previous.cancelled_jobs }));
      }
      if (performanceResult.status === "fulfilled") setPerformance(performanceResult.value.data);
    } catch { setError("Could not load Head operations data. Pull to refresh or try again."); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const tabs: { id: Tab; label: string; icon: typeof LayoutDashboard }[] = [
    { id: "dashboard", label: "Overview", icon: LayoutDashboard },
    { id: "organizations", label: "Organizations", icon: Building2 },
    { id: "jobs", label: "Jobs", icon: BriefcaseBusiness },
    { id: "agent_tracking", label: "Agent tracking", icon: Workflow },
    { id: "performance", label: "Performance", icon: TrendingUp },
  ];
  return <SafeAreaView style={[s.safe, { backgroundColor: "#F1F6F3" }]}>
    <View style={[s.top, { backgroundColor: "#123D2B", paddingTop: 13, paddingBottom: 15, shadowColor: "#0B2B1D", shadowOpacity: 0.14, shadowRadius: 10, elevation: 5 }]}><View style={s.brandRow}><View style={[s.mark, { backgroundColor: "#DDF5E7" }]}><Text style={[s.markText, { color: "#087A4E" }]}>F</Text></View><View><Text style={[s.brand, { color: "#FFFFFF" }]}>FieldOps</Text><Text style={[s.kicker, { color: "#8CDDB0" }]}>PLATFORM HEAD</Text></View></View><Pressable onPress={() => void logout()} style={[s.logout, { backgroundColor: "#24593F", borderRadius: 12, paddingHorizontal: 11 }]}><LogOut size={17} color="#EAF7EF"/><Text style={[s.logoutText, { color: "#EAF7EF" }]}>Sign out</Text></Pressable></View>
    <ScrollView style={s.scroll} contentContainerStyle={s.content} refreshControl={<RefreshControl refreshing={busy} onRefresh={() => void load()} tintColor="#078B54" />}>
      {tab !== "agent_tracking" && <View style={s.headingRow}><View><Text style={s.eyebrow}>PLATFORM OPERATIONS</Text><Text style={s.title}>{tab === "dashboard" ? `Welcome, ${user ? fullName(user).split(" ")[0] : "Head"}` : tabs.find((item) => item.id === tab)?.label}</Text><Text style={s.sub}>Manage FieldOps across all organizations.</Text></View><Pressable onPress={() => void load()} style={s.refresh}><RefreshCw size={17} color="#146B4B"/></Pressable></View>}
      {error ? <Pressable onPress={() => void load()} style={s.error}><Text style={s.errorText}>{error}</Text></Pressable> : null}
      {(tab === "dashboard" || tab === "jobs") && <View style={s.stats}>{[["Organizations", stats.total_organizations, "#E9F7F0"], ["Total jobs", stats.total_jobs, "#EEF4FF"], ["Active jobs", stats.active_jobs, "#FFF6E7"], ["Completed", stats.completed_jobs, "#F0EDFF"]].map(([label, value, tint]) => <View key={String(label)} style={[s.stat, { backgroundColor: String(tint) }]}><Text style={s.statValue}>{busy ? "—" : value}</Text><Text style={s.statLabel}>{label}</Text></View>)}</View>}
      {busy && !orgs.length && !jobs.length ? <View style={s.loading}><ActivityIndicator color="#078B54"/><Text style={s.muted}>Loading platform data…</Text></View> : null}
      {(tab === "dashboard" || tab === "organizations") && <View style={s.section}><View style={s.sectionHead}><Text style={s.sectionTitle}>Organizations</Text><Text style={s.count}>{orgs.length}</Text></View>{orgs.length ? orgs.slice(0, tab === "dashboard" ? 5 : undefined).map((org) => <View key={org.id} style={s.row}><View style={s.orgIcon}><Building2 size={18} color="#087A4E"/></View><View style={s.rowBody}><Text style={s.rowTitle} numberOfLines={1}>{org.name}</Text><Text style={s.rowDetail}>{org.subscription_plan || "Standard plan"} · {org.job_count ?? 0} jobs · {org.user_count ?? 0} users</Text></View><Text style={[s.pill, String(org.status).toUpperCase() === "ACTIVE" ? s.good : s.neutral]}>{org.status || "Active"}</Text></View>) : <Text style={s.empty}>No organizations found.</Text>}</View>}
      {(tab === "dashboard" || tab === "jobs") && <View style={s.section}><View style={s.sectionHead}><Text style={s.sectionTitle}>Platform jobs</Text><Text style={s.count}>{jobs.length}</Text></View>{jobs.slice(0, tab === "dashboard" ? 5 : undefined).map((job) => <View key={job.id} style={s.job}><View style={s.jobTop}><Text style={s.rowTitle}>{job.job_number || `JOB-${job.id}`}</Text><Text style={[s.pill, s.neutral]}>{job.status || "Open"}</Text></View><Text style={s.rowDetail}>{job.service_type || "Service job"} · {job.organization_name || "Organization"}</Text><Text style={s.rowDetail}>{job.customer_name || "Customer"}{job.technician_name ? ` · ${job.technician_name}` : " · Unassigned"}</Text></View>)}{!jobs.length && <Text style={s.empty}>No platform jobs found.</Text>}</View>}
      {tab === "agent_tracking" && <HeadAgentTracking />}
      {tab === "performance" && <>
        <View style={s.performanceSummary}>
          <Text style={s.performanceEyebrow}>ALL ORGANIZATIONS</Text>
          <Text style={s.performanceRevenue}>{formatRupees(performance?.totals.payment_amount ?? 0)}</Text>
          <Text style={s.performanceCaption}>Recorded job payments</Text>
          <View style={s.outcomeTotals}>
            <View style={s.outcomeBox}><Text style={s.acceptedNumber}>{performance?.totals.accepted_jobs ?? 0}</Text><Text style={s.outcomeLabel}>Accepted jobs</Text></View>
            <View style={s.outcomeDivider}/>
            <View style={s.outcomeBox}><Text style={s.rejectedNumber}>{performance?.totals.rejected_jobs ?? 0}</Text><Text style={s.outcomeLabel}>Rejected jobs</Text></View>
          </View>
        </View>
        <View style={s.section}>
          <View style={s.sectionHead}><Text style={s.sectionTitle}>Organization performance</Text><Text style={s.count}>{performance?.data.length ?? 0}</Text></View>
          {performance?.data.map((item) => <View key={item.organization_id} style={s.performanceRow}>
            <View style={s.performanceOrgTop}><View style={s.orgIcon}><Building2 size={18} color="#087A4E"/></View><Text style={s.rowTitle} numberOfLines={1}>{item.organization_name}</Text><Text style={s.performanceAmount}>{formatRupees(item.payment_amount)}</Text></View>
            <View style={s.performanceCounts}><Text style={s.acceptedSmall}>Accepted {item.accepted_jobs}</Text><Text style={s.rejectedSmall}>Rejected {item.rejected_jobs}</Text><Text style={s.totalSmall}>{item.total_jobs} total</Text></View>
          </View>)}
          {!performance?.data.length && <Text style={s.empty}>{busy ? "Loading performance…" : "No organization performance data found."}</Text>}
        </View>
      </>}
      {tab === "dashboard" && <View style={s.footer}><Text style={s.footerTitle}>Your FieldOps platform</Text><Text style={s.rowDetail}>Signed in as {user?.email}</Text></View>}
    </ScrollView>
    <View style={s.bottomNav} accessibilityRole="tablist">
      {tabs.map(({ id, label, icon: Icon }) => {
        const active = tab === id;
        const shortLabel = id === "dashboard" ? "Overview" : id === "organizations" ? "Orgs" : id === "agent_tracking" ? "Tracking" : label;
        return <Pressable key={id} onPress={() => setTab(id)} accessibilityRole="tab" accessibilityState={{ selected: active }} style={[s.bottomTab, active && s.bottomTabActive]}>
          <Icon size={20} color={active ? "#087A4E" : "#71847A"}/>
          <Text numberOfLines={1} style={[s.bottomTabText, active && s.bottomTabTextActive]}>{shortLabel}</Text>
        </Pressable>;
      })}
    </View>
  </SafeAreaView>;
}

function formatRupees(amount: number): string {
  return `₹${Number(amount || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

const s = StyleSheet.create({ safe: { flex: 1, backgroundColor: "#F4F8F6" }, top: { paddingHorizontal: 20, paddingTop: 10, paddingBottom: 14, flexDirection: "row", justifyContent: "space-between", alignItems: "center" }, brandRow: { flexDirection: "row", alignItems: "center", gap: 10 }, mark: { width: 38, height: 38, borderRadius: 12, backgroundColor: "#078B54", alignItems: "center", justifyContent: "center" }, markText: { color: "white", fontWeight: "900", fontSize: 20 }, brand: { fontSize: 17, fontWeight: "800", color: "#173326" }, kicker: { fontSize: 9, letterSpacing: 1.3, fontWeight: "800", color: "#078B54", marginTop: 2 }, logout: { flexDirection: "row", alignItems: "center", gap: 6, padding: 8 }, logoutText: { color: "#456257", fontSize: 12, fontWeight: "700" }, scroll: { flex: 1 }, bottomNav: { flexDirection: "row", alignItems: "stretch", paddingHorizontal: 6, paddingTop: 7, paddingBottom: 5, backgroundColor: "#FFFFFF", borderTopWidth: 1, borderTopColor: "#DFE9E3", shadowColor: "#173326", shadowOffset: { width: 0, height: -3 }, shadowOpacity: 0.06, shadowRadius: 8, elevation: 10 }, bottomTab: { flex: 1, minWidth: 0, alignItems: "center", justifyContent: "center", gap: 4, paddingVertical: 5, borderRadius: 12 }, bottomTabActive: { backgroundColor: "#E9F7F0" }, bottomTabText: { color: "#71847A", fontSize: 10, fontWeight: "700" }, bottomTabTextActive: { color: "#087A4E" }, content: { padding: 18, paddingTop: 8, paddingBottom: 20, gap: 16 }, headingRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }, eyebrow: { fontSize: 10, letterSpacing: 1.4, color: "#078B54", fontWeight: "800", marginBottom: 5 }, title: { fontSize: 25, color: "#173326", fontWeight: "800" }, sub: { color: "#70847A", fontSize: 13, marginTop: 4 }, refresh: { height: 40, width: 40, alignItems: "center", justifyContent: "center", backgroundColor: "white", borderRadius: 13, borderWidth: 1, borderColor: "#DFE9E3" }, stats: { flexDirection: "row", flexWrap: "wrap", gap: 10 }, stat: { width: "48%", flexGrow: 1, borderRadius: 17, padding: 16, minHeight: 92, justifyContent: "center" }, statValue: { fontSize: 24, fontWeight: "800", color: "#173326" }, statLabel: { fontSize: 12, color: "#647A6F", marginTop: 4, fontWeight: "600" }, section: { backgroundColor: "white", borderRadius: 18, borderWidth: 1, borderColor: "#E3EBE6", padding: 16, gap: 12 }, sectionHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 2 }, count: { color: "#73877D", fontSize: 12, fontWeight: "700" }, row: { flexDirection: "row", alignItems: "center", gap: 11, paddingVertical: 10, borderTopWidth: 1, borderTopColor: "#EEF2EF" }, orgIcon: { width: 38, height: 38, borderRadius: 12, backgroundColor: "#E9F7F0", alignItems: "center", justifyContent: "center" }, rowBody: { flex: 1 }, rowTitle: { color: "#1A3529", fontSize: 13, fontWeight: "800" }, rowDetail: { color: "#74867D", fontSize: 11, lineHeight: 17, marginTop: 4 }, pill: { paddingHorizontal: 9, paddingVertical: 5, overflow: "hidden", borderRadius: 20, fontSize: 10, fontWeight: "800" }, good: { backgroundColor: "#E7F7EF", color: "#168653" }, neutral: { backgroundColor: "#F0F4F2", color: "#63766D" }, job: { borderTopWidth: 1, borderTopColor: "#EEF2EF", paddingTop: 12, gap: 2 }, jobTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }, empty: { paddingVertical: 16, textAlign: "center", color: "#74867D", fontSize: 13 }, loading: { padding: 24, alignItems: "center", gap: 10 }, muted: { color: "#74867D", fontSize: 12 }, error: { backgroundColor: "#FFF0EE", padding: 12, borderRadius: 12 }, errorText: { color: "#B42318", fontSize: 12 }, footer: { padding: 16, gap: 4, backgroundColor: "#EAF4EF", borderRadius: 16 }, footerTitle: { color: "#173326", fontWeight: "800", fontSize: 14 }, performanceSummary: { padding: 20, borderRadius: 20, backgroundColor: "#E6F7EE", borderWidth: 1, borderColor: "#C8EAD7", gap: 5 }, performanceEyebrow: { color: "#087A4E", fontSize: 10, fontWeight: "800", letterSpacing: 1.2 }, performanceRevenue: { color: "#123C2B", fontSize: 30, fontWeight: "900", marginTop: 3 }, performanceCaption: { color: "#668174", fontSize: 12 }, outcomeTotals: { flexDirection: "row", alignItems: "center", marginTop: 16, paddingTop: 13, borderTopWidth: 1, borderTopColor: "#C8EAD7" }, outcomeBox: { flex: 1, gap: 3 }, outcomeDivider: { width: 1, height: 38, backgroundColor: "#C8EAD7", marginHorizontal: 14 }, outcomeLabel: { color: "#668174", fontSize: 11, fontWeight: "600" }, acceptedNumber: { color: "#087A4E", fontSize: 22, fontWeight: "900" }, rejectedNumber: { color: "#BE3F4C", fontSize: 22, fontWeight: "900" }, performanceRow: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: "#EEF2EF", gap: 8 }, performanceOrgTop: { flexDirection: "row", alignItems: "center", gap: 10 }, performanceAmount: { marginLeft: "auto", color: "#087A4E", fontSize: 13, fontWeight: "900" }, performanceCounts: { flexDirection: "row", flexWrap: "wrap", gap: 12, paddingLeft: 48 }, acceptedSmall: { color: "#087A4E", fontSize: 11, fontWeight: "700" }, rejectedSmall: { color: "#BE3F4C", fontSize: 11, fontWeight: "700" }, totalSmall: { color: "#71847A", fontSize: 11, fontWeight: "600" } });
