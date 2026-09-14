import { useState, useEffect, type JSX } from "react";
import { Icon } from "./icons.tsx";
import type { LocalApiClient, ReminderItem, DesktopNotification } from "./api.ts";
import {
  calculatePresetDueTime,
  filterReminders,
  formatReminderTime,
  REMINDER_PRESETS,
  reminderStatusLabel,
  type ReminderPreset
} from "./reminders.ts";

export type RemindersPanelProps = {
  api: LocalApiClient;
  notifications: DesktopNotification[];
  onRefreshNotifications: () => void;
};

export function RemindersPanel({
  api,
  notifications,
  onRefreshNotifications
}: RemindersPanelProps): JSX.Element {
  const [activeTab, setActiveTab] = useState<"reminders" | "notifications">("reminders");
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState("");

  // Create form state
  const [newTitle, setNewTitle] = useState("");
  const [newMessage, setNewMessage] = useState("");
  const [selectedPreset, setSelectedPreset] = useState<ReminderPreset>("1h");
  const [customDueTime, setCustomDueTime] = useState("");
  const [useCustomTime, setUseCustomTime] = useState(false);

  const fetchReminders = async () => {
    if (!api.hasUserId) return;
    setLoading(true);
    setActionError("");
    try {
      const items = await api.listReminders();
      setReminders(items);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "无法加载提醒列表");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchReminders();
  }, [api]);

  const handleCreateReminder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim() || actionPending) return;
    setActionPending(true);
    setActionError("");
    try {
      const dueAt = useCustomTime && customDueTime
        ? new Date(customDueTime).toISOString()
        : calculatePresetDueTime(selectedPreset);
      await api.createReminder(newTitle.trim(), newMessage.trim(), dueAt, "desktop");
      setNewTitle("");
      setNewMessage("");
      setShowCreateModal(false);
      await fetchReminders();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "创建提醒失败");
    } finally {
      setActionPending(false);
    }
  };

  const handleCancelReminder = async (reminderId: string) => {
    if (actionPending) return;
    setActionPending(true);
    try {
      await api.cancelReminder(reminderId);
      await fetchReminders();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "取消提醒失败");
    } finally {
      setActionPending(false);
    }
  };

  const handleAckNotification = async (notificationId: string) => {
    try {
      await api.ackNotification(notificationId);
      onRefreshNotifications();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "确认通知失败");
    }
  };

  const handleAckAllNotifications = async () => {
    if (!notifications.length || actionPending) return;
    setActionPending(true);
    try {
      for (const n of notifications) {
        const id = n.outbox_id || n.id;
        if (id) {
          await api.ackNotification(id);
        }
      }
      onRefreshNotifications();
    } finally {
      setActionPending(false);
    }
  };

  const filtered = filterReminders(reminders, searchQuery, statusFilter);
  const pendingCount = reminders.filter((r) => r.status === "pending").length;

  return (
    <section className="reminders-panel" aria-label="提醒与通知中心">
      <header className="reminders-header">
        <div className="reminders-title-group">
          <Icon name="clock" size={16} />
          <strong>提醒与通知中心</strong>
        </div>
        <div className="reminders-header-actions">
          <button
            type="button"
            className="icon-button compact-refresh"
            onClick={() => {
              void fetchReminders();
              onRefreshNotifications();
            }}
            disabled={loading || actionPending}
            title="刷新"
            aria-label="刷新提醒与通知"
          >
            <Icon name="refresh" size={14} className={loading ? "spin" : undefined} />
          </button>
          <button
            type="button"
            className="primary-action small-btn"
            onClick={() => setShowCreateModal(true)}
          >
            <Icon name="plus" size={13} />
            <span>新建提醒</span>
          </button>
        </div>
      </header>

      {actionError ? (
        <div className="panel-error-alert" role="alert">
          <Icon name="shield" size={14} />
          <span>{actionError}</span>
        </div>
      ) : null}

      <div className="reminders-sub-tabs">
        <button
          type="button"
          className={activeTab === "reminders" ? "sub-tab active" : "sub-tab"}
          onClick={() => setActiveTab("reminders")}
        >
          <span>定时提醒</span>
          {pendingCount > 0 ? <span className="tab-count">{pendingCount}</span> : null}
        </button>
        <button
          type="button"
          className={activeTab === "notifications" ? "sub-tab active" : "sub-tab"}
          onClick={() => setActiveTab("notifications")}
        >
          <span>系统通知</span>
          {notifications.length > 0 ? (
            <span className="tab-count alert-count">{notifications.length}</span>
          ) : null}
        </button>
      </div>

      {activeTab === "reminders" ? (
        <div className="reminders-content">
          <div className="reminders-filters">
            <div className="reminders-search-box">
              <Icon name="search" size={13} />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索提醒事项…"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="reminders-status-select"
            >
              <option value="all">全部状态</option>
              <option value="pending">待提醒</option>
              <option value="completed">已触发</option>
              <option value="cancelled">已取消</option>
            </select>
          </div>

          <div className="reminders-list">
            {loading && !reminders.length ? (
              <div className="panel-loading-state">
                <Icon name="activity" size={16} />
                <span>正在加载提醒…</span>
              </div>
            ) : filtered.length === 0 ? (
              <div className="panel-empty-state">
                <Icon name="clock" size={24} />
                <strong>暂无提醒事项</strong>
                <p>点击上方“新建提醒”创建定时提醒，助理将在设定时间通过系统通知提醒你。</p>
              </div>
            ) : (
              filtered.map((item) => {
                const isPending = item.status === "pending";
                const reminderId = item.reminder_id || item.id || "";
                return (
                  <article key={reminderId} className={`reminder-card status-${item.status}`}>
                    <div className="reminder-card-main">
                      <div className="reminder-card-header">
                        <strong className="reminder-title">{item.title}</strong>
                        <span className={`status-badge ${item.status}`}>
                          {reminderStatusLabel(item.status)}
                        </span>
                      </div>
                      {item.message ? <p className="reminder-desc">{item.message}</p> : null}
                      <div className="reminder-meta">
                        <span className="reminder-due-time">
                          <Icon name="clock" size={12} />
                          {formatReminderTime(item.due_at)}
                        </span>
                        {item.channel ? (
                          <span className="reminder-channel-tag">{item.channel}</span>
                        ) : null}
                      </div>
                    </div>
                    {isPending && reminderId ? (
                      <div className="reminder-card-actions">
                        <button
                          type="button"
                          className="cancel-reminder-btn"
                          onClick={() => void handleCancelReminder(reminderId)}
                          disabled={actionPending}
                          title="取消此提醒"
                          aria-label="取消此提醒"
                        >
                          <Icon name="x" size={13} />
                          <span>取消</span>
                        </button>
                      </div>
                    ) : null}
                  </article>
                );
              })
            )}
          </div>
        </div>
      ) : (
        <div className="notifications-content">
          <div className="notifications-toolbar">
            <span className="notifications-count-text">未读通知 ({notifications.length})</span>
            {notifications.length > 0 ? (
              <button
                type="button"
                className="secondary-action small-btn"
                onClick={() => void handleAckAllNotifications()}
                disabled={actionPending}
              >
                全部确认
              </button>
            ) : null}
          </div>

          <div className="notifications-list">
            {notifications.length === 0 ? (
              <div className="panel-empty-state">
                <Icon name="check" size={24} />
                <strong>暂无未读系统通知</strong>
                <p>所有定时任务通知和执行结果已处理完成。</p>
              </div>
            ) : (
              notifications.map((n) => {
                const nId = n.outbox_id || n.id || "";
                return (
                  <article key={nId} className="notification-card">
                    <div className="notification-icon">
                      <Icon name="sparkles" size={16} />
                    </div>
                    <div className="notification-main">
                      <strong>{n.title}</strong>
                      <p>{n.message || n.body || ""}</p>
                      {n.due_at ? <small>{formatReminderTime(n.due_at)}</small> : null}
                    </div>
                    <div className="notification-actions">
                      <button
                        type="button"
                        className="ack-notification-btn"
                        onClick={() => void handleAckNotification(nId)}
                        disabled={actionPending}
                        title="确认已知悉"
                        aria-label="确认已知悉"
                      >
                        <Icon name="check" size={13} />
                        <span>知道了</span>
                      </button>
                    </div>
                  </article>
                );
              })
            )}
          </div>
        </div>
      )}

      {showCreateModal ? (
        <div className="modal-backdrop" onClick={() => setShowCreateModal(false)}>
          <div
            className="modal-card reminder-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-labelledby="create-reminder-title"
          >
            <header className="modal-header">
              <div className="modal-title-group">
                <Icon name="clock" size={16} />
                <h3 id="create-reminder-title">新建定时提醒</h3>
              </div>
              <button
                type="button"
                className="icon-button modal-close"
                onClick={() => setShowCreateModal(false)}
                aria-label="关闭"
              >
                <Icon name="x" size={15} />
              </button>
            </header>

            <form onSubmit={(e) => void handleCreateReminder(e)} className="modal-form">
              <label className="form-field">
                <span>提醒事项 <strong className="required-star">*</strong></span>
                <input
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="例如：跟进周会决策推进…"
                  required
                  autoFocus
                />
              </label>

              <label className="form-field">
                <span>补充说明（可选）</span>
                <textarea
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="添加具体细节或相关链接…"
                  rows={3}
                />
              </label>

              <div className="form-field">
                <span>提醒时间</span>
                {!useCustomTime ? (
                  <div className="preset-time-selector">
                    <select
                      value={selectedPreset}
                      onChange={(e) => setSelectedPreset(e.target.value as ReminderPreset)}
                    >
                      {REMINDER_PRESETS.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="text-link-btn"
                      onClick={() => setUseCustomTime(true)}
                    >
                      自定义具体时间
                    </button>
                  </div>
                ) : (
                  <div className="custom-time-selector">
                    <input
                      type="datetime-local"
                      value={customDueTime}
                      onChange={(e) => setCustomDueTime(e.target.value)}
                      required
                    />
                    <button
                      type="button"
                      className="text-link-btn"
                      onClick={() => setUseCustomTime(false)}
                    >
                      使用常用预设
                    </button>
                  </div>
                )}
              </div>

              <footer className="modal-actions">
                <button
                  type="button"
                  className="secondary-action"
                  onClick={() => setShowCreateModal(false)}
                  disabled={actionPending}
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="primary-action"
                  disabled={actionPending || !newTitle.trim()}
                >
                  {actionPending ? "创建中…" : "确认创建"}
                </button>
              </footer>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}
