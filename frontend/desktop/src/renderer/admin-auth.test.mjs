import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("./AdminApp.tsx", import.meta.url), "utf8");
const api = await readFile(new URL("./admin-api.ts", import.meta.url), "utf8");

test("admin renderer gates management data behind session and a fail-closed role profile", () => {
  assert.match(app, /authState/);
  assert.match(app, /if \(!session\)/);
  assert.match(app, /if \(!access\.enterWorkbench\)/);
  assert.match(app, /当前身份没有管理权限/);
  assert.match(app, /access\.readOrganizations \? api\.organizations\(\)/);
  assert.match(app, /access\.readMembers \? api\.members\(\)/);
  assert.match(app, /access\.readCapabilities \? api\.capabilities\(\)/);
  assert.match(app, /access\.readAudit \? api\.audit\(\)/);
  assert.match(app, /access\.readCapabilityRequests \? api\.capabilityRequests\(\)/);
  assert.match(app, /access\.readNodes \? api\.nodes\(\)/);
  assert.match(app, /access\.readDiagnostics \? api\.diagnosticPackages\(\)/);
  assert.match(app, /access\.manageNodeOperations \? api\.nodeOperations\(\)/);
  assert.match(app, /setDashboard\(emptyDashboard\(\)\)/);
  assert.match(app, /revision !== refreshRevision\.current/);
  assert.match(app, /window\.assistantDesktop\.logout/);
  assert.doesNotMatch(app, /管理员标识/);
});

test("specialized management sections follow the derived role access", () => {
  assert.match(app, /access\.manageMembers/);
  assert.match(app, /access\.distributeCapabilities/);
  assert.match(app, /access\.recordSkillCandidates/);
  assert.match(app, /access\.manageConnectors/);
  assert.match(app, /access\.readAudit/);
  assert.match(app, /access\.triageCapabilityRequests/);
  assert.match(app, /NodeOperationsPanel/);
  assert.match(app, /能力改进队列/);
  assert.match(app, /AI_OPERATIONS_POLL_INTERVAL_MS/);
  assert.match(app, /identityResponsibilityLabel/);
  assert.match(app, /登记候选能力/);
  assert.match(app, /不会收集凭证或自动启用任何内容/);
  assert.doesNotMatch(app, /credential_ref|实例密钥<.*input/s);
});

test("admin API uses the main-process proxy rather than hand-entered identity or direct fetch", () => {
  assert.match(api, /window\.assistantDesktop\.apiRequest/);
  assert.doesNotMatch(api, /fetch\(/);
  assert.doesNotMatch(api, /user_id=/);
  assert.match(api, /bootstrapDepartments/);
  assert.match(app, /检查工作区基线/);
  assert.match(api, /provisionMember/);
  assert.match(api, /assignMemberRole/);
  assert.match(api, /distributeCapability/);
  assert.match(api, /recordSkillCandidate/);
  assert.match(api, /\/api\/admin\/skill-candidates/);
  assert.match(api, /\/api\/admin\/capability-requests/);
  assert.match(api, /updateCapabilityRequestStatus/);
  assert.match(api, /createNodeEnrollment/);
  assert.match(api, /diagnosticPackages/);
  assert.match(api, /createNodeOperation/);
  assert.doesNotMatch(api, /shell_command|command_text/);
});

test("mature management navigation keeps pages focused and actions progressive", () => {
  assert.match(app, /type AdminSection = "overview" \| "organization" \| "capabilities" \| "operations" \| "audit"/);
  assert.match(app, /setActiveSection\("overview"\)/);
  assert.match(app, /setActiveSection\("organization"\)/);
  assert.match(app, /setActiveSection\("capabilities"\)/);
  assert.match(app, /access\.readAudit \? <button/);
  assert.match(app, /activeSection === "organization"/);
  assert.match(app, /activeSection === "capabilities"/);
  assert.match(app, /activeAction \? <div className="action-layer"/);
  assert.match(app, /setActiveAction\("member"\)/);
  assert.match(app, /setActiveAction\("distribute"\)/);
  assert.match(app, /本地工作区/);
  assert.doesNotMatch(app, /Local 是当前服务器/);
});

test("connector definition values match the governed backend contract", () => {
  assert.match(app, /option value="MCP"/);
  assert.match(app, /option value="REST"/);
  assert.match(app, /option value="FEISHU"/);
  assert.doesNotMatch(app, /option value="webhook"|option value="plugin"/);
});

test("capability catalog lists vertically with paging and a department scope view", () => {
  assert.match(app, /CATALOG_PAGE_SIZE = 10/);
  assert.match(app, /className="capability-list"/);
  assert.match(app, /按部门查看已有能力/);
  assert.match(app, /readOrganizationCapabilities/);
  assert.match(app, /上一页/);
  assert.match(app, /下一页/);
  assert.match(app, /该部门还没有已分发的能力/);
  assert.match(api, /\/api\/admin\/organizations\/\$\{encodeURIComponent\(organizationId\)\}\/capabilities/);
  assert.match(app, /setCapabilityDepartmentId/);
});

test("capability rows render Chinese skill labels and department enablement", () => {
  assert.match(app, /skillLabel/);
  assert.match(app, /已启用部门：/);
  assert.match(app, /尚未分发给任何部门/);
  assert.match(app, /capability\.departments/);
});
