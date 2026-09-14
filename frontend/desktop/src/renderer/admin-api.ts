import type { CapabilityRequest, CapabilityRequestStatus } from "./capability-requests";
import type {
  DepartmentNode,
  DiagnosticPackage,
  DiagnosticRecord,
  NodeOperationType,
  NodeRemoteOperation
} from "./node-operations";

export type AdminItem = Record<string, unknown>;
export type AdminCollection = { items: AdminItem[] };

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const payload = body as { error?: { message?: unknown }; detail?: unknown };
    if (typeof payload.error?.message === "string") return payload.error.message;
    if (typeof payload.detail === "string") return payload.detail;
  }
  return `管理请求失败（${status}）。`;
}

/** Session-bound management client; the Electron main process attaches credentials. */
export class AdminApiClient {
  constructor(private readonly baseUrl: string) {}

  private async request(path: string, options: { method?: string; body?: unknown } = {}): Promise<AdminCollection> {
    const response = await window.assistantDesktop.apiRequest(this.baseUrl, {
      path,
      method: options.method,
      body: options.body
    });
    if (response.status < 200 || response.status >= 300) throw new Error(errorMessage(response.body, response.status));
    return response.body as AdminCollection;
  }

  private async requestItem(path: string, options: { method?: string; body?: unknown } = {}): Promise<AdminItem> {
    const response = await window.assistantDesktop.apiRequest(this.baseUrl, {
      path,
      method: options.method,
      body: options.body
    });
    if (response.status < 200 || response.status >= 300) throw new Error(errorMessage(response.body, response.status));
    return response.body as AdminItem;
  }

  private mutation(path: string, body?: unknown): Promise<AdminCollection> {
    return this.request(path, { method: "POST", body });
  }

  organizations(): Promise<AdminCollection> { return this.request("/api/admin/organizations"); }
  organizationCapabilities(organizationId: string): Promise<AdminCollection> {
    return this.request(`/api/admin/organizations/${encodeURIComponent(organizationId)}/capabilities`);
  }
  members(): Promise<AdminCollection> { return this.request("/api/admin/members"); }
  capabilities(): Promise<AdminCollection> { return this.request("/api/admin/capabilities"); }
  audit(): Promise<AdminCollection> { return this.request("/api/admin/audit"); }
  capabilityRequests(): Promise<{ items: CapabilityRequest[] }> { return this.request("/api/admin/capability-requests") as Promise<{ items: CapabilityRequest[] }>; }
  nodes(): Promise<{ items: DepartmentNode[] }> { return this.request("/api/admin/nodes") as Promise<{ items: DepartmentNode[] }>; }
  diagnosticPackages(): Promise<{ items: DiagnosticPackage[] }> { return this.request("/api/admin/diagnostic-packages") as Promise<{ items: DiagnosticPackage[] }>; }
  nodeOperations(): Promise<{ items: NodeRemoteOperation[] }> { return this.request("/api/admin/node-operations") as Promise<{ items: NodeRemoteOperation[] }>; }
  bootstrapDepartments(): Promise<AdminCollection> { return this.mutation("/api/admin/departments/bootstrap"); }

  /** Create a one-time enrollment; callers must not persist the returned secret. */
  createNodeEnrollment(payload: { organization_id: string; expires_in_minutes: number }): Promise<AdminItem> {
    return this.requestItem("/api/admin/node-enrollments", { method: "POST", body: payload });
  }

  revokeNodeEnrollment(enrollmentId: string): Promise<AdminItem> {
    return this.requestItem(`/api/admin/node-enrollments/${encodeURIComponent(enrollmentId)}/revoke`, { method: "POST" });
  }

  revokeNode(nodeId: string): Promise<AdminCollection> {
    return this.mutation(`/api/admin/nodes/${encodeURIComponent(nodeId)}/revoke`);
  }

  diagnostics(filters: {
    start_at: string;
    end_at: string;
    organization_id?: string;
    node_id?: string;
    status?: string;
  }): Promise<{ items: DiagnosticRecord[] }> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
    return this.request(`/api/admin/diagnostics?${query.toString()}`) as Promise<{ items: DiagnosticRecord[] }>;
  }

  createDiagnosticPackage(payload: {
    start_at: string;
    end_at: string;
    organization_id?: string;
    node_id?: string;
    status?: string;
    expires_in_minutes: number;
  }): Promise<{ items: DiagnosticPackage[] }> {
    return this.mutation("/api/admin/diagnostic-packages", payload) as Promise<{ items: DiagnosticPackage[] }>;
  }

  /** Fetch the already-redacted manifest through the authenticated main-process proxy. */
  downloadDiagnosticPackage(packageId: string): Promise<AdminItem> {
    return this.requestItem(`/api/admin/diagnostic-packages/${encodeURIComponent(packageId)}/download`);
  }

  createNodeOperation(
    nodeId: string,
    payload: { operation_type: NodeOperationType; target_task_id?: string; expires_in_minutes: number }
  ): Promise<{ items: NodeRemoteOperation[] }> {
    return this.mutation(`/api/admin/nodes/${encodeURIComponent(nodeId)}/operations`, payload) as Promise<{ items: NodeRemoteOperation[] }>;
  }

  recordSkillCandidate(payload: { key: string; display_name: string; summary: string }): Promise<AdminCollection> {
    return this.mutation("/api/admin/skill-candidates", payload);
  }

  provisionMember(payload: { display_name: string; login_name: string; password: string; organization_id: string; role: string }): Promise<AdminCollection> {
    return this.mutation("/api/admin/members", payload);
  }

  assignMemberRole(userId: string, payload: { organization_id: string; role: string }): Promise<AdminCollection> {
    return this.request(`/api/admin/members/${encodeURIComponent(userId)}/membership`, { method: "PUT", body: payload });
  }

  distributeCapability(capabilityVersionId: string, payload: { organization_id: string }): Promise<AdminCollection> {
    return this.mutation(`/api/admin/capabilities/${encodeURIComponent(capabilityVersionId)}/distribute`, { ...payload, can_delegate: false });
  }

  createConnector(payload: {
    key: string;
    connector_type: string;
    display_name: string;
    config?: Record<string, unknown>;
  }): Promise<AdminCollection> {
    return this.mutation("/api/admin/connectors", payload);
  }

  updateCapabilityRequestStatus(requestId: string, status: CapabilityRequestStatus): Promise<CapabilityRequest> {
    return this.requestItem(
      `/api/admin/capability-requests/${encodeURIComponent(requestId)}/status`,
      { method: "PATCH", body: { status } }
    ) as Promise<CapabilityRequest>;
  }

  createConnectorInstance(
    connectorId: string,
    payload: {
      organization_id: string;
      auth_mode: string;
      credential_ref: string;
    }
  ): Promise<AdminCollection> {
    return this.mutation(`/api/admin/connectors/${encodeURIComponent(connectorId)}/instances`, payload);
  }
}
