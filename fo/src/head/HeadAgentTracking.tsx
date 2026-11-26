import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Activity, AlertCircle, Check, CircleCheck, CircleDashed, Clock3, MapPin, RefreshCw, Search, Workflow } from "lucide-react-native";
import { apiClient } from "../api/client";

type Stage = { key: string; label: string; state: "complete" | "active" | "waiting" | "locked" | "event_driven"; detail: string; timestamp: string | null };
type TrackedJob = { job_id: number; job_number: number | string; service_type: string; status: string; priority: string; technician_name: string | null; customer_name: string | null; location: string | null; created_at: string | null; updated_at: string | null; assigned_at: string | null; accepted: boolean; current_agent: string; current_detail: string; stages: Stage[] };
type TrackingEvent = { timestamp: string | null; agent: string; title: string; detail: string; kind: string; delivery_status?: string | null };
type TrackingDetail = TrackedJob & { events: TrackingEvent[] };
type Response = { jobs: TrackedJob[]; total: number; active: number; waiting_acceptance: number; generated_at: string };
type Filter = "all" | "active" | "complete" | "failed";

const filters: { key: Filter; label: string }[] = [
  { key: "all", label: "All" }, { key: "active", label: "Running" },
  { key: "complete", label: "Completed" }, { key: "failed", label: "Failed" },
];
const terminal = (status: string) => ["COMPLETED", "CLOSED", "CANCELLED", "CANCELED"].includes(status.toUpperCase());
const failed = (status: string) => ["FAILED", "CANCELLED", "CANCELED"].includes(status.toUpperCase());
const completed = (status: string) => ["COMPLETED", "CLOSED"].includes(status.toUpperCase());

function progress(job: TrackedJob) {
  if (completed(job.status)) return 100;
  const stages = job.stages || [];
  if (!stages.length) return 0;
  const done = stages.filter((stage) => stage.state === "complete").length;
  const active = stages.some((stage) => stage.state === "active") ? 0.45 : 0;
  return Math.min(99, Math.round(((done + active) / stages.length) * 100));
}

function stageColor(stage: Stage) {
  if (stage.state === "complete") return "#078B54";
  if (stage.state === "active") return "#D28A22";
  if (stage.state === "waiting") return "#3978BD";
  return "#A6B3AC";
}

function formatDate(value?: string | null) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function HeadAgentTracking() {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<TrackingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true); else setLoading(true);
    try {
      const result = await apiClient.get<Response>("/api/admin/agent-tracking", { params: { limit: 250 } });
      setData(result.data);
      setError("");
    } catch (requestError: any) {
      const status = requestError?.response?.status;
      setError(status === 403 ? "Your account cannot view agent tracking." : status === 404 ? "Agent tracking is unavailable on this server." : "Could not load workflows. Pull to refresh and try again.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(true), 20_000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    const timer = setInterval(() => {
      void apiClient.get<TrackingDetail>(`/api/admin/agent-tracking/${selected.job_id}`)
        .then((result) => setSelected(result.data))
        .catch(() => undefined);
    }, 20_000);
    return () => clearInterval(timer);
  }, [selected?.job_id]);

  const jobs = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (data?.jobs || []).filter((job) => {
      const matchesFilter = filter === "all"
        || (filter === "active" && !terminal(job.status) && !failed(job.status))
        || (filter === "complete" && terminal(job.status) && !failed(job.status))
        || (filter === "failed" && failed(job.status));
      const matchesQuery = !query || [String(job.job_number), job.service_type, job.status, job.technician_name || "", job.customer_name || "", job.location || "", job.current_agent].some((value) => value.toLowerCase().includes(query));
      return matchesFilter && matchesQuery;
    });
  }, [data?.jobs, filter, search]);

  const openJob = async (job: TrackedJob) => {
    setDetailLoading(true);
    setSelected(null);
    try {
      const result = await apiClient.get<TrackingDetail>(`/api/admin/agent-tracking/${job.job_id}`);
      setSelected(result.data);
    } catch {
      setError(`Could not load the workflow details for job #${job.job_number}.`);
    } finally {
      setDetailLoading(false);
    }
  };

  if (selected) return <View style={s.root}>
    <Pressable onPress={() => setSelected(null)} style={s.back}><Text style={s.backText}>‹  All workflows</Text></Pressable>
    <View style={s.detailHero}><Text style={s.eyebrow}>JOB WORKFLOW · #{selected.job_number}</Text><Text style={s.title}>{selected.service_type || "Service job"}</Text><Text style={s.sub}>{selected.current_agent} · {selected.status}</Text></View>
    <View style={s.card}><Text style={s.cardTitle}>Agent stages</Text>{selected.stages.map((stage, index) => <View key={`${stage.key}-${index}`} style={s.detailStage}>
      <View style={[s.stageDot, { backgroundColor: stageColor(stage) }]}>{stage.state === "complete" ? <Check size={13} color="#fff"/> : <CircleDashed size={13} color="#fff"/>}</View>
      <View style={s.stageBody}><Text style={s.stageTitle}>{stage.label}</Text><Text style={s.stageDetail}>{stage.detail}</Text><Text style={s.stageDate}>{formatDate(stage.timestamp)}</Text></View>
    </View>)}</View>
    <View style={s.card}><Text style={s.cardTitle}>Workflow activity</Text>{selected.events?.length ? selected.events.slice().reverse().map((event, index) => <View key={`${event.kind}-${index}`} style={s.event}>
      <View style={s.eventDot}/><View style={s.stageBody}><Text style={s.stageTitle}>{event.title || event.agent}</Text><Text style={s.stageDetail}>{event.detail}</Text><Text style={s.stageDate}>{event.agent} · {formatDate(event.timestamp)}</Text></View>
    </View>) : <Text style={s.emptyText}>No detailed events have been recorded for this job.</Text>}</View>
  </View>;

  return <View style={s.root}>
    <View style={s.hero}>
      <View style={s.heroIcon}><Workflow size={21} color="#fff"/></View>
      <View style={s.heroCopy}><Text style={s.eyebrow}>LIVE OPERATIONS</Text><Text style={[s.title, s.heroTitle]}>Agent Tracking</Text><Text style={[s.sub, s.heroSub]}>Follow agent workflows across the platform.</Text></View>
      <Pressable onPress={() => void load(true)} accessibilityLabel="Refresh agent tracking" style={s.refresh}><RefreshCw size={17} color="#087A4E"/><View style={[s.refreshDot, refreshing && s.refreshDotActive]}/></Pressable>
    </View>
    <View style={s.metrics}>
      <Metric icon={<Workflow size={16} color="#087A4E"/>} label="Tracked" value={data?.total ?? 0} tint="#EAF7EF"/>
      <Metric icon={<Activity size={16} color="#B77B1B"/>} label="Running" value={data?.active ?? 0} tint="#FFF5E2"/>
      <Metric icon={<Clock3 size={16} color="#3978BD"/>} label="Awaiting" value={data?.waiting_acceptance ?? 0} tint="#EAF2FC"/>
    </View>
    <View style={s.toolbar}>
      <View style={s.search}><Search size={17} color="#82948B"/><TextInput value={search} onChangeText={setSearch} placeholder="Search jobs, people, locations" placeholderTextColor="#92A198" style={s.searchInput}/></View>
      <View style={s.filters}>{filters.map((item) => <Pressable key={item.key} onPress={() => setFilter(item.key)} style={[s.filter, filter === item.key && s.filterActive]}><Text style={[s.filterText, filter === item.key && s.filterTextActive]}>{item.label}</Text></Pressable>)}</View>
    </View>
    {loading ? <View style={s.state}><ActivityIndicator color="#078B54"/><Text style={s.muted}>Loading platform workflows…</Text></View> : error ? <Pressable onPress={() => void load()} style={s.error}><AlertCircle size={18} color="#B42318"/><Text style={s.errorText}>{error}</Text><Text style={s.retry}>Retry</Text></Pressable> : detailLoading ? <View style={s.state}><ActivityIndicator color="#078B54"/><Text style={s.muted}>Loading workflow details…</Text></View> : jobs.length ? jobs.map((job) => <WorkflowCard key={job.job_id} job={job} onPress={() => void openJob(job)}/>) : <View style={s.empty}><CircleCheck size={24} color="#6C9D83"/><Text style={s.cardTitle}>No matching workflows</Text><Text style={s.muted}>Try a different filter or search term.</Text></View>}
    {data?.generated_at ? <Text style={s.updated}>Updated {formatDate(data.generated_at)} · Refreshes every 20 seconds</Text> : null}
  </View>;
}

function Metric({ icon, label, value, tint }: { icon: ReactNode; label: string; value: number; tint: string }) {
  return <View style={[s.metric, { backgroundColor: tint }]}><View style={s.metricIcon}>{icon}</View><Text style={s.metricValue}>{value}</Text><Text style={s.metricLabel}>{label}</Text></View>;
}

function WorkflowCard({ job, onPress }: { job: TrackedJob; onPress: () => void }) {
  const pct = progress(job);
  const isProblem = failed(job.status);
  const isDone = completed(job.status);
  const stages = (job.stages || []).filter((stage) => stage.key !== "technician_work").slice(0, 6);
  return <Pressable onPress={onPress} style={s.jobCard}>
    <View style={s.jobTop}><View style={s.jobIdRow}><Text style={s.jobId}>#{job.job_number}</Text><Text style={[s.status, isProblem ? s.statusFailed : isDone ? s.statusDone : s.statusRunning]}>{isProblem ? "Failed" : isDone ? "Completed" : "Running"}</Text></View><Text style={s.jobUpdated}>{formatDate(job.updated_at)}</Text></View>
    <Text numberOfLines={1} style={s.jobName}>{job.service_type || "Service job"}</Text>
    <View style={s.jobMeta}><Text numberOfLines={1} style={s.metaText}>{job.customer_name || "Customer"}</Text><Text style={s.metaDivider}>·</Text><MapPin size={12} color="#8A9B92"/><Text numberOfLines={1} style={s.metaText}>{job.location || "Location not recorded"}</Text></View>
    <View style={s.agentLine}><View style={[s.agentDot, { backgroundColor: isProblem ? "#D74D5B" : isDone ? "#078B54" : "#D28A22" }]} /><View style={s.agentCopy}><Text style={s.agentName}>{job.current_agent}</Text><Text numberOfLines={2} style={s.agentDetail}>{job.current_detail}</Text></View><Text style={s.progressLabel}>{pct}%</Text></View>
    <View style={s.progressTrack}><View style={[s.progressFill, { width: `${pct}%`, backgroundColor: isProblem ? "#D74D5B" : "#078B54" }]} /></View>
    <View style={s.pipeline}>{stages.map((stage, index) => <View key={stage.key} style={s.pipelineItem}><View style={[s.pipelineDot, { backgroundColor: stageColor(stage) }]}>{stage.state === "complete" ? <Check size={9} color="#fff"/> : null}</View>{index < stages.length - 1 ? <View style={[s.pipelineLine, stage.state === "complete" && s.pipelineLineDone]}/> : null}</View>)}</View>
    <Text style={s.viewDetail}>View workflow details  ›</Text>
  </Pressable>;
}

const s = StyleSheet.create({
  root: { gap: 13 }, hero: { flexDirection: "row", alignItems: "center", gap: 11, backgroundColor: "#123D2B", borderRadius: 20, padding: 16 }, heroIcon: { width: 42, height: 42, borderRadius: 14, backgroundColor: "#218356", alignItems: "center", justifyContent: "center" }, heroCopy: { flex: 1 }, eyebrow: { color: "#78D4A6", fontSize: 9, fontWeight: "900", letterSpacing: 1.3 }, title: { color: "#173326", fontSize: 22, fontWeight: "900" }, sub: { color: "#74867D", fontSize: 12, marginTop: 3 }, heroTitle: { color: "#fff" }, refresh: { width: 38, height: 38, borderRadius: 13, backgroundColor: "#fff", alignItems: "center", justifyContent: "center", position: "relative" }, refreshDot: { position: "absolute", width: 7, height: 7, borderRadius: 4, right: 7, top: 7, backgroundColor: "#A6B3AC" }, refreshDotActive: { backgroundColor: "#078B54" }, metrics: { flexDirection: "row", gap: 8 }, metric: { flex: 1, minWidth: 0, borderRadius: 15, paddingVertical: 11, paddingHorizontal: 10 }, metricIcon: { marginBottom: 5 }, metricValue: { color: "#173326", fontSize: 20, fontWeight: "900" }, metricLabel: { color: "#74867D", fontSize: 10, fontWeight: "700", marginTop: 2 }, toolbar: { gap: 9 }, search: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 12, borderRadius: 13, backgroundColor: "#fff", borderWidth: 1, borderColor: "#E2EBE5" }, searchInput: { flex: 1, color: "#173326", fontSize: 12, paddingVertical: 8 }, filters: { flexDirection: "row", gap: 6 }, filter: { flex: 1, alignItems: "center", paddingVertical: 8, borderRadius: 10, backgroundColor: "#E9EFEB" }, filterActive: { backgroundColor: "#087A4E" }, filterText: { color: "#647A6F", fontSize: 10, fontWeight: "800" }, filterTextActive: { color: "#fff" }, jobCard: { padding: 14, borderRadius: 17, backgroundColor: "#fff", borderWidth: 1, borderColor: "#E4ECE7", gap: 8 }, jobTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" }, jobIdRow: { flexDirection: "row", alignItems: "center", gap: 7 }, jobId: { color: "#173326", fontSize: 13, fontWeight: "900" }, jobUpdated: { color: "#8A9B92", fontSize: 9 }, status: { overflow: "hidden", borderRadius: 20, paddingHorizontal: 8, paddingVertical: 4, fontSize: 9, fontWeight: "800" }, statusFailed: { color: "#B42318", backgroundColor: "#FFF0EE" }, statusDone: { color: "#087A4E", backgroundColor: "#EAF7EF" }, statusRunning: { color: "#9A6715", backgroundColor: "#FFF5E2" }, jobName: { color: "#173326", fontSize: 15, fontWeight: "800" }, jobMeta: { flexDirection: "row", alignItems: "center", gap: 5 }, metaText: { color: "#70847A", fontSize: 10, maxWidth: "44%" }, metaDivider: { color: "#B0BDB6", fontSize: 12 }, agentLine: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 2 }, agentDot: { width: 8, height: 8, borderRadius: 4, marginTop: 4 }, agentCopy: { flex: 1 }, agentName: { color: "#173326", fontSize: 11, fontWeight: "800" }, agentDetail: { color: "#70847A", fontSize: 10, lineHeight: 15, marginTop: 2 }, progressLabel: { color: "#087A4E", fontSize: 11, fontWeight: "900" }, progressTrack: { height: 5, borderRadius: 3, backgroundColor: "#EAF0EC", overflow: "hidden" }, progressFill: { height: "100%", borderRadius: 3 }, pipeline: { flexDirection: "row", alignItems: "center", paddingHorizontal: 3, marginTop: 1 }, pipelineItem: { flex: 1, flexDirection: "row", alignItems: "center" }, pipelineDot: { width: 14, height: 14, borderRadius: 7, alignItems: "center", justifyContent: "center" }, pipelineLine: { flex: 1, height: 2, backgroundColor: "#E2EAE5" }, pipelineLineDone: { backgroundColor: "#74C69D" }, viewDetail: { color: "#087A4E", fontSize: 10, fontWeight: "800", textAlign: "right", marginTop: 1 }, state: { padding: 24, alignItems: "center", gap: 10 }, muted: { color: "#74867D", fontSize: 12 }, error: { flexDirection: "row", alignItems: "center", gap: 8, padding: 13, borderRadius: 13, backgroundColor: "#FFF0EE" }, errorText: { flex: 1, color: "#B42318", fontSize: 11 }, retry: { color: "#B42318", fontSize: 11, fontWeight: "800" }, empty: { padding: 22, alignItems: "center", gap: 7, borderRadius: 16, backgroundColor: "#fff" }, emptyText: { color: "#74867D", fontSize: 11, lineHeight: 17 }, card: { borderRadius: 17, padding: 14, backgroundColor: "#fff", borderWidth: 1, borderColor: "#E4ECE7", gap: 10 }, cardTitle: { color: "#173326", fontSize: 14, fontWeight: "900" }, back: { alignSelf: "flex-start", paddingVertical: 5 }, backText: { color: "#087A4E", fontSize: 12, fontWeight: "800" }, detailHero: { backgroundColor: "#E6F7EE", borderRadius: 18, padding: 16, gap: 5 }, detailStage: { flexDirection: "row", alignItems: "flex-start", gap: 9, borderTopWidth: 1, borderTopColor: "#EEF2EF", paddingTop: 10 }, stageDot: { width: 22, height: 22, borderRadius: 11, alignItems: "center", justifyContent: "center" }, stageBody: { flex: 1, gap: 3 }, stageTitle: { color: "#173326", fontSize: 11, fontWeight: "800" }, stageDetail: { color: "#70847A", fontSize: 10, lineHeight: 15 }, stageDate: { color: "#98A59E", fontSize: 9 }, event: { flexDirection: "row", alignItems: "flex-start", gap: 9, borderTopWidth: 1, borderTopColor: "#EEF2EF", paddingTop: 10 }, eventDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: "#81B89A", marginTop: 3 }, updated: { color: "#95A49C", textAlign: "center", fontSize: 9, paddingVertical: 3 },
});
