import { css, html, LitElement } from "lit";
import { customElement, state } from "lit/decorators.js";

type LogRow = {
  id: number;
  turbine_code: string;
  yaw_err_deg: number;
  status: string;
  verdict: string | null;
  reason: string | null;
  created_by: string;
  created_at: string;
  processed_at: string | null;
};

type MergePreview = {
  turbine_code: string;
  source_ids: number[];
  source_count: number;
  avg_yaw_err_deg: number;
};

type MergeRow = {
  id: number;
  new_log_id: number;
  turbine_code: string;
  source_ids: number[];
  source_count: number;
  avg_yaw_err_deg: number;
  created_by: string;
  created_at: string;
};

type Session = {
  token: string;
  username: string;
  role: string;
};

type View = "logs" | "merge";

@customElement("yaw-align-app")
export class YawAlignApp extends LitElement {
  static styles = css`
    :host {
      display: block;
      min-height: 100vh;
      box-sizing: border-box;
      padding: 1.5rem;
      max-width: 960px;
      margin: 0 auto;
    }
    h1 {
      margin: 0 0 0.25rem;
      font-size: 1.75rem;
      color: #38bdf8;
    }
    .sub {
      color: #94a3b8;
      margin-bottom: 1.5rem;
    }
    section {
      background: #1e293b;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
      border: 1px solid #334155;
    }
    label {
      display: block;
      font-size: 0.85rem;
      color: #cbd5e1;
      margin-bottom: 0.25rem;
    }
    input {
      width: 100%;
      box-sizing: border-box;
      padding: 0.5rem 0.65rem;
      border-radius: 6px;
      border: 1px solid #475569;
      background: #0f172a;
      color: #f1f5f9;
      margin-bottom: 0.75rem;
    }
    input[type="checkbox"] {
      width: auto;
      margin: 0;
    }
    button {
      cursor: pointer;
      padding: 0.5rem 1rem;
      border-radius: 6px;
      border: none;
      background: #0284c7;
      color: #fff;
      font-weight: 600;
    }
    button.secondary {
      background: #475569;
    }
    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }
    th,
    td {
      text-align: left;
      padding: 0.5rem 0.4rem;
      border-bottom: 1px solid #334155;
    }
    th {
      color: #94a3b8;
      font-weight: 600;
    }
    .tag {
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      font-size: 0.8rem;
    }
    .ok {
      background: #14532d;
      color: #86efac;
    }
    .bad {
      background: #7f1d1d;
      color: #fca5a5;
    }
    .pending {
      background: #713f12;
      color: #fde68a;
    }
    .err {
      color: #f87171;
      margin-top: 0.5rem;
    }
    .notice {
      color: #86efac;
      margin-top: 0.5rem;
    }
    .hint {
      color: #94a3b8;
      font-size: 0.85rem;
    }
    .preview-box {
      margin-top: 0.75rem;
      padding: 0.6rem 0.8rem;
      border-radius: 6px;
      border: 1px solid #334155;
      background: #0f172a;
      font-size: 0.9rem;
    }
    .row-actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      align-items: center;
    }
    .spacer {
      flex: 1;
    }
  `;

  @state() private session: Session | null = null;
  @state() private logs: LogRow[] = [];
  @state() private merges: MergeRow[] = [];
  @state() private view: View = "logs";
  @state() private selectedIds: Set<number> = new Set();
  @state() private preview: MergePreview | null = null;
  @state() private mergeError = "";
  @state() private mergeNotice = "";
  @state() private loginUser = "technician";
  @state() private loginPass = "tech123456";
  @state() private turbineCode = "";
  @state() private yawErr = "";
  @state() private error = "";
  @state() private loading = false;

  connectedCallback() {
    super.connectedCallback();
    const raw = localStorage.getItem("yaw_session");
    if (raw) {
      try {
        this.session = JSON.parse(raw) as Session;
        void this.refreshAll();
        this._pollTimer = window.setInterval(() => void this.refreshAll(), 2000);
      } catch {
        localStorage.removeItem("yaw_session");
      }
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
    }
  }

  private _pollTimer?: number;

  private authHeaders(): HeadersInit {
    return this.session
      ? { Authorization: `Bearer ${this.session.token}` }
      : {};
  }

  private async refreshAll() {
    await Promise.all([this.refreshLogs(), this.refreshMerges()]);
  }

  private async refreshLogs() {
    if (!this.session) return;
    try {
      const res = await fetch("/api/logs", { headers: this.authHeaders() });
      if (res.status === 401) {
        this.logout();
        return;
      }
      if (!res.ok) return;
      this.logs = (await res.json()) as LogRow[];
    } catch {
      /* ignore transient network errors */
    }
  }

  private async refreshMerges() {
    if (!this.session) return;
    try {
      const res = await fetch("/api/merges", { headers: this.authHeaders() });
      if (res.status === 401) {
        this.logout();
        return;
      }
      if (!res.ok) return;
      this.merges = (await res.json()) as MergeRow[];
    } catch {
      /* ignore transient network errors */
    }
  }

  private async login() {
    this.error = "";
    this.loading = true;
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: this.loginUser,
          password: this.loginPass,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        this.error = data.detail || "登录失败";
        return;
      }
      this.session = {
        token: data.access_token,
        username: data.username,
        role: data.role,
      };
      localStorage.setItem("yaw_session", JSON.stringify(this.session));
      await this.refreshAll();
      this._pollTimer = window.setInterval(() => void this.refreshAll(), 2000);
    } catch {
      this.error = "无法连接接口";
    } finally {
      this.loading = false;
    }
  }

  private logout() {
    if (this._pollTimer) clearInterval(this._pollTimer);
    this.session = null;
    this.logs = [];
    this.merges = [];
    this.view = "logs";
    this.selectedIds = new Set();
    this.preview = null;
    this.mergeError = "";
    this.mergeNotice = "";
    localStorage.removeItem("yaw_session");
  }

  private get isWriter() {
    return this.session?.role === "writer";
  }

  private setView(view: View) {
    this.view = view;
    this.mergeError = "";
    this.mergeNotice = "";
  }

  private get doneLogs() {
    return this.logs.filter((row) => row.status === "done");
  }

  private toggleSelect(id: number) {
    const next = new Set(this.selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    this.selectedIds = next;
    this.preview = null;
    this.mergeError = "";
    this.mergeNotice = "";
  }

  private async previewMerge() {
    this.mergeError = "";
    this.mergeNotice = "";
    this.loading = true;
    try {
      const res = await fetch("/api/merge/preview", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...this.authHeaders(),
        },
        body: JSON.stringify({ ids: [...this.selectedIds] }),
      });
      if (res.status === 401) {
        this.logout();
        return;
      }
      const data = await res.json();
      if (!res.ok) {
        this.preview = null;
        this.mergeError = data.detail || "预览失败";
        return;
      }
      this.preview = data as MergePreview;
    } catch {
      this.mergeError = "预览时网络异常";
    } finally {
      this.loading = false;
    }
  }

  private async enqueueMerge() {
    this.mergeError = "";
    this.mergeNotice = "";
    this.loading = true;
    try {
      const res = await fetch("/api/merge/enqueue", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...this.authHeaders(),
        },
        body: JSON.stringify({ ids: [...this.selectedIds] }),
      });
      if (res.status === 401) {
        this.logout();
        return;
      }
      const data = await res.json();
      if (!res.ok) {
        this.mergeError = data.detail || "入队失败";
        return;
      }
      const result = data as { log: LogRow; merge: MergeRow };
      this.mergeNotice = `已入队：新待处理单 #${result.log.id}（合成流水 #${result.merge.id}）`;
      this.selectedIds = new Set();
      this.preview = null;
      await this.refreshAll();
    } catch {
      this.mergeError = "入队时网络异常";
    } finally {
      this.loading = false;
    }
  }

  private async submitLog() {
    this.error = "";
    this.loading = true;
    try {
      const res = await fetch("/api/logs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...this.authHeaders(),
        },
        body: JSON.stringify({
          turbine_code: this.turbineCode,
          yaw_err_deg: Number(this.yawErr),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        this.error = data.detail || "提交失败";
        return;
      }
      this.turbineCode = "";
      this.yawErr = "";
      await this.refreshLogs();
    } catch {
      this.error = "提交时网络异常";
    } finally {
      this.loading = false;
    }
  }

  private verdictClass(row: LogRow) {
    if (row.status === "pending") return "pending";
    if (row.verdict === "合格") return "ok";
    if (row.verdict === "偏航超差") return "bad";
    return "";
  }

  private renderMergeView() {
    const done = this.doneLogs;
    return html`
      <section>
        <h2 style="margin-top:0;font-size:1.1rem;">均值合成入队</h2>
        <p class="hint">
          勾选至少两笔同机组的「已办结」记录，先预览后台均值，再确认入队生成一笔新的待处理单。
        </p>
        ${done.length === 0
          ? html`<p class="hint">暂无已办结记录可合成。</p>`
          : html`
              <table>
                <thead>
                  <tr>
                    <th>勾选</th>
                    <th>编号</th>
                    <th>机组</th>
                    <th>误差°</th>
                    <th>结论</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  ${done.map(
                    (row) => html`
                      <tr>
                        <td>
                          <input
                            type="checkbox"
                            .checked=${this.selectedIds.has(row.id)}
                            @change=${() => this.toggleSelect(row.id)}
                          />
                        </td>
                        <td>${row.id}</td>
                        <td>${row.turbine_code}</td>
                        <td>${row.yaw_err_deg}</td>
                        <td>
                          ${row.verdict
                            ? html`<span class="tag ${this.verdictClass(row)}">
                                  ${row.verdict}
                                </span>`
                            : "—"}
                        </td>
                        <td>${row.reason ?? "—"}</td>
                      </tr>
                    `
                  )}
                </tbody>
              </table>
            `}
        <div class="row-actions" style="margin-top:0.75rem;">
          <span class="hint">已选 ${this.selectedIds.size} 笔</span>
          <button
            ?disabled=${this.selectedIds.size < 2 || this.loading}
            @click=${this.previewMerge}
          >
            预览均值
          </button>
          <button
            ?disabled=${!this.isWriter || !this.preview || this.loading}
            @click=${this.enqueueMerge}
          >
            确认入队
          </button>
          ${!this.isWriter
            ? html`<span class="hint">观察员仅可预览，入队需现场技师账号</span>`
            : null}
        </div>
        ${this.preview
          ? html`<div class="preview-box">
              预览（后台计算）：机组 <b>${this.preview.turbine_code}</b> · 共
              ${this.preview.source_count} 笔（${this.preview.source_ids
                .map((id) => "#" + id)
                .join("、")}）· 均值
              <b>${this.preview.avg_yaw_err_deg}°</b>
              ，确认入队后生成一笔「待处理」新单
            </div>`
          : null}
        ${this.mergeError ? html`<p class="err">${this.mergeError}</p>` : null}
        ${this.mergeNotice
          ? html`<p class="notice">${this.mergeNotice}</p>`
          : null}
      </section>

      <section>
        <h2 style="margin-top:0;font-size:1.1rem;">合成流水</h2>
        ${this.merges.length === 0
          ? html`<p class="hint">暂无合成记录。</p>`
          : html`
              <table>
                <thead>
                  <tr>
                    <th>流水号</th>
                    <th>新单号</th>
                    <th>机组</th>
                    <th>笔数</th>
                    <th>均值°</th>
                    <th>来源编号</th>
                    <th>操作人</th>
                    <th>时间</th>
                  </tr>
                </thead>
                <tbody>
                  ${this.merges.map(
                    (row) => html`
                      <tr>
                        <td>${row.id}</td>
                        <td>#${row.new_log_id}</td>
                        <td>${row.turbine_code}</td>
                        <td>${row.source_count}</td>
                        <td>${row.avg_yaw_err_deg}</td>
                        <td>${row.source_ids.map((id) => "#" + id).join("、")}</td>
                        <td>${row.created_by}</td>
                        <td>${row.created_at}</td>
                      </tr>
                    `
                  )}
                </tbody>
              </table>
            `}
      </section>
    `;
  }

  private renderLogsView() {
    return html`
      ${this.isWriter
        ? html`
            <section>
              <h2 style="margin-top:0;font-size:1.1rem;">提交偏航记录</h2>
              <label>机组编号</label>
              <input
                placeholder="例如 W12"
                .value=${this.turbineCode}
                @input=${(e: Event) =>
                  (this.turbineCode = (e.target as HTMLInputElement).value)}
              />
              <label>偏航误差（度，可正可负）</label>
              <input
                type="number"
                step="0.1"
                .value=${this.yawErr}
                @input=${(e: Event) =>
                  (this.yawErr = (e.target as HTMLInputElement).value)}
              />
              <button ?disabled=${this.loading} @click=${this.submitLog}>
                提交（进入待认领队列）
              </button>
              ${this.error ? html`<p class="err">${this.error}</p>` : null}
            </section>
          `
        : null}

      <section>
        <h2 style="margin-top:0;font-size:1.1rem;">对中记录</h2>
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>机组</th>
              <th>误差°</th>
              <th>状态</th>
              <th>结论</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            ${this.logs.map(
              (row) => html`
                <tr>
                  <td>${row.id}</td>
                  <td>${row.turbine_code}</td>
                  <td>${row.yaw_err_deg}</td>
                  <td>
                    <span class="tag ${row.status === "pending" ? "pending" : "ok"}">
                      ${row.status === "pending" ? "待处理" : "已完成"}
                    </span>
                  </td>
                  <td>
                    ${row.verdict
                      ? html`<span class="tag ${this.verdictClass(row)}">${row.verdict}</span>`
                      : "—"}
                  </td>
                  <td>${row.reason ?? "—"}</td>
                </tr>
              `
            )}
          </tbody>
        </table>
      </section>
    `;
  }

  render() {
    if (!this.session) {
      return html`
        <h1>风机偏航对中台</h1>
        <p class="sub">现场技师提交偏航误差，后台 worker 认领后给出合格或偏航超差结论。</p>
        <section>
          <label>用户名</label>
          <input
            .value=${this.loginUser}
            @input=${(e: Event) =>
              (this.loginUser = (e.target as HTMLInputElement).value)}
          />
          <label>密码</label>
          <input
            type="password"
            .value=${this.loginPass}
            @input=${(e: Event) =>
              (this.loginPass = (e.target as HTMLInputElement).value)}
          />
          <button ?disabled=${this.loading} @click=${this.login}>登录</button>
          ${this.error ? html`<p class="err">${this.error}</p>` : null}
        </section>
      `;
    }

    return html`
      <h1>风机偏航对中台</h1>
      <p class="sub">
        已登录：${this.session.username}
        (${this.isWriter ? "可提交" : "只读"})
      </p>
      <section>
        <div class="row-actions">
          <button
            class=${this.view === "logs" ? "" : "secondary"}
            @click=${() => this.setView("logs")}
          >
            对中记录
          </button>
          <button
            class=${this.view === "merge" ? "" : "secondary"}
            @click=${() => this.setView("merge")}
          >
            均值合成入队
          </button>
          <span class="spacer"></span>
          <button
            class="secondary"
            ?disabled=${this.loading}
            @click=${this.refreshAll}
          >
            刷新列表
          </button>
          <button class="secondary" @click=${this.logout}>退出</button>
        </div>
      </section>

      ${this.view === "logs" ? this.renderLogsView() : this.renderMergeView()}
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "yaw-align-app": YawAlignApp;
  }
}
