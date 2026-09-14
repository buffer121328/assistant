import { FormEvent, ReactElement, useEffect, useMemo, useRef, useState } from "react";
import type { AuthenticatedSession, DesktopSettings } from "./api";
import { managementAccess } from "./admin-access";
import { AdminApiClient, AdminItem } from "./admin-api";
import { authErrorMessage, departmentLabel, identityResponsibilityLabel, roleLabel } from "./auth-presentation";
import { AI_OPERATIONS_POLL_INTERVAL_MS, aiOperationsRecommendation } from "./ai-operations";
import { userFacingMessage } from "./presentation";
import { NodeOperationsPanel } from "./NodeOperationsPanel";
import type { DepartmentNode, DiagnosticPackage, NodeRemoteOperation } from "./node-operations";
import jarvisMark from "./assets/jarvis-mark.svg";
import {
  CAPABILITY_REQUEST_STATUSES,
  allowedNextCapabilityRequestStatuses,
  capabilityRequestStatusLabel,
  type CapabilityRequest,
  type CapabilityRequestStatus
} from "./capability-requests";

const DEFAULT_SETTINGS: DesktopSettings = { apiBaseUrl: "http://127.0.0.1:18080", defaultWorkdir: "", defaultModelClass: "standard", approvalPolicy: "ask" };
const MEMBER_ROLES = [["member", "员工"], ["manager", "员工（业务主管）"], ["department_admin", "部门负责人"], ["capability_publisher", "能力发布者"], ["auditor", "审计员"]] as const;
const CATALOG_PAGE_SIZE = 10;
type AdminDashboard = { organizations: AdminItem[]; members: AdminItem[]; capabilities: AdminItem[]; audit: AdminItem[]; capabilityRequests: CapabilityRequest[]; nodes: DepartmentNode[]; diagnosticPackages: DiagnosticPackage[]; nodeOperations: NodeRemoteOperation[] };
type AdminSection = "overview" | "organization" | "capabilities" | "operations" | "audit";
type AdminAction = "bootstrap" | "member" | "role" | "distribute" | "candidate" | "connector" | null;
const emptyDashboard = (): AdminDashboard => ({ organizations: [], members: [], capabilities: [], audit: [], capabilityRequests: [], nodes: [], diagnosticPackages: [], nodeOperations: [] });

const ACTION_TITLES: Record<Exclude<AdminAction, null>, string> = {
  bootstrap: "检查工作区基线",
  member: "添加成员",
  role: "调整成员角色",
  distribute: "分发能力",
  candidate: "登记候选能力",
  connector: "创建连接器定义"
};

/** Present governed skill keys in Chinese; unmapped keys fall back to the raw key. */
const SKILL_LABELS: Record<string, string> = {
  "skill-creator": "技能创作",
  "skill-installer": "技能安装",
  "define-goal": "目标定义",
  "security-best-practices": "安全最佳实践",
  "security-threat-model": "威胁建模",
  "security-ownership-map": "安全权责映射",
  "jupyter-notebook": "Jupyter 笔记本",
  "office-writing": "公文写作",
  "meeting-minutes": "会议纪要",
  "business-email": "商务邮件",
  "progress-report": "进度汇报",
  "proposal-writing": "方案撰写",
  "presentation-briefing": "演示简报",
  "structured-spreadsheet": "结构化表格",
  "pdf": "PDF 处理",
  "speech": "语音合成",
  "transcribe": "语音转写",
  "notion-meeting-intelligence": "Notion 会议智能",
  "notion-knowledge-capture": "Notion 知识收集",
  "figma": "Figma 设计",
  "daily-report": "日报生成",
  "research": "资料调研",
  "structured-planning": "结构化规划",
  "technical-daily-report": "技术日报",
  "technical-document-layout": "技术文档排版",
  "project-progress-notification": "项目进度通知",
  "capability-design": "能力设计",
  "mcp-integration-design": "MCP 集成设计",
  "problem-observation": "问题观察",
  "solution-recommendation": "解决方案建议"
};

function skillLabel(key: string): string {
  return SKILL_LABELS[key] || key;
}

/** Compact stroke icon set so the console reads as one product without extra assets. */
function Icon({ name }: { name: "overview" | "organization" | "capabilities" | "operations" | "audit" | "logout" | "org" | "members" | "spark" | "pending" | "empty" }): ReactElement {
  const shapes: Record<string, ReactElement> = {
    overview: <><rect x="3.2" y="3.2" width="7.2" height="8.6" rx="1.6" /><rect x="13.6" y="3.2" width="7.2" height="5.2" rx="1.6" /><rect x="13.6" y="11.6" width="7.2" height="9.2" rx="1.6" /><rect x="3.2" y="15" width="7.2" height="5.8" rx="1.6" /></>,
    organization: <><circle cx="9.2" cy="7.8" r="3.1" /><path d="M3.6 19.4c.7-3.2 3-4.9 5.6-4.9s4.9 1.7 5.6 4.9" /><circle cx="17" cy="9.4" r="2.3" /><path d="M15.5 18.9c.5-2.3 1.9-3.5 4-3.5 1 0 1.9.3 2.7.9" /></>,
    capabilities: <><path d="M12 3.4l2.5 5.1 5.6.8-4 4 .9 5.6-5-2.7-5 2.7.9-5.6-4-4 5.6-.8z" /></>,
    operations: <><path d="M3 12.4h3.6L9 6.6l4.4 10.8 2.3-5h5.3" /></>,
    audit: <><path d="M6.2 3.4h8.2l3.8 3.8v13.4H6.2z" /><path d="M14.2 3.6v3.8H18" /><path d="M9.4 12.2h5.4M9.4 15.8h5.4" /></>,
    logout: <><path d="M14.4 4.2h5.4v15.6h-5.4" /><path d="M10.4 8.2l-3.8 3.8 3.8 3.8M6.8 12h9" /></>,
    org: <><rect x="4" y="3.6" width="9.4" height="16.8" rx="1.4" /><path d="M13.4 9.8h5.2a1.4 1.4 0 011.4 1.4v9.2" /><path d="M7.4 8h2.6M7.4 12h2.6M7.4 16h2.6" /></>,
    members: <><circle cx="9" cy="8.4" r="3" /><path d="M3.8 19.2c.6-3 2.7-4.6 5.2-4.6s4.6 1.6 5.2 4.6" /><path d="M15.4 6.6a3 3 0 010 5.4M17.6 14.9c1.6.7 2.7 2.1 3.1 4.1" /></>,
    spark: <><path d="M12 3.8c.7 3.9 2.3 5.5 6.2 6.2-3.9.7-5.5 2.3-6.2 6.2-.7-3.9-2.3-5.5-6.2-6.2 3.9-.7 5.5-2.3 6.2-6.2z" /><path d="M18.6 15.4c.3 1.7 1 2.4 2.7 2.7-1.7.3-2.4 1-2.7 2.7-.3-1.7-1-2.4-2.7-2.7 1.7-.3 2.4-1 2.7-2.7z" /></>,
    pending: <><circle cx="12" cy="12" r="8.4" /><path d="M12 7.6V12l3 1.8" /></>,
    empty: <><circle cx="12" cy="12" r="8.2" strokeDasharray="3.4 3.4" /><path d="M8.8 12h6.4" /></>
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}

/** Bounded timestamp rendering; falls back to the raw server value when unparseable. */
function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 6) return "夜深了";
  if (hour < 12) return "上午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

/** Keep organisation governance deliberate and session-bound rather than adding an employee-side admin mode. */
export function AdminApp(): ReactElement {
  const [settings, setSettings] = useState<DesktopSettings>(DEFAULT_SETTINGS);
  const [session, setSession] = useState<AuthenticatedSession | null>(null);
  const [initializationRequired, setInitializationRequired] = useState(false);
  const [state, setState] = useState<"checking" | "signed_out" | "authenticated">("checking");
  const [dashboard, setDashboard] = useState<AdminDashboard>(emptyDashboard);
  const [status, setStatus] = useState("正在确认管理身份…");
  const [busy, setBusy] = useState(false);
  const [activeSection, setActiveSection] = useState<AdminSection>("overview");
  const [activeAction, setActiveAction] = useState<AdminAction>(null);
  const [displayName, setDisplayName] = useState("");
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "recovery">("login");
  const [recoveryQuestion, setRecoveryQuestion] = useState<string | null>(null);
  const [recoveryAnswer, setRecoveryAnswer] = useState("");
  const [recoveryPassword, setRecoveryPassword] = useState("");
  const [memberName, setMemberName] = useState("");
  const [memberLogin, setMemberLogin] = useState("");
  const [memberPassword, setMemberPassword] = useState("");
  const [memberOrganizationId, setMemberOrganizationId] = useState("");
  const [memberRole, setMemberRole] = useState("member");
  const [selectedMemberId, setSelectedMemberId] = useState("");
  const [selectedCapabilityVersionId, setSelectedCapabilityVersionId] = useState("");
  const [distributionOrganizationId, setDistributionOrganizationId] = useState("");
  const [candidateKey, setCandidateKey] = useState("");
  const [candidateDisplayName, setCandidateDisplayName] = useState("");
  const [candidateSummary, setCandidateSummary] = useState("");
  const [connectorKey, setConnectorKey] = useState("");
  const [connectorType, setConnectorType] = useState("MCP");
  const [connectorDisplayName, setConnectorDisplayName] = useState("");
  const [requestStatusFilter, setRequestStatusFilter] = useState<"all" | CapabilityRequestStatus>("all");
  const [capabilityRequestsUpdatedAt, setCapabilityRequestsUpdatedAt] = useState<string | null>(null);
  const [capabilityDepartmentId, setCapabilityDepartmentId] = useState("");
  const [departmentCapabilities, setDepartmentCapabilities] = useState<AdminItem[] | null>(null);
  const [departmentCapabilitiesLoading, setDepartmentCapabilitiesLoading] = useState(false);
  const [capabilityPage, setCapabilityPage] = useState(0);
  const refreshRevision = useRef(0);
  const api = useMemo(() => new AdminApiClient(settings.apiBaseUrl), [settings.apiBaseUrl]);
  const access = useMemo(
    () => managementAccess(session?.roles ?? [], session?.organization_names ?? []),
    [session]
  );
  const departments = dashboard.organizations.filter((item) => item.type === "department" && item.status === "active");
  const visibleCapabilityRequests = requestStatusFilter === "all"
    ? dashboard.capabilityRequests
    : dashboard.capabilityRequests.filter((request) => request.status === requestStatusFilter);
  const capabilityDepartmentName = departments.find((item) => String(item.id) === capabilityDepartmentId)?.name;
  const activeCapabilityList = capabilityDepartmentId ? departmentCapabilities ?? [] : dashboard.capabilities;
  const capabilityTotalPages = Math.max(1, Math.ceil(activeCapabilityList.length / CATALOG_PAGE_SIZE));
  const safeCapabilityPage = Math.min(capabilityPage, capabilityTotalPages - 1);
  const pagedCapabilities = activeCapabilityList.slice(safeCapabilityPage * CATALOG_PAGE_SIZE, (safeCapabilityPage + 1) * CATALOG_PAGE_SIZE);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const persisted = await window.assistantDesktop.loadSettings();
        const next = { ...DEFAULT_SETTINGS, ...persisted };
        const auth = await window.assistantDesktop.authState(next.apiBaseUrl);
        if (cancelled) return;
        setSettings(next); setSession(auth.session); setInitializationRequired(auth.initializationRequired);
        if (!auth.session && !auth.initializationRequired) {
          const recovery = await window.assistantDesktop.recoveryStatus(next.apiBaseUrl);
          if (recovery.status >= 200 && recovery.body && typeof recovery.body === "object") {
            const body = recovery.body as { enabled?: boolean; question?: string | null };
            if (body.enabled && body.question) setRecoveryQuestion(body.question);
          }
        }
        setState(auth.session ? "authenticated" : "signed_out");
        setStatus(auth.session ? "已确认当前服务器身份。" : "请登录后进入管理台。");
      } catch (reason) {
        if (!cancelled) { setState("signed_out"); setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function refreshDashboard(): Promise<void> {
    if (!session || !access.enterWorkbench) return;
    const revision = ++refreshRevision.current;
    setBusy(true);
    setDashboard(emptyDashboard());
    try {
      const empty = { items: [] };
      const [organizations, members, capabilities, audit, capabilityRequests, nodes, diagnosticPackages, nodeOperations] = await Promise.all([
        access.readOrganizations ? api.organizations() : Promise.resolve(empty),
        access.readMembers ? api.members() : Promise.resolve(empty),
        access.readCapabilities ? api.capabilities() : Promise.resolve(empty),
        access.readAudit ? api.audit() : Promise.resolve(empty),
        access.readCapabilityRequests ? api.capabilityRequests() : Promise.resolve({ items: [] as CapabilityRequest[] }),
        access.readNodes ? api.nodes() : Promise.resolve({ items: [] as DepartmentNode[] }),
        access.readDiagnostics ? api.diagnosticPackages() : Promise.resolve({ items: [] as DiagnosticPackage[] }),
        access.manageNodeOperations ? api.nodeOperations() : Promise.resolve({ items: [] as NodeRemoteOperation[] })
      ]);
      if (revision !== refreshRevision.current) return;
      const next = { organizations: organizations.items, members: members.items, capabilities: capabilities.items, audit: audit.items, capabilityRequests: capabilityRequests.items, nodes: nodes.items, diagnosticPackages: diagnosticPackages.items, nodeOperations: nodeOperations.items };
      setDashboard(next);
      setCapabilityRequestsUpdatedAt(new Date().toISOString());
      const firstDepartment = next.organizations.find((item) => item.type === "department")?.id;
      setMemberOrganizationId(String(firstDepartment || ""));
      setDistributionOrganizationId(String(firstDepartment || ""));
      setSelectedMemberId("");
      setSelectedCapabilityVersionId(String(next.capabilities[0]?.capability_version_id || ""));
      setStatus("管理数据已从服务端刷新。");
    } catch (reason) {
      if (revision !== refreshRevision.current) return;
      setDashboard(emptyDashboard());
      setStatus(userFacingMessage(reason instanceof Error ? reason.message : ""));
    }
    finally { if (revision === refreshRevision.current) setBusy(false); }
  }

  useEffect(() => {
    refreshRevision.current += 1;
    setDashboard(emptyDashboard());
    if (state === "authenticated" && access.enterWorkbench) void refreshDashboard();
  }, [state, session?.user_id, session?.authority_revision, access.enterWorkbench]);

  useEffect(() => {
    if (state !== "authenticated" || !access.readCapabilityRequests) return;
    const timer = window.setInterval(() => {
      void api.capabilityRequests().then((response) => {
        setDashboard((current) => ({ ...current, capabilityRequests: response.items }));
        setCapabilityRequestsUpdatedAt(new Date().toISOString());
      }).catch(() => {
        // Keep the last safe queue visible; the next bounded poll remains retryable.
      });
    }, AI_OPERATIONS_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [state, session?.user_id, session?.authority_revision, access.readCapabilityRequests, api]);

  // The department scope view loads grants on demand and never reuses a stale selection.
  useEffect(() => {
    setCapabilityPage(0);
    if (!capabilityDepartmentId || !access.readOrganizationCapabilities) {
      setDepartmentCapabilities(null);
      return;
    }
    let cancelled = false;
    setDepartmentCapabilitiesLoading(true);
    void api.organizationCapabilities(capabilityDepartmentId)
      .then((response) => { if (!cancelled) setDepartmentCapabilities(response.items); })
      .catch((reason) => {
        if (!cancelled) {
          setDepartmentCapabilities(null);
          setStatus(userFacingMessage(reason instanceof Error ? reason.message : ""));
        }
      })
      .finally(() => { if (!cancelled) setDepartmentCapabilitiesLoading(false); });
    return () => { cancelled = true; };
  }, [capabilityDepartmentId, access.readOrganizationCapabilities, api]);

  async function authenticate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setBusy(true);
    try {
      const response = initializationRequired
        ? await window.assistantDesktop.initialize({ baseUrl: settings.apiBaseUrl, displayName: displayName.trim(), loginName: loginName.trim(), password })
        : await window.assistantDesktop.login({ baseUrl: settings.apiBaseUrl, loginName: loginName.trim(), password });
      if (response.status < 200 || response.status >= 300 || !response.body || typeof response.body !== "object") throw new Error(authErrorMessage(response.body) || "管理登录失败。");
      setSession(response.body as AuthenticatedSession); setInitializationRequired(false); setState("authenticated"); setPassword(""); setStatus("已登录；正在检查管理角色。");
    } catch (reason) { setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
    finally { setBusy(false); }
  }

  async function recover(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); setBusy(true);
    try {
      const response = await window.assistantDesktop.recoverPassword({ baseUrl: settings.apiBaseUrl, loginName: loginName.trim(), answer: recoveryAnswer, newPassword: recoveryPassword });
      if (response.status < 200 || response.status >= 300 || !response.body || typeof response.body !== "object") throw new Error(authErrorMessage(response.body) || "密码恢复失败。");
      setSession(response.body as AuthenticatedSession); setState("authenticated"); setRecoveryAnswer(""); setRecoveryPassword(""); setStatus("密码已更新，旧会话已失效。正在进入管理台。");
    } catch (reason) { setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
    finally { setBusy(false); }
  }

  async function logout(): Promise<void> {
    refreshRevision.current += 1;
    await window.assistantDesktop.logout(settings.apiBaseUrl);
    setSession(null); setDashboard(emptyDashboard()); setState("signed_out"); setStatus("已退出管理台。");
  }

  async function bootstrap(): Promise<void> {
    setBusy(true);
    try { await api.bootstrapDepartments(); await refreshDashboard(); setStatus("AI 部 Skill 治理、财务部办公协作与市场部内容生产能力已补齐。"); }
    catch (reason) { setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
    finally { setBusy(false); }
  }

  async function provision(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); if (!memberName.trim() || !memberLogin.trim() || !memberPassword || !memberOrganizationId) return; setBusy(true);
    try {
      await api.provisionMember({ display_name: memberName.trim(), login_name: memberLogin.trim(), password: memberPassword, organization_id: memberOrganizationId, role: memberRole });
      setMemberName(""); setMemberLogin(""); setMemberPassword(""); await refreshDashboard(); setStatus("成员已创建并分配到部门；密码未在界面或审计中保留。");
    } catch (reason) { setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
    finally { setBusy(false); }
  }

  async function assignRole(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); if (!selectedMemberId || !memberOrganizationId) return; setBusy(true);
    try { await api.assignMemberRole(selectedMemberId, { organization_id: memberOrganizationId, role: memberRole }); await refreshDashboard(); setStatus("成员角色已按服务端治理规则更新。"); }
    catch (reason) { setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
    finally { setBusy(false); }
  }

  async function distribute(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault(); if (!selectedCapabilityVersionId || !distributionOrganizationId) return; setBusy(true);
    try { await api.distributeCapability(selectedCapabilityVersionId, { organization_id: distributionOrganizationId }); await refreshDashboard(); setStatus("能力分发已提交；运行时仍会重新执行策略与审批检查。"); }
    catch (reason) { setStatus(userFacingMessage(reason instanceof Error ? reason.message : "")); }
    finally { setBusy(false); }
  }

  async function recordCandidate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!candidateKey.trim() || !candidateDisplayName.trim() || !candidateSummary.trim()) return;
    setBusy(true);
    try {
      await api.recordSkillCandidate({
        key: candidateKey.trim(),
        display_name: candidateDisplayName.trim(),
        summary: candidateSummary.trim()
      });
      setCandidateKey("");
      setCandidateDisplayName("");
      setCandidateSummary("");
      setStatus("候选能力已登记为停用状态，仍需完成评审后才能启用。");
    } catch (reason) {
      setStatus(userFacingMessage(reason instanceof Error ? reason.message : ""));
    } finally {
      setBusy(false);
    }
  }

  async function updateCapabilityRequestStatus(
    requestId: string,
    nextStatus: CapabilityRequestStatus
  ): Promise<void> {
    setBusy(true);
    try {
      await api.updateCapabilityRequestStatus(requestId, nextStatus);
      await refreshDashboard();
      setStatus(`能力申请已更新为“${capabilityRequestStatusLabel(nextStatus)}”。`);
    } catch (reason) {
      setStatus(userFacingMessage(reason instanceof Error ? reason.message : ""));
    } finally {
      setBusy(false);
    }
  }

  async function createConnector(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!connectorKey.trim() || !connectorType.trim() || !connectorDisplayName.trim()) return;
    setBusy(true);
    try {
      await api.createConnector({
        key: connectorKey.trim(),
        connector_type: connectorType.trim(),
        display_name: connectorDisplayName.trim()
      });
      setConnectorKey("");
      setConnectorDisplayName("");
      await refreshDashboard();
      setStatus("连接器定义已成功创建。");
    } catch (reason) {
      setStatus(userFacingMessage(reason instanceof Error ? reason.message : ""));
    } finally {
      setBusy(false);
    }
  }

  if (state === "checking") {
    return <main className="admin-root admin-boot">
      <span className="admin-boot-mark"><img src={jarvisMark} alt="" /></span>
      <p role="status">正在建立安全连接…</p>
    </main>;
  }

  if (!session) {
    return <main className="admin-root admin-auth">
      <section className="admin-auth-brand">
        <header className="admin-brand-lockup"><img src={jarvisMark} alt="" /><span>JARVIS</span><small>企业控制台</small></header>
        <div className="admin-brand-body">
          <p className="admin-brand-kicker">企业级智能协作控制面</p>
          <h1>让能力被看见，<br />让权限始终可控。</h1>
          <p className="admin-brand-lede">统一管理组织、成员、Agent 能力与审计记录。所有关键操作均由服务端权限策略确认。</p>
          <ul>
            <li><strong>身份隔离</strong><span>会话与管理范围由服务端校验</span></li>
            <li><strong>能力治理</strong><span>分发、审批和连接器统一管理</span></li>
            <li><strong>完整审计</strong><span>关键变更保留可追踪记录</span></li>
          </ul>
        </div>
        <footer><small>JARVIS Assistant · Enterprise Control Plane</small></footer>
      </section>
      <section className="admin-auth-panel">
        <div className="admin-auth-heading">
          <span>{initializationRequired ? "首次设置" : authMode === "recovery" ? "紧急恢复" : "安全登录"}</span>
          <h2>{initializationRequired ? "创建企业管理员" : authMode === "recovery" ? "重置管理员密码" : "欢迎回来"}</h2>
          <p>{initializationRequired
            ? "为当前工作区设置首位管理员。完成后即可配置组织与成员。"
            : authMode === "recovery"
              ? "仅用于服务器已配置的紧急恢复凭据；成功后凭据立即失效。"
              : "使用企业管理账号继续访问控制台。"}</p>
        </div>
        {authMode === "recovery" && !initializationRequired ? <form onSubmit={(event) => void recover(event)}>
          <label>登录名
            <input autoComplete="username" value={loginName} onChange={(event) => setLoginName(event.target.value)} placeholder="输入登录名" required />
          </label>
          <label>{recoveryQuestion || "恢复问题"}
            <input autoComplete="off" value={recoveryAnswer} onChange={(event) => setRecoveryAnswer(event.target.value)} placeholder="输入答案" required />
          </label>
          <label>新密码<span>至少 12 位字符</span>
            <input autoComplete="new-password" type="password" value={recoveryPassword} onChange={(event) => setRecoveryPassword(event.target.value)} minLength={12} required />
          </label>
          <button className="admin-auth-submit" type="submit" disabled={busy}>{busy ? "正在恢复…" : "设置新密码并登录"}</button>
        </form> : <form onSubmit={(event) => void authenticate(event)}>
          {initializationRequired ? <label>管理员显示名称<span>用于管理台身份与审计记录，不作为登录凭据</span>
            <input autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：系统管理员" required />
          </label> : null}
          <label>登录名
            <input autoComplete="username" value={loginName} onChange={(event) => setLoginName(event.target.value)} placeholder="输入登录名" required />
          </label>
          <label>密码
            <input autoComplete={initializationRequired ? "new-password" : "current-password"} type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={initializationRequired ? "至少 12 位字符" : "输入密码"} minLength={12} required />
          </label>
          <button className="admin-auth-submit" type="submit" disabled={busy}>{busy ? "正在验证…" : initializationRequired ? "创建管理员并进入控制台" : "进入控制台"}</button>
        </form>}
        {!initializationRequired && recoveryQuestion ? <button className="admin-auth-link" type="button" onClick={() => { setAuthMode(authMode === "login" ? "recovery" : "login"); setStatus(""); }}>{authMode === "login" ? "无法登录？使用紧急密码恢复" : "返回普通登录"}</button> : null}
        <p className="admin-auth-status" role="status">{status}</p>
        <p className="admin-auth-security">受保护的管理入口 · 会话仅在当前浏览器标签页有效</p>
      </section>
    </main>;
  }

  if (!access.enterWorkbench) {
    return <main className="admin-root admin-shell">
      <section className="admin-denied">
        <p className="admin-denied-kicker">受治理的管理入口</p>
        <h1>当前身份没有管理权限</h1>
        <p className="admin-denied-identity"><strong>{session.display_name}</strong> · {departmentLabel(session)} · {roleLabel(session.roles)} <span className="admin-pill admin-pill-neutral">{identityResponsibilityLabel(session.roles)}</span></p>
        <p>当前身份不会请求或显示组织、成员、能力目录、连接器或审计数据。</p>
        <div className="admin-denied-actions"><button className="secondary-button" type="button" onClick={() => void logout()}>退出</button></div>
        <p className="admin-denied-status" role="status">{status}</p>
      </section>
    </main>;
  }

  const sectionMeta: Record<AdminSection, { title: string; description: string }> = {
    overview: { title: "工作区总览", description: "查看当前管理范围、系统健康与待处理事项。" },
    organization: { title: "组织与成员", description: "管理工作区内的部门、成员和角色边界。" },
    capabilities: { title: "能力中心", description: "管理已批准能力、改进队列与受治理的集成定义。" },
    operations: { title: "运行与诊断", description: "查看节点健康、配置状态和受控远程操作。" },
    audit: { title: "审计记录", description: "浏览服务端留存的治理事件和执行结果。" }
  };
  const currentPage = sectionMeta[activeSection];
  const pendingRequests = dashboard.capabilityRequests.filter((item) => item.status !== "closed" && item.status !== "rejected").length;

  return <main className="admin-root admin-console">
    <aside className="admin-sidenav">
      <button className="admin-wordmark" type="button" onClick={() => setActiveSection("overview")} aria-label="返回工作区总览">
        <img src={jarvisMark} alt="" />
        <span>JARVIS</span>
        <small>企业控制台</small>
      </button>
      <nav className="admin-sidenav-nav" aria-label="管理模块">
        <button className={activeSection === "overview" ? "active" : ""} onClick={() => setActiveSection("overview")}><Icon name="overview" />总览</button>
        {(access.readOrganizations || access.readMembers) ? <button className={activeSection === "organization" ? "active" : ""} onClick={() => setActiveSection("organization")}><Icon name="organization" />组织与成员</button> : null}
        {(access.readCapabilities || access.recordSkillCandidates || access.manageConnectors || access.readCapabilityRequests) ? <button className={activeSection === "capabilities" ? "active" : ""} onClick={() => setActiveSection("capabilities")}><Icon name="capabilities" />能力中心</button> : null}
        {(access.readNodes || access.readDiagnostics || access.manageNodeOperations) ? <button className={activeSection === "operations" ? "active" : ""} onClick={() => setActiveSection("operations")}><Icon name="operations" />运行与诊断</button> : null}
        {access.readAudit ? <button className={activeSection === "audit" ? "active" : ""} onClick={() => setActiveSection("audit")}><Icon name="audit" />审计</button> : null}
      </nav>
      <div className="admin-sidenav-footer">
        <div className="admin-sidenav-user">
          <span className="admin-avatar">{session.display_name.slice(0, 1)}</span>
          <div><strong>{session.display_name}</strong><small>{roleLabel(session.roles)}</small></div>
        </div>
        <button className="admin-sidenav-logout" type="button" aria-label="退出登录" onClick={() => void logout()}>
          <Icon name="logout" />退出登录
        </button>
      </div>
    </aside>

    <div className="admin-main">
      <header className="admin-page-heading">
        <div>
          <h1>{currentPage.title}</h1>
          <p>{currentPage.description}</p>
        </div>
        <div className="admin-page-controls">
          <button className="secondary-button" type="button" onClick={() => void refreshDashboard()} disabled={busy}>{busy ? "同步中…" : "刷新数据"}</button>
          {activeSection === "organization" && access.manageMembers ? <button className="primary-button" type="button" onClick={() => setActiveAction("member")}>添加成员</button> : null}
          {activeSection === "capabilities" && access.distributeCapabilities ? <button className="primary-button" type="button" onClick={() => setActiveAction("distribute")}>分发能力</button> : null}
        </div>
      </header>

      <div className="admin-statusbar">
        <span className={busy ? "loading" : "ready"} />
        <p role="status">{status}</p>
        <small>{capabilityRequestsUpdatedAt ? `同步于 ${new Date(capabilityRequestsUpdatedAt).toLocaleTimeString("zh-CN", { hour12: false })}` : "等待首次同步"}</small>
      </div>

      {activeSection === "overview" ? <>
        <section className="overview-hero">
          <div className="overview-hero-greeting">
            <h2>{greeting()}，{session.display_name}</h2>
            <p>{departmentLabel(session)} · {roleLabel(session.roles)} <span className="admin-pill admin-pill-blue">{identityResponsibilityLabel(session.roles)}</span></p>
          </div>
          <div className="overview-hero-scope">
            <span>当前工作区</span>
            <strong>本地工作区</strong>
            <small>服务器级治理范围</small>
          </div>
        </section>
        <section className="metric-grid" aria-label="管理概览">
          <MetricCard icon="org" label="部门与组织" value={dashboard.organizations.length} detail="当前管理范围" tone="blue" />
          <MetricCard icon="members" label="已授权成员" value={dashboard.members.length} detail="在角色边界内" tone="violet" />
          <MetricCard icon="spark" label="可用能力" value={dashboard.capabilities.length} detail="已批准版本" tone="green" />
          <MetricCard icon="pending" label="待处理事项" value={pendingRequests} detail="能力改进申请" tone="amber" />
        </section>
        <section className="overview-layout">
          <article className="surface-card">
            <div className="surface-heading"><div><h2>常用操作</h2><p>从这里开始最常见的治理动作。</p></div></div>
            <div className="quick-action-grid">
              {access.bootstrapDepartments ? <button type="button" onClick={() => setActiveAction("bootstrap")}><b>01</b><span><strong>检查工作区基线</strong><small>补齐部门与已批准能力</small></span><i>→</i></button> : null}
              {access.manageMembers ? <button type="button" onClick={() => setActiveAction("member")}><b>02</b><span><strong>添加团队成员</strong><small>创建账号并分配角色</small></span><i>→</i></button> : null}
              {access.distributeCapabilities ? <button type="button" onClick={() => { setActiveSection("capabilities"); setActiveAction("distribute"); }}><b>03</b><span><strong>分发能力</strong><small>让部门获得已批准能力</small></span><i>→</i></button> : null}
            </div>
          </article>
          <article className="surface-card">
            <div className="surface-heading"><div><h2>最近治理事件</h2></div><button className="link-button" type="button" onClick={() => setActiveSection("audit")}>查看审计</button></div>
            {dashboard.audit.length ? <div className="activity-list">
              {dashboard.audit.slice(0, 5).map((entry) => (
                <div key={String(entry.id)}>
                  <i className={String(entry.policy_decision) === "ALLOW" ? "positive" : "neutral"} />
                  <p><strong>{String(entry.result_status)}</strong><span>{formatTimestamp(String(entry.created_at))}</span></p>
                </div>
              ))}
            </div> : <EmptyState title="暂时没有可显示的治理事件" detail="新产生的管理操作会在服务端审计后出现在这里。" />}
          </article>
        </section>
      </> : null}

      {activeSection === "organization" ? <div className="module-stack">
        <section className="surface-card">
          <div className="surface-heading">
            <div><h2>组织架构</h2><p>“本地工作区”是当前服务器的根管理范围；部门和成员在其下按权限受治理。</p></div>
            <span className="count-badge">{dashboard.organizations.length} 个组织</span>
          </div>
          {dashboard.organizations.length ? <div className="org-grid">
            {dashboard.organizations.map((organization) => (
              <article className="org-card" key={String(organization.id)}>
                <span className="org-card-icon"><Icon name="org" /></span>
                <div><strong>{organization.type === "department" ? String(organization.name) : "本地工作区"}</strong><span>{organization.type === "department" ? "部门" : "根管理范围"}</span></div>
              </article>
            ))}
          </div> : <EmptyState title="工作区还没有组织结构" detail="完成基线初始化后，会在这里显示可管理的部门。" />}
        </section>
        <section className="surface-card">
          <div className="surface-heading">
            <div><h2>成员与角色</h2><p>角色决定成员在当前工作区可查看和可执行的管理范围。</p></div>
            {access.manageMembers ? <div className="inline-actions">
              <button className="secondary-button" type="button" onClick={() => setActiveAction("role")}>调整角色</button>
              <button className="primary-button" type="button" onClick={() => setActiveAction("member")}>添加成员</button>
            </div> : null}
          </div>
          {dashboard.members.length ? <div className="member-table">
            {dashboard.members.map((member) => (
              <article className="member-row" key={`${String(member.user_id)}-${String(member.organization_id)}`}>
                <span className="admin-avatar">{String(member.display_name).slice(0, 1)}</span>
                <div><strong>{String(member.display_name)}</strong><span>{String(member.organization_name)}</span></div>
                <span className="admin-pill admin-pill-blue">{roleLabel([String(member.role)])}</span>
              </article>
            ))}
          </div> : <EmptyState title="还没有成员" detail="添加成员后，即可在这里查看其角色和部门范围。" />}
        </section>
      </div> : null}

      {activeSection === "capabilities" ? <div className="module-stack">
        <section className="surface-card">
          <div className="surface-heading">
            <div>
              <h2>{capabilityDepartmentName ? `「${capabilityDepartmentName}」已有能力` : "能力目录"}</h2>
              <p>{capabilityDepartmentName
                ? "以下是该部门当前已分发启用的工作能力，以服务端分发记录为准。"
                : "这里展示已经过治理流程批准、可以按部门分发的工作能力。"}</p>
            </div>
            <div className="inline-actions">
              {access.readOrganizationCapabilities ? <select
                aria-label="按部门查看已有能力"
                value={capabilityDepartmentId}
                onChange={(event) => setCapabilityDepartmentId(event.target.value)}
              >
                <option value="">能力目录（全部）</option>
                {departments.map((department) => <option key={String(department.id)} value={String(department.id)}>{String(department.name)}</option>)}
              </select> : null}
              {access.distributeCapabilities ? <button className="primary-button" type="button" onClick={() => setActiveAction("distribute")}>分发能力</button> : null}
            </div>
          </div>
          {capabilityDepartmentId && departmentCapabilitiesLoading ? <p className="admin-loading-hint">正在加载该部门的能力…</p>
            : activeCapabilityList.length ? <>
              <div className="capability-list">
                {pagedCapabilities.map((capability) => (
                  <CapabilityRow
                    key={String(capability.grant_id ?? capability.capability_version_id ?? capability.capability_id)}
                    title={String(capability.display_name)}
                    version={String(capability.version)}
                    summary={String(capability.summary || "暂无说明")}
                    skills={Array.isArray(capability.skills) ? capability.skills.map(String) : []}
                    orgs={capabilityDepartmentId ? undefined : Array.isArray(capability.departments) ? capability.departments.map(String) : []}
                    meta={capabilityDepartmentId ? `分发于 ${formatTimestamp(String(capability.distributed_at))}` : undefined}
                  />
                ))}
              </div>
              {capabilityTotalPages > 1 ? <nav className="table-pager" aria-label="能力分页">
                <button className="secondary-button" type="button" disabled={safeCapabilityPage === 0} onClick={() => setCapabilityPage(safeCapabilityPage - 1)}>上一页</button>
                <span>第 {safeCapabilityPage + 1} / {capabilityTotalPages} 页 · 共 {activeCapabilityList.length} 条</span>
                <button className="secondary-button" type="button" disabled={safeCapabilityPage >= capabilityTotalPages - 1} onClick={() => setCapabilityPage(safeCapabilityPage + 1)}>下一页</button>
              </nav> : null}
            </>
            : <EmptyState
              title={capabilityDepartmentId ? "该部门还没有已分发的能力" : "还没有已批准的能力"}
              detail={capabilityDepartmentId
                ? "通过“分发能力”把已批准版本下发给该部门后，会在这里显示。"
                : "完成能力评审后，已批准版本会显示在这里。"}
            />}
        </section>
        {access.triageCapabilityRequests ? <section className="surface-card request-queue-card">
          <div className="surface-heading">
            <div><h2>能力改进队列</h2><p>仅显示员工明确提交的改进请求，不会自动读取私人对话。</p></div>
            <select aria-label="按状态筛选" value={requestStatusFilter} onChange={(event) => setRequestStatusFilter(event.target.value as "all" | CapabilityRequestStatus)}>
              <option value="all">全部状态</option>
              {CAPABILITY_REQUEST_STATUSES.map((item) => <option key={item} value={item}>{capabilityRequestStatusLabel(item)}</option>)}
            </select>
          </div>
          {visibleCapabilityRequests.length ? <div className="request-list">
            {visibleCapabilityRequests.map((request) => {
              const recommendation = aiOperationsRecommendation(request);
              return <article key={request.id}>
                <div>
                  <span className={`admin-pill request-status status-${request.status}`}>{capabilityRequestStatusLabel(request.status)}</span>
                  <h3>{request.title}</h3>
                  <p>{request.description}</p>
                  <small>{request.requester_display_name || "员工"} · {request.organization_name} · {formatTimestamp(request.updated_at)}</small>
                </div>
                <aside>
                  <strong>{recommendation.title}</strong>
                  <p>{recommendation.nextAction}</p>
                  {allowedNextCapabilityRequestStatuses(request.status).map((nextStatus) => (
                    <button key={nextStatus} type="button" disabled={busy} onClick={() => void updateCapabilityRequestStatus(request.id, nextStatus)}>转为{capabilityRequestStatusLabel(nextStatus)}</button>
                  ))}
                </aside>
              </article>;
            })}
          </div> : <EmptyState title="当前没有需要处理的能力请求" detail="新的员工请求会在这里按照服务端状态出现。" />}
        </section> : null}
        {(access.recordSkillCandidates || access.manageConnectors) ? <section className="surface-card">
          <div className="surface-heading">
            <div><h2>治理工具</h2><p>登记候选能力或连接器定义；不会收集凭证或自动启用任何内容。</p></div>
          </div>
          <div className="tool-grid">
            {access.recordSkillCandidates ? <button className="tool-card" type="button" onClick={() => setActiveAction("candidate")}><b>＋</b><span><strong>登记候选能力</strong><small>以待评审状态记录受控元数据</small></span></button> : null}
            {access.manageConnectors ? <button className="tool-card" type="button" onClick={() => setActiveAction("connector")}><b>↗</b><span><strong>创建连接器定义</strong><small>建立后续评审所需的受治理定义</small></span></button> : null}
          </div>
        </section> : null}
      </div> : null}

      {activeSection === "operations" ? <div className="operations-page">
        <NodeOperationsPanel api={api} roles={session.roles} access={access} organizations={dashboard.organizations} nodes={dashboard.nodes} packages={dashboard.diagnosticPackages} operations={dashboard.nodeOperations} busy={busy} onRefresh={refreshDashboard} onStatus={setStatus} />
      </div> : null}

      {activeSection === "audit" ? <section className="surface-card">
        <div className="surface-heading">
          <div><h2>最近审计记录</h2><p>记录的是服务端治理事实，而不是浏览器侧调试日志。</p></div>
          <span className="count-badge">{dashboard.audit.length} 条记录</span>
        </div>
        {dashboard.audit.length ? <div className="audit-timeline">
          {dashboard.audit.map((entry) => (
            <article key={String(entry.id)}>
              <i className={String(entry.policy_decision) === "ALLOW" ? "positive" : "neutral"} />
              <div><strong>{String(entry.result_status)}</strong><p>{String(entry.policy_decision)} · {String(entry.resource_type || "治理事件")}</p></div>
              <time>{formatTimestamp(String(entry.created_at))}</time>
            </article>
          ))}
        </div> : <EmptyState title="暂无审计记录" detail="服务端产生可展示的治理事件后，会在这里出现。" />}
      </section> : null}
    </div>

    {activeAction ? <div className="action-layer" role="dialog" aria-modal="true" aria-label="管理操作">
      <div className="action-panel">
        <header>
          <div><h2>{ACTION_TITLES[activeAction]}</h2></div>
          <button type="button" aria-label="关闭" onClick={() => setActiveAction(null)}>×</button>
        </header>
        {activeAction === "bootstrap" ? <>
          <p>该操作会幂等检查并补齐预置部门及其能力基线，不会创建示例账号。</p>
          <div className="action-panel-footer">
            <button className="secondary-button" type="button" onClick={() => setActiveAction(null)}>取消</button>
            <button className="primary-button" type="button" disabled={busy} onClick={() => { setActiveAction(null); void bootstrap(); }}>开始检查</button>
          </div>
        </> : null}
        {activeAction === "member" ? <form onSubmit={(event) => { void provision(event); setActiveAction(null); }}>
          <label>显示名称<input value={memberName} onChange={(event) => setMemberName(event.target.value)} required /></label>
          <label>登录名<input value={memberLogin} onChange={(event) => setMemberLogin(event.target.value)} required /></label>
          <label>初始密码<input type="password" value={memberPassword} onChange={(event) => setMemberPassword(event.target.value)} minLength={12} required /></label>
          <DepartmentSelect value={memberOrganizationId} onChange={setMemberOrganizationId} departments={departments} />
          <RoleSelect value={memberRole} onChange={setMemberRole} />
          <div className="action-panel-footer">
            <button className="secondary-button" type="button" onClick={() => setActiveAction(null)}>取消</button>
            <button className="primary-button" type="submit" disabled={busy}>创建成员</button>
          </div>
        </form> : null}
        {activeAction === "role" ? <form onSubmit={(event) => { void assignRole(event); setActiveAction(null); }}>
          <label>成员
            <select value={selectedMemberId} onChange={(event) => setSelectedMemberId(event.target.value)} required>
              <option value="">请选择成员</option>
              {dashboard.members.map((member) => <option key={String(member.user_id)} value={String(member.user_id)}>{String(member.display_name)} · {String(member.organization_name)}</option>)}
            </select>
          </label>
          <DepartmentSelect value={memberOrganizationId} onChange={setMemberOrganizationId} departments={departments} />
          <RoleSelect value={memberRole} onChange={setMemberRole} />
          <div className="action-panel-footer">
            <button className="secondary-button" type="button" onClick={() => setActiveAction(null)}>取消</button>
            <button className="primary-button" type="submit" disabled={busy}>保存角色</button>
          </div>
        </form> : null}
        {activeAction === "distribute" ? <form onSubmit={(event) => { void distribute(event); setActiveAction(null); }}>
          <label>能力版本
            <select value={selectedCapabilityVersionId} onChange={(event) => setSelectedCapabilityVersionId(event.target.value)} required>
              <option value="">请选择能力</option>
              {dashboard.capabilities.map((capability) => <option key={String(capability.capability_version_id)} value={String(capability.capability_version_id)}>{String(capability.display_name)} · {String(capability.version)}</option>)}
            </select>
          </label>
          <DepartmentSelect value={distributionOrganizationId} onChange={setDistributionOrganizationId} departments={departments} />
          <div className="action-panel-footer">
            <button className="secondary-button" type="button" onClick={() => setActiveAction(null)}>取消</button>
            <button className="primary-button" type="submit" disabled={busy}>确认分发</button>
          </div>
        </form> : null}
        {activeAction === "candidate" ? <form onSubmit={(event) => { void recordCandidate(event); setActiveAction(null); }}>
          <label>候选标识<input value={candidateKey} onChange={(event) => setCandidateKey(event.target.value)} placeholder="例如：finance-review" required /></label>
          <label>显示名称<input value={candidateDisplayName} onChange={(event) => setCandidateDisplayName(event.target.value)} required /></label>
          <label className="full-width">用途说明<textarea value={candidateSummary} onChange={(event) => setCandidateSummary(event.target.value)} required /></label>
          <div className="action-panel-footer">
            <button className="secondary-button" type="button" onClick={() => setActiveAction(null)}>取消</button>
            <button className="primary-button" type="submit" disabled={busy}>登记候选项</button>
          </div>
        </form> : null}
        {activeAction === "connector" ? <form onSubmit={(event) => { void createConnector(event); setActiveAction(null); }}>
          <label>连接器标识<input value={connectorKey} onChange={(event) => setConnectorKey(event.target.value)} placeholder="例如：github" required /></label>
          <label>连接器类型
            <select value={connectorType} onChange={(event) => setConnectorType(event.target.value)}>
              <option value="MCP">MCP</option>
              <option value="REST">REST</option>
              <option value="FEISHU">飞书</option>
            </select>
          </label>
          <label>显示名称<input value={connectorDisplayName} onChange={(event) => setConnectorDisplayName(event.target.value)} required /></label>
          <div className="action-panel-footer">
            <button className="secondary-button" type="button" onClick={() => setActiveAction(null)}>取消</button>
            <button className="primary-button" type="submit" disabled={busy}>创建定义</button>
          </div>
        </form> : null}
      </div>
    </div> : null}
  </main>;
}

function DepartmentSelect({ value, onChange, departments }: { value: string; onChange: (value: string) => void; departments: AdminItem[] }): ReactElement {
  return <label>部门
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">请选择部门</option>
      {departments.map((department) => <option key={String(department.id)} value={String(department.id)}>{String(department.name)}</option>)}
    </select>
  </label>;
}

function RoleSelect({ value, onChange }: { value: string; onChange: (value: string) => void }): ReactElement {
  return <label>角色
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {MEMBER_ROLES.map(([role, label]) => <option key={role} value={role}>{label}</option>)}
    </select>
  </label>;
}

function MetricCard({ icon, label, value, detail, tone }: { icon: "org" | "members" | "spark" | "pending"; label: string; value: number; detail: string; tone: "blue" | "violet" | "green" | "amber" }): ReactElement {
  return <article className={`metric-card tone-${tone}`}>
    <span className="metric-icon"><Icon name={icon} /></span>
    <span className="metric-label">{label}</span>
    <strong>{value}</strong>
    <small>{detail}</small>
  </article>;
}

function CapabilityRow({ title, version, summary, skills, orgs, meta }: { title: string; version: string; summary: string; skills: string[]; orgs?: string[]; meta?: string }): ReactElement {
  return <article className="capability-row">
    <span className="capability-row-icon"><Icon name="spark" /></span>
    <div>
      <div className="capability-row-title"><strong>{title}</strong><span className="version-badge">{version}</span></div>
      <p>{summary}</p>
      {(skills.length || orgs || meta) ? <div className="capability-row-meta">
        {skills.map((skill) => <span key={skill} className="admin-chip" title={skill}>{skillLabel(skill)}</span>)}
        {orgs ? <small>{orgs.length ? `已启用部门：${orgs.join("、")}` : "尚未分发给任何部门"}</small> : null}
        {meta ? <small>{meta}</small> : null}
      </div> : null}
    </div>
  </article>;
}

function EmptyState({ title, detail }: { title: string; detail: string }): ReactElement {
  return <div className="admin-empty">
    <span><Icon name="empty" /></span>
    <strong>{title}</strong>
    <p>{detail}</p>
  </div>;
}
