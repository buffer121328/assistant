import { FormEvent, JSX, useState } from "react";
import {
  capabilityRequestStatusLabel,
  type CapabilityRequest
} from "./capability-requests";

export type CapabilityRequestDraft = {
  title: string;
  description: string;
  related_task_id: string | null;
};

const REQUESTS_PER_PAGE = 5;

export function CapabilityRequestPanel({
  requests,
  status,
  relatedTaskId,
  onSubmit
}: {
  requests: CapabilityRequest[];
  status: "idle" | "loading" | "ready" | "failed";
  relatedTaskId: string | null;
  onSubmit: (draft: CapabilityRequestDraft) => Promise<void>;
}): JSX.Element {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [includeTask, setIncludeTask] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [page, setPage] = useState(0);

  const pageCount = Math.max(1, Math.ceil(requests.length / REQUESTS_PER_PAGE));
  const safePage = Math.min(page, pageCount - 1);
  const visibleRequests = requests.slice(
    safePage * REQUESTS_PER_PAGE,
    (safePage + 1) * REQUESTS_PER_PAGE
  );

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!title.trim() || !description.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await onSubmit({
        title: title.trim(),
        description: description.trim(),
        related_task_id: includeTask ? relatedTaskId : null
      });
      setTitle("");
      setDescription("");
      setIncludeTask(false);
      setPage(0);
      setMessage("申请已提交给 IT。它不会立即扩大你的工作权限。后续进度会显示在右侧。");
    } catch {
      setMessage("申请暂时没有提交成功，请稍后重试；你填写的内容已保留。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-section capability-request-panel" aria-label="向 IT 申请能力">
      <div className="capability-request-layout">
        <form className="capability-request-form" onSubmit={(event) => void submit(event)}>
          <p className="capability-request-intro">说明你想完成的工作。提交申请不会直接获得新权限。</p>
          <label>需要什么能力<input value={title} maxLength={160} onChange={(event) => setTitle(event.target.value)} placeholder="例如：审核供应商合同" required /></label>
          <label>工作目标和使用场景<textarea value={description} maxLength={2000} rows={6} onChange={(event) => setDescription(event.target.value)} placeholder="请说明要解决的问题、期望结果和使用频率" required /></label>
          {relatedTaskId ? <label className="capability-request-task"><input type="checkbox" checked={includeTask} onChange={(event) => setIncludeTask(event.target.checked)} />关联当前任务，帮助 IT 理解场景</label> : null}
          <div className="capability-request-actions">
            <button type="submit" disabled={busy}>{busy ? "正在提交…" : "提交能力申请"}</button>
          </div>
          {message ? <p role="status" className="capability-request-message">{message}</p> : null}
        </form>
        <aside className="capability-request-history" aria-label="我的能力申请">
          <header className="capability-request-history-header">
            <h4>我的申请</h4>
            {requests.length > 0 ? <span className="capability-request-count">共 {requests.length} 条</span> : null}
          </header>
          {status === "loading" ? <p role="status">正在读取申请进度…</p> : null}
          {status === "failed" ? <p role="alert">暂时无法读取申请进度，请稍后重试。</p> : null}
          {status === "ready" && requests.length === 0 ? <p className="capability-request-empty">还没有提交过能力申请。</p> : null}
          {visibleRequests.map((request) => (
            <article key={request.id} className="capability-request-item">
              <div className="capability-request-item-head">
                <strong>{request.title}</strong>
                <span className={`capability-request-status status-${request.status}`}>{capabilityRequestStatusLabel(request.status)}</span>
              </div>
              <p>{request.description}</p>
              <small>{request.organization_name} · {new Date(request.updated_at).toLocaleString()}</small>
            </article>
          ))}
          {pageCount > 1 ? (
            <nav className="capability-request-pagination" aria-label="申请分页">
              <button type="button" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button>
              <span>第 {safePage + 1} / {pageCount} 页</span>
              <button type="button" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>下一页</button>
            </nav>
          ) : null}
        </aside>
      </div>
    </section>
  );
}
