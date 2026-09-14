import type { JSX } from "react";
import { useMemo, useState } from "react";
import type { MyCapabilities } from "./api";
import { Icon } from "./icons";
import {
  actionCategoryLabels,
  capabilityCenterState,
  informationScopeLabels,
  type CapabilityLoadStatus
} from "./capability-center";

const SKILL_PAGE_SIZE = 12;

export type EffectiveCapabilitiesPanelProps = {
  profile: MyCapabilities | null;
  status: CapabilityLoadStatus;
  selectedSkillNames: string[];
  onSkillSelectionChange: (names: string[]) => void;
};

export function EffectiveCapabilitiesPanel({
  profile,
  status,
  selectedSkillNames,
  onSkillSelectionChange
}: EffectiveCapabilitiesPanelProps): JSX.Element {
  const state = capabilityCenterState(status, profile);

  if (state === "loading") {
    return (
      <section className="settings-section effective-capability-center" aria-label="我的工作能力">
        <div className="capability-center-state" role="status">
          <Icon name="activity" size={18} className="spin" />
          <div>
            <strong>正在确认当前工作能力…</strong>
            <p>系统正在根据你的身份和部门读取最新授权。</p>
          </div>
        </div>
      </section>
    );
  }

  if (state === "unavailable") {
    return (
      <section className="settings-section effective-capability-center" aria-label="我的工作能力">
        <div className="capability-center-state unavailable" role="alert">
          <Icon name="shield" size={18} />
          <div>
            <strong>暂时无法读取工作能力</strong>
            <p>请稍后重新打开设置。现有权限不会因此扩大或改变。</p>
          </div>
        </div>
      </section>
    );
  }

  if (state === "empty" || profile === null) {
    return (
      <section className="settings-section effective-capability-center" aria-label="我的工作能力">
        <CapabilityCenterHeader />
        <div className="capability-center-state empty">
          <Icon name="sparkles" size={18} />
          <div>
            <strong>当前没有组织发放的工作能力</strong>
            <p>你仍可使用页面下方可选的本机协助模式，但它们不会授予企业系统权限。</p>
          </div>
        </div>
      </section>
    );
  }

  const actionLabels = actionCategoryLabels(profile.tools);
  const scopeLabels = informationScopeLabels(profile.knowledge_scopes, profile.memory_access);

  return (
    <section className="settings-section effective-capability-center" aria-label="我的工作能力">
      <CapabilityCenterHeader />

      <div className="effective-capability-grid">
        {profile.capability_details.map((capability) => (
          <article className="effective-capability-card" key={capability.id}>
            <span className="effective-capability-icon" aria-hidden="true">
              <Icon name="sparkles" size={15} />
            </span>
            <div>
              <strong>{capability.display_name}</strong>
              <p>{capability.summary || "组织已为你启用这项工作能力。"}</p>
            </div>
          </article>
        ))}
      </div>

      <div className="capability-authority-summary">
        <CapabilityLabelGroup
          title="可以协助的操作"
          labels={actionLabels}
          emptyText="当前能力不包含额外操作权限"
        />
        <CapabilityLabelGroup
          title="可以使用的信息"
          labels={scopeLabels}
          emptyText="当前能力不包含额外知识或记忆范围"
        />
      </div>
      {(profile.skills ?? []).length > 0 && (
        <SkillSelectionArea
          skills={profile.skills ?? []}
          selectedSkillNames={selectedSkillNames}
          onSkillSelectionChange={onSkillSelectionChange}
        />
      )}
    </section>
  );
}

function CapabilityCenterHeader(): JSX.Element {
  return (
    <header className="capability-center-header">
      <div>
        <span className="eyebrow">由组织按当前身份授权</span>
        <h3>当前已获得的工作能力</h3>
        <p>这里只展示你现在可以使用的能力；实际执行时仍会再次检查权限和审批。</p>
      </div>
      <span className="capability-authority-badge">
        <Icon name="shield" size={13} />
        服务端确认
      </span>
    </header>
  );
}

/** Searchable, paginated skill picker so the list stays usable as authorized skills grow. */
function SkillSelectionArea({
  skills,
  selectedSkillNames,
  onSkillSelectionChange
}: {
  skills: { name: string; display_name: string; selectable: boolean }[];
  selectedSkillNames: string[];
  onSkillSelectionChange: (names: string[]) => void;
}): JSX.Element {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [collapsed, setCollapsed] = useState(false);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return skills;
    return skills.filter((skill) => skill.display_name.toLowerCase().includes(keyword) || skill.name.toLowerCase().includes(keyword));
  }, [skills, query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / SKILL_PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(safePage * SKILL_PAGE_SIZE, (safePage + 1) * SKILL_PAGE_SIZE);
  const selectedCount = selectedSkillNames.length;

  function toggle(name: string, checked: boolean): void {
    onSkillSelectionChange(
      checked
        ? [...selectedSkillNames, name]
        : selectedSkillNames.filter((item) => item !== name)
    );
  }

  return (
    <div className={`capability-skill-selection${collapsed ? " collapsed" : ""}`}>
      <header className="capability-skill-header">
        <button
          type="button"
          className="capability-skill-toggle"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed(!collapsed)}
        >
          <Icon name="arrow" size={12} className={collapsed ? "capability-chevron collapsed" : "capability-chevron"} />
          <strong>本次任务优先使用的能力</strong>
          <span className="capability-skill-count">{selectedCount > 0 ? `已选 ${selectedCount} / ${skills.length}` : `共 ${skills.length} 项`}</span>
        </button>
        {!collapsed && skills.length > 6 ? (
          <input
            className="capability-skill-search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            placeholder="搜索能力名称…"
            aria-label="搜索已授权能力"
          />
        ) : null}
      </header>
      {!collapsed ? (
        <>
          <div className="capability-label-list">
            {visible.map((skill) => (
              <label key={skill.name}>
                <input
                  type="checkbox"
                  checked={selectedSkillNames.includes(skill.name)}
                  onChange={(event) => toggle(skill.name, event.target.checked)}
                />
                {skill.display_name}
              </label>
            ))}
          </div>
          {filtered.length === 0 ? <p className="capability-skill-empty">没有匹配的能力。</p> : null}
          {pageCount > 1 ? (
            <nav className="capability-skill-pagination" aria-label="能力分页">
              <button type="button" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button>
              <span>第 {safePage + 1} / {pageCount} 页 · 共 {filtered.length} 项</span>
              <button type="button" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>下一页</button>
            </nav>
          ) : null}
          <p>未勾选的已授权能力仅在任务内容匹配时渐进式披露。</p>
        </>
      ) : null}
    </div>
  );
}

function CapabilityLabelGroup({
  title,
  labels,
  emptyText
}: {
  title: string;
  labels: string[];
  emptyText: string;
}): JSX.Element {
  return (
    <div className="capability-label-group">
      <strong>{title}</strong>
      {labels.length > 0 ? (
        <div className="capability-label-list">
          {labels.map((label) => <span key={label}>{label}</span>)}
        </div>
      ) : (
        <p>{emptyText}</p>
      )}
    </div>
  );
}
