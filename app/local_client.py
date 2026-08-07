from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

os.environ.setdefault(
    "AUTODEV_ENV_FILE",
    str(Path(__file__).resolve().parent.parent / "local-runner" / ".env.runner"),
)

from .config import ROOT, settings


INK = "#070b1c"
DEEP = "#0c1224"
PANEL = "#111a30"
PANEL_2 = "#17223c"
LINE = "#293b61"
PAPER = "#f2f5ff"
MUTED = "#91a2c5"
ACID = "#29a7ff"
AMBER = "#ffc857"
RED = "#ff746d"
MONO = "Consolas"

STATUS = {
    "queued": "等待执行",
    "validating": "准入校验",
    "developing": "Codex 研发中",
    "submitting": "提交代码",
    "building": "本地构建",
    "waiting_merge": "等待 PR 合并",
    "capturing": "生成合并凭证",
    "delivering": "发送交付邮件",
    "delivered": "已交付",
    "waiting_approval": "等待人工确认",
    "rejected": "准入驳回",
    "failed": "执行失败",
    "cancelled": "已取消",
}


class AutoDevConsole:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AutoDev · 执行器控制台")
        self.root.geometry("1320x820")
        self.root.minsize(1180, 680)
        self.root.configure(bg=INK)
        self.results: queue.Queue[tuple[Callable[..., None] | None, Any, Exception | None]] = queue.Queue()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.selected_id: str | None = None
        self.watcher_id: str | None = None
        self.watch_cursor = 0
        self.watch_generation = 0
        self.watch_poll_inflight = False
        self.last_live_group = ""
        self.last_live_kind = ""
        self.current_tab = "session"
        self.closing = False

        self._configure_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self._drain_results)
        self.root.after(100, self._tick_health)
        self.root.after(200, self._tick_tasks)
        self.root.after(500, self._tick_watch)
        self.root.after(900, self._tick_logs)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Auto.Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=PAPER,
            rowheight=56,
            borderwidth=0,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Auto.Treeview.Heading",
            background=DEEP,
            foreground=MUTED,
            bordercolor=LINE,
            font=(MONO, 9, "bold"),
        )
        style.map("Auto.Treeview", background=[("selected", "#214632")], foreground=[("selected", PAPER)])
        style.configure(
            "Auto.Vertical.TScrollbar",
            background="#355a47",
            troughcolor=INK,
            bordercolor=INK,
            arrowcolor=ACID,
        )

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=DEEP, height=92, highlightbackground=LINE, highlightthickness=1)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=DEEP)
        brand.pack(side="left", padx=28, pady=18)
        self.brand_image: tk.PhotoImage | None = None
        try:
            self.brand_image = tk.PhotoImage(file=str(ROOT / "app" / "static" / "brand" / "autodev-mark-64.png"))
        except tk.TclError:
            pass
        mark = tk.Label(
            brand,
            image=self.brand_image,
            text="CS" if self.brand_image is None else "",
            bg=PAPER,
            fg=INK,
            font=("Microsoft YaHei UI", 20, "bold"),
            width=58,
            height=58,
        )
        mark.pack(side="left", padx=(0, 13))
        copy = tk.Frame(brand, bg=DEEP)
        copy.pack(side="left")
        tk.Label(copy, text="AutoDev · LOCAL RUNNER", bg=DEEP, fg=ACID, font=(MONO, 9, "bold")).pack(anchor="w")
        tk.Label(copy, text="执行器控制台", bg=DEEP, fg=PAPER, font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")

        self.quota_label = tk.Label(header, text="CODEX 额度读取中", bg=DEEP, fg=MUTED, font=(MONO, 10))
        self.quota_label.pack(side="right", padx=(12, 28))
        self.status_label = tk.Label(header, text="● 连接中", bg=DEEP, fg=AMBER, font=("Microsoft YaHei UI", 10, "bold"))
        self.status_label.pack(side="right", padx=12)

        body = tk.PanedWindow(self.root, orient="horizontal", bg=INK, bd=0, sashwidth=7, sashrelief="flat")
        body.pack(fill="both", expand=True, padx=20, pady=20)

        left = tk.Frame(body, bg=PANEL, highlightbackground=LINE, highlightthickness=1, width=620)
        right = tk.Frame(body, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        body.add(left, minsize=560, width=620)
        body.add(right, minsize=560)
        self._build_task_list(left)
        self._build_detail(right)

        controls = tk.Frame(self.root, bg=DEEP, height=62, highlightbackground=LINE, highlightthickness=1)
        controls.pack(fill="x")
        controls.pack_propagate(False)
        tk.Label(controls, text="执行器控制 / RUNNER CONTROL", bg=DEEP, fg=MUTED, font=(MONO, 9, "bold")).pack(side="left", padx=25)
        self._control_button(controls, "启动执行器", "start.ps1", ACID).pack(side="left", padx=5, pady=12)
        self._control_button(controls, "停止执行器", "stop.ps1", RED).pack(side="left", padx=5, pady=12)
        self._control_button(controls, "重启执行器", "restart.ps1", AMBER).pack(side="left", padx=5, pady=12)
        self.footer = tk.Label(controls, text="本机接口 127.0.0.1:28766 · 详细会话不落盘", bg=DEEP, fg=MUTED, font=(MONO, 9))
        self.footer.pack(side="right", padx=25)

    def _build_task_list(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=PANEL)
        top.pack(fill="x", padx=18, pady=(18, 12))
        tk.Label(top, text="当前 / 最近 · CURRENT / RECENT", bg=PANEL, fg=ACID, font=(MONO, 9, "bold")).pack(anchor="w")
        row = tk.Frame(top, bg=PANEL)
        row.pack(fill="x", pady=(5, 0))
        tk.Label(row, text="执行任务", bg=PANEL, fg=PAPER, font=("Microsoft YaHei UI", 17, "bold")).pack(side="left")
        tk.Button(
            row, text="刷新", command=lambda: self._tick_tasks(force=True), bg=PANEL_2, fg=ACID,
            activebackground="#214632", activeforeground=PAPER, relief="flat", padx=12, pady=5,
        ).pack(side="right")
        tree_frame = tk.Frame(parent, bg=PANEL)
        tree_frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self.task_tree = ttk.Treeview(
            tree_frame, style="Auto.Treeview",
            columns=("id", "requester", "submitted", "status"), show="headings", selectmode="browse",
        )
        self.task_scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", style="Auto.Vertical.TScrollbar", command=self.task_tree.yview
        )
        self.task_tree.configure(yscrollcommand=self.task_scrollbar.set)
        self.task_tree.heading("id", text="TFS / 项目")
        self.task_tree.heading("requester", text="提交人")
        self.task_tree.heading("submitted", text="提交时间")
        self.task_tree.heading("status", text="状态")
        self.task_tree.column("id", width=275, minwidth=220, anchor="w")
        self.task_tree.column("requester", width=85, minwidth=70, anchor="w")
        self.task_tree.column("submitted", width=105, minwidth=95, anchor="w")
        self.task_tree.column("status", width=90, minwidth=80, anchor="w")
        self.task_tree.pack(side="left", fill="both", expand=True)
        self.task_scrollbar.pack(side="right", fill="y")
        self.task_tree.bind("<<TreeviewSelect>>", self._select_task)

    def _build_detail(self, parent: tk.Frame) -> None:
        head = tk.Frame(parent, bg=PANEL, height=106)
        head.pack(fill="x", padx=22, pady=(18, 0))
        head.pack_propagate(False)
        self.detail_code = tk.Label(head, text="SELECT A TASK", bg=PANEL, fg=ACID, font=(MONO, 9, "bold"))
        self.detail_code.pack(anchor="w")
        self.detail_title = tk.Label(
            head, text="选择左侧任务查看研发过程", bg=PANEL, fg=PAPER,
            font=("Microsoft YaHei UI", 18, "bold"), anchor="w",
        )
        self.detail_title.pack(fill="x", pady=(7, 0))
        self.detail_activity = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 9), anchor="w")
        self.detail_activity.pack(fill="x", pady=(6, 0))

        tabs = tk.Frame(parent, bg=DEEP, highlightbackground=LINE, highlightthickness=1)
        tabs.pack(fill="x")
        self.tab_buttons: dict[str, tk.Button] = {}
        for key, label in (("session", "研发会话"), ("detail", "任务详情"), ("logs", "执行器日志")):
            button = tk.Button(
                tabs, text=label, command=lambda value=key: self._switch_tab(value), bg=DEEP, fg=MUTED,
                activebackground=PANEL_2, activeforeground=PAPER, relief="flat", padx=22, pady=11,
            )
            button.pack(side="left")
            self.tab_buttons[key] = button

        content = tk.Frame(parent, bg=INK)
        content.pack(fill="both", expand=True)
        self.texts: dict[str, tk.Text] = {}
        self.text_panels: dict[str, tk.Frame] = {}
        self.text_scrollbars: dict[str, ttk.Scrollbar] = {}
        for key in ("session", "detail", "logs"):
            panel = tk.Frame(content, bg=INK)
            text = tk.Text(
                panel, bg=INK, fg=PAPER, insertbackground=ACID, relief="flat", wrap="word",
                padx=24, pady=22, font=("Microsoft YaHei UI", 10), spacing1=2, spacing3=7,
            )
            scrollbar = ttk.Scrollbar(
                panel, orient="vertical", style="Auto.Vertical.TScrollbar", command=text.yview
            )
            text.configure(yscrollcommand=scrollbar.set)
            text.tag_configure("label", foreground=ACID, font=(MONO, 9, "bold"), spacing1=12)
            text.tag_configure("assistant", foreground=PAPER)
            text.tag_configure(
                "assistant_heading", foreground=ACID,
                font=("Microsoft YaHei UI", 12, "bold"), spacing1=12, spacing3=4,
            )
            text.tag_configure("assistant_bullet", foreground=PAPER, lmargin1=18, lmargin2=34)
            text.tag_configure("reasoning", foreground="#afd2c0")
            text.tag_configure("command", foreground="#b9d8ff", font=(MONO, 9))
            text.tag_configure("file", foreground=AMBER, font=(MONO, 9))
            text.tag_configure("status", foreground=MUTED, font=(MONO, 9))
            text.tag_configure("error", foreground=RED)
            text.configure(state="disabled")
            self.texts[key] = text
            self.text_panels[key] = panel
            self.text_scrollbars[key] = scrollbar
            text.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
        self._switch_tab("session")
        self._set_text("session", "打开任务即开始接收此后产生的 Codex 输出。\n关闭客户端或切换任务后立即停止采集，不会保存历史会话。", "status")

    def _control_button(self, parent: tk.Frame, label: str, script: str, color: str) -> tk.Button:
        return tk.Button(
            parent, text=label, command=lambda: self._run_control(script, label), bg=PANEL_2, fg=color,
            activebackground="#214632", activeforeground=PAPER, relief="flat", padx=14, pady=7,
        )

    def _switch_tab(self, name: str) -> None:
        self.current_tab = name
        for key, panel in self.text_panels.items():
            if key == name:
                panel.pack(fill="both", expand=True)
            else:
                panel.pack_forget()
        for key, button in self.tab_buttons.items():
            button.configure(fg=ACID if key == name else MUTED, bg=PANEL_2 if key == name else DEEP)

    def _select_task(self, _event: object = None) -> None:
        selected = self.task_tree.selection()
        if not selected:
            return
        request_id = selected[0]
        if request_id == self.selected_id:
            return
        self._stop_watch()
        self.selected_id = request_id
        task = self.tasks.get(request_id, {})
        self.detail_code.configure(text=f"RUN / {request_id[:8].upper()} / TFS #{task.get('work_item_id', '—')}")
        self.detail_title.configure(text=task.get("title") or "正在读取需求…")
        self.detail_activity.configure(text=task.get("current_activity") or STATUS.get(task.get("status"), task.get("status", "")))
        self._set_text("session", "仅查看期间传输 · 正在连接 Codex 实时会话…", "status")
        self.last_live_group = ""
        self.last_live_kind = ""
        generation = self.watch_generation
        self._async(lambda: self._monitor_json("/api/watch/start", method="POST", payload={"request_id": request_id}),
                    lambda result: self._watch_started(result, request_id, generation),
                    lambda exc: self._watch_error(exc, generation))
        self._async(
            lambda: self._cloud_json(f"/api/runner/requests/{request_id}"),
            self._render_task_detail,
            lambda exc: self._set_text("detail", f"任务详情读取失败：{exc}", "error"),
        )

    def _watch_started(self, result: dict[str, Any], request_id: str, generation: int) -> None:
        if generation != self.watch_generation or request_id != self.selected_id:
            return
        self.watcher_id = result.get("watcher_id")
        self.watch_cursor = int(result.get("cursor") or 0)
        self._append_text("session", "\n● 实时通道已打开，等待新的 Codex 输出…\n", "status")

    def _render_task_detail(self, result: dict[str, Any]) -> None:
        detail = result.get("request") or {}
        lines = [
            f"任务编号      {detail.get('id', '—')}",
            f"TFS 编号     #{detail.get('work_item_id', '—')}",
            f"项目          {detail.get('project_name', '—')}",
            f"发起人        {detail.get('requester_name', '—')}",
            f"状态          {STATUS.get(detail.get('status'), detail.get('status', '—'))}",
            f"分支          {detail.get('branch_name') or '—'}",
            f"PR            {detail.get('pr_url') or '—'}",
            f"Codex 会话    {detail.get('codex_thread_id') or '执行中/尚未生成'}",
            "",
            "PIPELINE",
        ]
        for step in detail.get("steps", []):
            lines.append(f"  {step.get('name')}  [{step.get('status')}]  {step.get('message') or ''}")
        if detail.get("result_summary"):
            lines.extend(("", "研发结论", str(detail["result_summary"])))
        if detail.get("error_message"):
            lines.extend(("", "错误信息", str(detail["error_message"])))
        lines.extend(("", "RECENT EVENTS"))
        for event in (detail.get("events") or [])[:30]:
            lines.append(f"  {self._format_time(event.get('created_at'))}  {event.get('message', '')}")
        self._set_text("detail", "\n".join(lines), "status")

    def _tick_health(self) -> None:
        if self.closing:
            return
        self._async(lambda: self._monitor_json("/healthz", timeout=1.5), self._render_health, self._health_error)
        self.root.after(2500, self._tick_health)

    def _render_health(self, data: dict[str, Any]) -> None:
        state = data.get("state", "idle")
        self.status_label.configure(text="● 执行器运行中" if state == "working" else "● 执行器在线", fg=ACID)
        usage = data.get("codex_usage") or {}
        primary = usage.get("primary") or {}
        if usage.get("available"):
            plan = str(usage.get("plan_type") or "unknown").upper()
            remaining = primary.get("remaining_percent")
            self.quota_label.configure(text=f"CODEX {plan} · 剩余 {remaining}%" if remaining is not None else f"CODEX {plan}")
        else:
            self.quota_label.configure(text="CODEX 额度暂不可用")

    def _health_error(self, _exc: Exception) -> None:
        self.status_label.configure(text="● 执行器离线", fg=RED)
        self.quota_label.configure(text="本机接口未连接")

    def _tick_tasks(self, force: bool = False) -> None:
        if self.closing:
            return
        self._async(
            lambda: self._cloud_json("/api/runner/tasks?" + urllib.parse.urlencode({"runner_id": settings.runner_id, "limit": 100})),
            self._render_tasks,
            lambda exc: self.footer.configure(text=f"云端任务读取失败 · {exc}"),
        )
        if not force:
            self.root.after(5000, self._tick_tasks)

    def _render_tasks(self, data: dict[str, Any]) -> None:
        tasks = data.get("tasks") or []
        self.footer.configure(
            text=f"本机接口 {settings.runner_monitor_host}:{settings.runner_monitor_port} · 详细会话不落盘"
        )
        self.tasks = {task["id"]: task for task in tasks}
        previous = self.selected_id
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        for task in tasks:
            work = f"#{task.get('work_item_id')}  {task.get('title') or '正在读取需求…'}\n{task.get('project_name') or ''}"
            status = STATUS.get(task.get("status"), task.get("status", ""))
            self.task_tree.insert(
                "", "end", iid=task["id"],
                values=(work, task.get("requester_name") or "—", self._format_datetime(task.get("created_at")), status),
            )
        if previous and previous in self.tasks:
            self.task_tree.selection_set(previous)
        elif tasks:
            self.task_tree.selection_set(tasks[0]["id"])
            self.task_tree.event_generate("<<TreeviewSelect>>")

    def _tick_watch(self) -> None:
        if self.closing:
            return
        if self.selected_id and self.watcher_id and not self.watch_poll_inflight:
            request_id, watcher_id, cursor = self.selected_id, self.watcher_id, self.watch_cursor
            generation = self.watch_generation
            query = urllib.parse.urlencode({"request_id": request_id, "watcher_id": watcher_id, "after": cursor})
            self.watch_poll_inflight = True
            self._async(
                lambda: self._monitor_json(f"/api/watch/poll?{query}", timeout=2),
                lambda result: self._watch_result(result, generation),
                lambda exc: self._watch_error(exc, generation),
            )
        self.root.after(650, self._tick_watch)

    def _render_live_events(self, data: dict[str, Any]) -> None:
        self.watch_cursor = max(self.watch_cursor, int(data.get("cursor") or 0))
        for event in data.get("events") or []:
            kind = event.get("kind") or "status"
            if kind == "status":
                continue
            group = event.get("group") or f"seq-{event.get('seq')}"
            delta = bool(event.get("delta"))
            if not delta or group != self.last_live_group or kind != self.last_live_kind:
                label = {
                    "assistant": "CODEX 研发结论",
                    "reasoning": "分析摘要",
                    "command": "终端执行",
                    "file": "文件变更",
                    "plan": "研发计划",
                }.get(kind, "研发过程")
                self._append_text("session", f"\n{label}  {self._format_time(event.get('at'))}\n", "label")
            content = str(event.get("content") or "")
            if event.get("format") == "markdown" and not delta:
                self._append_markdown("session", content)
            else:
                self._append_text("session", content, kind)
            if not content.endswith("\n") and not delta:
                self._append_text("session", "\n", kind)
            self.last_live_group, self.last_live_kind = group, kind

    def _watch_result(self, data: dict[str, Any], generation: int) -> None:
        if generation != self.watch_generation:
            return
        self.watch_poll_inflight = False
        self._render_live_events(data)

    def _watch_error(self, exc: Exception, generation: int) -> None:
        if generation != self.watch_generation:
            return
        self.watch_poll_inflight = False
        if self.watcher_id or self.selected_id:
            self._append_text("session", f"\n实时会话暂不可用：{exc}\n", "error")
        self.watcher_id = None

    def _tick_logs(self) -> None:
        if self.closing:
            return
        path = settings.data_dir / "logs" / "runner.log"
        try:
            if path.is_file():
                text = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-700:])
            else:
                text = "执行器日志尚未生成。"
            self._set_text("logs", text, "command")
        except OSError as exc:
            self._set_text("logs", str(exc), "error")
        self.root.after(4000, self._tick_logs)

    def _run_control(self, script: str, label: str) -> None:
        path = ROOT / "local-runner" / script
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
                cwd=str(ROOT),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.status_label.configure(text=f"● {label}指令已发送", fg=AMBER)
            self.root.after(2500, lambda: self._tick_health())
        except OSError as exc:
            messagebox.showerror("执行失败", str(exc), parent=self.root)

    def _stop_watch(self) -> None:
        request_id, watcher_id = self.selected_id, self.watcher_id
        self.watch_generation += 1
        self.watch_poll_inflight = False
        self.watcher_id = None
        self.watch_cursor = 0
        if request_id and watcher_id:
            self._async(
                lambda: self._monitor_json(
                    "/api/watch/stop", method="POST",
                    payload={"request_id": request_id, "watcher_id": watcher_id}, timeout=1,
                ),
                lambda _result: None,
            )

    def _monitor_json(
        self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 3
    ) -> dict[str, Any]:
        url = f"http://{settings.runner_monitor_host}:{settings.runner_monitor_port}{path}"
        return self._request_json(url, method=method, payload=payload, timeout=timeout)

    def _cloud_json(self, path: str, *, timeout: float = 12) -> dict[str, Any]:
        if not settings.cloud_url or not settings.runner_token:
            raise RuntimeError("本机 .env.runner 尚未配置云端地址或执行器令牌")
        return self._request_json(
            f"{settings.cloud_url}{path}", timeout=timeout,
            headers={"Authorization": f"Bearer {settings.runner_token}"},
        )

    @staticmethod
    def _request_json(
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float = 3,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=body, method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except Exception:
                detail = None
            raise RuntimeError(detail or f"接口返回 {exc.code}") from exc

    def _async(
        self,
        work: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        failure: Callable[[Exception], None] | None = None,
    ) -> None:
        def run() -> None:
            try:
                self.results.put((success, work(), None))
            except Exception as exc:
                self.results.put((failure, None, exc))

        threading.Thread(target=run, daemon=True).start()

    def _drain_results(self) -> None:
        while True:
            try:
                callback, result, error = self.results.get_nowait()
            except queue.Empty:
                break
            if callback:
                try:
                    callback(error if error is not None else result)
                except Exception:
                    pass
        if not self.closing:
            self.root.after(80, self._drain_results)

    def _set_text(self, name: str, value: str, tag: str = "status") -> None:
        text = self.texts[name]
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", value, tag)
        text.configure(state="disabled")

    def _append_text(self, name: str, value: str, tag: str = "status") -> None:
        text = self.texts[name]
        text.configure(state="normal")
        text.insert("end", value, tag)
        text.see("end")
        text.configure(state="disabled")

    def _append_markdown(self, name: str, value: str) -> None:
        text = self.texts[name]
        text.configure(state="normal")
        for line in value.splitlines():
            if line.startswith("### "):
                text.insert("end", line[4:] + "\n", "assistant_heading")
            elif line.startswith("- "):
                text.insert("end", "• " + line[2:] + "\n", "assistant_bullet")
            else:
                text.insert("end", line + "\n", "assistant")
        text.see("end")
        text.configure(state="disabled")

    @staticmethod
    def _format_time(value: Any) -> str:
        if not value:
            return ""
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
        except ValueError:
            return str(value)

    @staticmethod
    def _format_datetime(value: Any) -> str:
        if not value:
            return "—"
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
        except ValueError:
            return str(value)

    def close(self) -> None:
        self.closing = True
        request_id, watcher_id = self.selected_id, self.watcher_id
        if request_id and watcher_id:
            try:
                self._monitor_json(
                    "/api/watch/stop", method="POST",
                    payload={"request_id": request_id, "watcher_id": watcher_id}, timeout=0.8,
                )
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        root.iconbitmap(str(ROOT / "app" / "static" / "brand" / "favicon.ico"))
    except tk.TclError:
        pass
    AutoDevConsole(root)
    root.mainloop()


if __name__ == "__main__":
    main()
