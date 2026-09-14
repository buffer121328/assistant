import test from "node:test";
import assert from "node:assert/strict";
import {
  authErrorMessage,
  departmentLabel,
  identityResponsibilityLabel,
  roleLabel
} from "./auth-presentation.ts";

const session = (overrides = {}) => ({
  expires_at: "2026-08-16T00:00:00Z",
  tenant_id: "tenant-1",
  user_id: "user-1",
  display_name: "张三",
  organization_ids: ["finance"],
  roles: ["member"],
  authority_revision: 1,
  ...overrides
});

test("auth presentation only surfaces bounded backend messages", () => {
  assert.equal(authErrorMessage({ error: { message: "登录失败" } }), "登录失败");
  assert.equal(authErrorMessage({ detail: [{ msg: "密码太短" }] }), "密码太短");
  assert.equal(authErrorMessage({ detail: { token: "secret" } }), null);
});

test("identity summary uses department names without exposing internal IDs", () => {
  assert.equal(departmentLabel(session({ organization_names: ["财务部"] })), "财务部");
  assert.equal(departmentLabel(session({ organization_names: undefined, organization_ids: ["org-1", "org-2"] })), "已加入 2 个部门");
  assert.equal(roleLabel(["enterprise_admin", "department_admin"]), "企业管理员、部门负责人");
  assert.equal(roleLabel(["member", "manager"]), "员工");
  assert.equal(roleLabel(["unknown_internal_role"]), "员工");
  assert.equal(identityResponsibilityLabel(["enterprise_admin", "department_admin"]), "部门负责人");
  assert.equal(identityResponsibilityLabel(["member"]), "员工");
});
