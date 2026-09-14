export type ManagementAccess = {
  enterWorkbench: boolean;
  readOrganizations: boolean;
  readMembers: boolean;
  readCapabilities: boolean;
  readOrganizationCapabilities: boolean;
  readAudit: boolean;
  readCapabilityRequests: boolean;
  bootstrapDepartments: boolean;
  manageMembers: boolean;
  distributeCapabilities: boolean;
  recordSkillCandidates: boolean;
  manageConnectors: boolean;
  triageCapabilityRequests: boolean;
  readNodes: boolean;
  manageNodes: boolean;
  readDiagnostics: boolean;
  createDiagnosticPackages: boolean;
  manageNodeOperations: boolean;
};

/**
 * Mirror the backend's current role-to-operation matrix for presentation only.
 * The server remains authoritative for every read and mutation.
 */
export function managementAccess(
  roles: readonly string[],
  organizationNames: readonly string[] = []
): ManagementAccess {
  const has = (role: string): boolean => roles.includes(role);
  const enterprise = has("enterprise_admin");
  const department = has("department_admin");
  const publisher = has("capability_publisher");
  const connector = has("connector_admin");
  const auditor = has("auditor");
  const aiDepartmentLead = department && organizationNames.includes("AI部");

  return {
    enterWorkbench: enterprise || department || publisher || connector || auditor,
    readOrganizations: enterprise || department || auditor,
    readMembers: enterprise || department,
    readCapabilities: enterprise || department || publisher,
    readOrganizationCapabilities: enterprise || department,
    readAudit: enterprise || auditor,
    readCapabilityRequests: enterprise || publisher || aiDepartmentLead,
    bootstrapDepartments: enterprise,
    manageMembers: enterprise,
    distributeCapabilities: enterprise || department,
    recordSkillCandidates: enterprise || publisher,
    manageConnectors: enterprise || connector,
    triageCapabilityRequests: enterprise || publisher || aiDepartmentLead,
    readNodes: enterprise || department,
    manageNodes: enterprise,
    readDiagnostics: enterprise || department || auditor,
    createDiagnosticPackages: enterprise || department || auditor,
    manageNodeOperations: enterprise || department
  };
}
