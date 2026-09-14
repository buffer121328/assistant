import test from "node:test";
import assert from "node:assert/strict";
import { managementAccess } from "./admin-access.ts";

test("enterprise administrator receives the complete existing management loop", () => {
  const access = managementAccess(["enterprise_admin"]);
  assert.equal(access.enterWorkbench, true);
  assert.ok(Object.values(access).every(Boolean));
});

test("department administrator receives scoped reads and distribution only", () => {
  assert.deepEqual(managementAccess(["department_admin"]), {
    enterWorkbench: true,
    readOrganizations: true,
    readMembers: true,
    readCapabilities: true,
    readOrganizationCapabilities: true,
    readAudit: false,
    readCapabilityRequests: false,
    bootstrapDepartments: false,
    manageMembers: false,
    distributeCapabilities: true,
    recordSkillCandidates: false,
    manageConnectors: false,
    triageCapabilityRequests: false,
    readNodes: true,
    manageNodes: false,
    readDiagnostics: true,
    createDiagnosticPackages: true,
    manageNodeOperations: true
  });
});

test("AI department lead receives the improvement queue without enterprise writes", () => {
  const access = managementAccess(["department_admin"], ["AI部"]);
  assert.equal(access.readCapabilityRequests, true);
  assert.equal(access.triageCapabilityRequests, true);
  assert.equal(access.manageMembers, false);
  assert.equal(access.manageConnectors, false);
});

test("capability publisher receives catalog and metadata-only candidate intake", () => {
  const access = managementAccess(["capability_publisher"]);
  assert.equal(access.readCapabilities, true);
  assert.equal(access.recordSkillCandidates, true);
  assert.equal(access.readCapabilityRequests, true);
  assert.equal(access.triageCapabilityRequests, true);
  assert.equal(access.readOrganizations, false);
  assert.equal(access.distributeCapabilities, false);
});

test("connector administrator receives connector definition management without unrelated reads", () => {
  const access = managementAccess(["connector_admin"]);
  assert.equal(access.enterWorkbench, true);
  assert.equal(access.manageConnectors, true);
  assert.equal(access.readOrganizations, false);
  assert.equal(access.readCapabilities, false);
  assert.equal(access.readAudit, false);
  assert.equal(access.readCapabilityRequests, false);
  assert.equal(access.readDiagnostics, false);
  assert.equal(access.createDiagnosticPackages, false);
  assert.equal(access.manageNodeOperations, false);
});

test("auditor receives scoped organizations and redacted audit only", () => {
  const access = managementAccess(["auditor"]);
  assert.equal(access.readOrganizations, true);
  assert.equal(access.readAudit, true);
  assert.equal(access.readMembers, false);
  assert.equal(access.manageMembers, false);
  assert.equal(access.readCapabilityRequests, false);
  assert.equal(access.readDiagnostics, true);
  assert.equal(access.createDiagnosticPackages, true);
  assert.equal(access.manageNodeOperations, false);
});

test("multiple roles combine their bounded access", () => {
  const access = managementAccess(["capability_publisher", "auditor"]);
  assert.equal(access.readCapabilities, true);
  assert.equal(access.recordSkillCandidates, true);
  assert.equal(access.readOrganizations, true);
  assert.equal(access.readAudit, true);
  assert.equal(access.readCapabilityRequests, true);
  assert.equal(access.manageConnectors, false);
});

test("ordinary and unknown roles fail closed", () => {
  for (const roles of [["member"], ["manager"], ["unknown_role"], []]) {
    assert.ok(Object.values(managementAccess(roles)).every((value) => value === false));
  }
});
