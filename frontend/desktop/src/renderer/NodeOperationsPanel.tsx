import { FormEvent, ReactElement, useMemo, useState } from "react";

import type { AdminApiClient, AdminItem } from "./admin-api";
import type { ManagementAccess } from "./admin-access";
import {
  allowedNodeOperationTypes,
  nodeConnectionLabel,
  nodeOperationLabel,
  type DepartmentNode,
  type DiagnosticPackage,
  type DiagnosticRecord,
  type NodeOperationType,
  type NodeRemoteOperation
} from "./node-operations";

type Props = {
  api: AdminApiClient;
  roles: readonly string[];
  access: ManagementAccess;
  organizations: AdminItem[];
  nodes: DepartmentNode[];
  packages: DiagnosticPackage[];
  operations: NodeRemoteOperation[];
  busy: boolean;
  onRefresh: () => Promise<void>;
  onStatus: (message: string) => void;
};

function defaultRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - 60 * 60 * 1000);
  const local = (value: Date): string => new Date(value.getTime() - value.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  return { start: local(start), end: local(end) };
}

/** Render IT node, diagnostics, package, and finite-operation controls from server facts. */
export function NodeOperationsPanel(props: Props): ReactElement {
  const range = useMemo(defaultRange, []);
  const departments = props.organizations.filter((item) => item.type === "department" && item.status === "active");
  const [organizationId, setOrganizationId] = useState("");
  const [nodeId, setNodeId] = useState("");
  const [startAt, setStartAt] = useState(range.start);
  const [endAt, setEndAt] = useState(range.end);
  const [diagnosticStatus, setDiagnosticStatus] = useState("");
  const [diagnostics, setDiagnostics] = useState<DiagnosticRecord[]>([]);
  const [diagnosticsLoaded, setDiagnosticsLoaded] = useState(false);
  const [enrollmentToken, setEnrollmentToken] = useState("");
  const [enrollmentId, setEnrollmentId] = useState("");
  const [operationType, setOperationType] = useState<NodeOperationType>("refresh_config");
  const [targetTaskId, setTargetTaskId] = useState("");
  const actions = allowedNodeOperationTypes(props.roles);

  const filters = (): { start_at: string; end_at: string; organization_id?: string; node_id?: string; status?: string } => ({
    start_at: new Date(startAt).toISOString(),
    end_at: new Date(endAt).toISOString(),
    ...(organizationId ? { organization_id: organizationId } : {}),
    ...(nodeId ? { node_id: nodeId } : {}),
    ...(diagnosticStatus ? { status: diagnosticStatus } : {})
  });

  async function createEnrollment(): Promise<void> {
    if (!organizationId) return;
    try {
      const item = await props.api.createNodeEnrollment({ organization_id: organizationId, expires_in_minutes: 60 });
      setEnrollmentToken(String(item.enrollment_token || ""));
      setEnrollmentId(String(item.id || ""));
      props.onStatus("节点注册码已生成，仅在当前响应中显示，请安全交给目标节点。");
    } catch (reason) {
      props.onStatus(reason instanceof Error ? reason.message : "节点注册码生成失败。");
    }
  }

  async function revokeEnrollment(): Promise<void> {
    if (!enrollmentId) return;
    try {
      await props.api.revokeNodeEnrollment(enrollmentId);
      setEnrollmentId("");
      setEnrollmentToken("");
      props.onStatus("一次性节点注册码已撤销。");
    } catch (reason) {
      props.onStatus(reason instanceof Error ? reason.message : "节点注册码撤销失败。");
    }
  }

  async function queryDiagnostics(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    try {
      const result = await props.api.diagnostics(filters());
      setDiagnostics(result.items);
      setDiagnosticsLoaded(true);
      props.onStatus(`已加载 ${result.items.length} 条脱敏诊断记录。`);
    } catch (reason) {
      setDiagnostics([]);
      setDiagnosticsLoaded(true);
      props.onStatus(reason instanceof Error ? reason.message : "诊断查询失败。");
    }
  }

  async function createPackage(): Promise<void> {
    try {
      await props.api.createDiagnosticPackage({ ...filters(), expires_in_minutes: 60 });
      await props.onRefresh();
      props.onStatus("诊断包已生成，将在 60 分钟后过期。");
    } catch (reason) {
      props.onStatus(reason instanceof Error ? reason.message : "诊断包生成失败。");
    }
  }

  async function downloadPackage(item: DiagnosticPackage): Promise<void> {
    try {
      const manifest = await props.api.downloadDiagnosticPackage(item.id);
      const url = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `diagnostic-${item.id}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      await props.onRefresh();
      props.onStatus("脱敏诊断包已下载并记录审计。");
    } catch (reason) {
      props.onStatus(reason instanceof Error ? reason.message : "诊断包下载失败或已过期。");
    }
  }

  async function createOperation(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!nodeId || (operationType === "stop_task" && !targetTaskId.trim())) return;
    try {
      await props.api.createNodeOperation(nodeId, {
        operation_type: operationType,
        ...(operationType === "stop_task" ? { target_task_id: targetTaskId.trim() } : {}),
        expires_in_minutes: 10
      });
      setTargetTaskId("");
      await props.onRefresh();
      props.onStatus(`已排队“${nodeOperationLabel(operationType)}”，等待目标节点领取。`);
    } catch (reason) {
      props.onStatus(reason instanceof Error ? reason.message : "受控运维操作提交失败。");
    }
  }

  async function revokeNode(nodeIdValue: string): Promise<void> {
    try {
      await props.api.revokeNode(nodeIdValue);
      await props.onRefresh();
      props.onStatus("节点已停用，原节点凭证不再可用。");
    } catch (reason) {
      props.onStatus(reason instanceof Error ? reason.message : "节点停用失败。");
    }
  }

  return <>
    {props.access.readNodes ? <section className="admin-card node-inventory"><h2>部门节点</h2><p>节点主动上报心跳并拉取所属部门的授权配置；控制面不会主动连接部门网络。</p>
      {props.access.manageNodes ? <div className="node-enrollment"><label>注册到部门<select value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}><option value="">请选择部门</option>{departments.map((department) => <option key={String(department.id)} value={String(department.id)}>{String(department.name)}</option>)}</select></label><button type="button" disabled={props.busy || !organizationId} onClick={() => void createEnrollment()}>生成一次性注册码</button>{enrollmentToken ? <div className="one-time-secret"><strong>仅显示一次</strong><code>{enrollmentToken}</code><div className="admin-actions"><button type="button" onClick={() => { setEnrollmentId(""); setEnrollmentToken(""); }}>我已保存，隐藏</button><button type="button" onClick={() => void revokeEnrollment()}>撤销注册码</button></div></div> : null}</div> : null}
      {props.nodes.length ? <div className="node-grid">{props.nodes.map((node) => <article key={node.id}><header><strong>{node.name}</strong><span className={`node-conn conn-${node.connection_state}`}>{nodeConnectionLabel(node.connection_state)}</span></header><p>{node.organization_name || node.organization_id} · Agent {node.agent_version}</p><p>配置：已应用 {node.applied_config_revision} / 期望 {node.desired_config_revision}{node.configuration_drift ? " · 存在漂移" : " · 已同步"}</p><p>{node.accepts_new_work ? "接收新任务" : "已暂停新任务"} · {node.health_summary || node.health_status}</p>{props.access.manageNodes && node.status === "active" ? <button type="button" disabled={props.busy} onClick={() => void revokeNode(node.id)}>停用节点</button> : null}</article>)}</div> : <p>当前授权范围内尚无已注册节点。</p>}
    </section> : null}

    {props.access.readDiagnostics ? <section className="admin-card diagnostic-center"><h2>跨部门诊断</h2><p>默认只返回任务状态、版本、错误码和脱敏摘要，不包含员工输入、凭证、环境变量或文件内容。</p><form onSubmit={(event) => void queryDiagnostics(event)}><label>开始时间<input type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} /></label><label>结束时间<input type="datetime-local" value={endAt} onChange={(event) => setEndAt(event.target.value)} /></label>{props.access.readOrganizations ? <label>部门<select value={organizationId} onChange={(event) => setOrganizationId(event.target.value)}><option value="">全部授权部门</option>{departments.map((department) => <option key={String(department.id)} value={String(department.id)}>{String(department.name)}</option>)}</select></label> : null}<label>节点<select value={nodeId} onChange={(event) => setNodeId(event.target.value)}><option value="">全部授权节点</option>{props.nodes.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}</select></label><label>任务状态<select value={diagnosticStatus} onChange={(event) => setDiagnosticStatus(event.target.value)}><option value="">全部状态</option><option value="failed">失败</option><option value="running">运行中</option><option value="cancelled">已取消</option></select></label><div className="admin-actions"><button type="submit" disabled={props.busy}>查询脱敏诊断</button>{props.access.createDiagnosticPackages ? <button type="button" disabled={props.busy} onClick={() => void createPackage()}>生成 60 分钟诊断包</button> : null}</div></form>
      {diagnostics.length ? <ul>{diagnostics.map((item) => <li key={item.task_id}><strong>{item.task_id}</strong> · {item.status} · {item.error_code || "无错误码"}<br />{item.error_summary || "无错误摘要"}</li>)}</ul> : diagnosticsLoaded ? <p>当前筛选条件下没有诊断记录。</p> : <p>设置范围后查询；最长支持 7 天。</p>}
      {props.packages.length ? <div className="diagnostic-packages"><h3>诊断包</h3>{props.packages.map((item) => <article key={item.id}><span>{item.record_count} 条 · {item.status === "expired" ? "已过期" : `有效至 ${new Date(item.expires_at).toLocaleString()}`}</span><button type="button" disabled={item.status === "expired"} onClick={() => void downloadPackage(item)}>下载 JSON</button></article>)}</div> : null}
    </section> : null}

    {props.access.manageNodeOperations ? <section className="admin-card controlled-operations"><h2>受控远程运维</h2><p>只允许预定义动作，不提供 Shell、远程桌面、文件浏览或任意命令。</p><form onSubmit={(event) => void createOperation(event)}><label>目标节点<select value={nodeId} onChange={(event) => setNodeId(event.target.value)}><option value="">请选择节点</option>{props.nodes.filter((node) => node.status === "active").map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}</select></label><label>操作<select value={operationType} onChange={(event) => setOperationType(event.target.value as NodeOperationType)}>{actions.map((action) => <option key={action} value={action}>{nodeOperationLabel(action)}</option>)}</select></label>{operationType === "stop_task" ? <label>任务编号<input value={targetTaskId} onChange={(event) => setTargetTaskId(event.target.value)} maxLength={36} /></label> : null}<button type="submit" disabled={props.busy || !nodeId}>提交受控操作</button></form>{props.operations.length ? <ul>{props.operations.map((item) => <li key={item.id}>{nodeOperationLabel(item.operation_type)} · {item.status} · {item.node_id}</li>)}</ul> : <p>当前没有待处理或历史运维操作。</p>}</section> : null}
  </>;
}
