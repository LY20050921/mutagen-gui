"""主窗口：实例列表 + 右侧模式按钮（需求 6.1 / 6.2 / 6.7）。

三种应用模式的行为（需求 3.4）：

======== ==================== ==================================
模式     点击实例行           右侧按钮状态
======== ==================== ==================================
普通     弹出实例操作对话框   Add 可用
编辑     打开 yml 编辑对话框   Add **禁用**，Edit 高亮
删除     弹出删除确认         Add **禁用**，Delete 红色高亮
======== ==================== ==================================

状态刷新走后台线程（``QThreadPool``），避免每 5 秒把界面卡住一次。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import APP_VERSION, MODE_DELETE, MODE_EDIT, MODE_NORMAL, SSH_CONFIG_PATH
from mutagen_core import template
from mutagen_core.cli import MutagenCLI
from mutagen_core.models import Project, SessionState
from mutagen_core.parser import index_by_name
from mutagen_core.registry import Registry
from mutagen_core.settings import Settings
from mutagen_core.states import has_stale_lock

from . import theme, widgets
from .add_dialog import AddConnectionDialog
from .op_dialog import InstanceDialog
from .settings_dialog import SettingsDialog
from .tasks import PollTask, submit
from .theme import Metrics


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        cli: MutagenCLI,
        registry: Registry,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cli = cli
        self.registry = registry
        self.settings = settings

        self._mode = MODE_NORMAL
        self._projects: list[Project] = []
        self._states: dict[str, SessionState] = {}
        self._rows: dict[str, widgets.InstanceRow] = {}
        self._stale_lock: dict[str, bool] = {}
        """实例 id -> 是否**残留锁文件**。

        残留锁会让 `project start` **永久**报 `already running`
        （成因与解法见 `mutagen_core.states.has_stale_lock`）。
        每次轮询都重算，好在用户点 Start **之前**就把问题摆出来——
        否则他只会收到一句莫名的 already running。
        """
        self._missing: dict[str, bool] = {}
        """实例 id -> yml 文件是否已不存在。

        每次轮询都会重新检测（只是几次 stat），
        所以外部删除 / 恢复 yml 文件都能实时反映到列表上。
        """
        self._polling = False
        self._daemon_ok = False
        self._mutagen_version = ""

        self.setWindowTitle("MutagenGUI")
        self.resize(Metrics.WINDOW_DEFAULT_WIDTH, Metrics.WINDOW_DEFAULT_HEIGHT)
        self.setMinimumSize(Metrics.WINDOW_MIN_WIDTH, Metrics.WINDOW_MIN_HEIGHT)

        self._build_ui()
        self._reload_projects()
        self._start_polling()

        QTimer.singleShot(0, self._initial_environment_check)
        QTimer.singleShot(50, self._refresh_states)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        outer = QHBoxLayout(central)
        outer.setContentsMargins(
            Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN, Metrics.MARGIN
        )
        outer.setSpacing(Metrics.MARGIN)

        outer.addWidget(self._build_left(), 1)
        outer.addWidget(self._build_right(), 0)

        self._status_label = QLabel()
        self._status_label.setFont(theme.meta_font())
        self._status_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        self.statusBar().addPermanentWidget(self._status_label)
        self.statusBar().setSizeGripEnabled(False)

    def _build_left(self) -> QWidget:
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索实例…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._rebuild_list)
        layout.addWidget(self._search)

        self._pages = QStackedWidget()

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)

        self._empty = widgets.EmptyState()

        self._pages.addWidget(self._list)
        self._pages.addWidget(self._empty)
        layout.addWidget(self._pages, 1)
        return left

    def _build_right(self) -> QWidget:
        right = QWidget()
        right.setFixedWidth(Metrics.SIDE_BUTTON_WIDTH)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Metrics.SIDE_BUTTON_SPACING)

        self._btn_add = widgets.SideButton("Add Connection", "plus")
        self._btn_add.clicked.connect(self._on_add_connection)
        layout.addWidget(self._btn_add)

        self._btn_edit = widgets.SideButton("Edit mode", "edit")
        self._btn_edit.clicked.connect(self._toggle_mode_edit)
        layout.addWidget(self._btn_edit)

        self._btn_delete = widgets.SideButton("Delete mode", "delete")
        self._btn_delete.clicked.connect(self._toggle_mode_delete)
        layout.addWidget(self._btn_delete)

        layout.addStretch(1)

        self._btn_settings = widgets.SideButton("Settings", "settings")
        self._btn_settings.clicked.connect(self._on_settings)
        layout.addWidget(self._btn_settings)

        self._btn_about = widgets.SideButton("About", "about")
        self._btn_about.clicked.connect(self._on_about)
        layout.addWidget(self._btn_about)

        return right

    # ----------------------------------------------------------- 实例列表 --

    def _reload_projects(self) -> None:
        self._projects = self.registry.load()
        self._rebuild_list()

    def _rebuild_list(self, *_args: object) -> None:
        self._refresh_missing_flags()

        keyword = self._search.text().strip().lower()
        visible = [
            project for project in self._projects
            if not keyword or keyword in project.name.lower()
        ]

        self._list.clear()
        self._rows.clear()

        for project in visible:
            item = QListWidgetItem(self._list)
            item.setSizeHint(QSize(0, Metrics.ROW_HEIGHT))
            row = widgets.InstanceRow(
                project,
                self._states.get(project.name),
                self._mode,
                missing=self._missing.get(project.id, False),
                stale_lock=self._stale_lock.get(project.id, False),
            )
            row.activated.connect(self._on_row_activated)
            row.inline_action.connect(self._on_inline_action)
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
            self._rows[project.id] = row

        # 实例被删空后自动回到普通模式：
        # 留在一个「没有对象可操作」的编辑/删除模式里没有意义，也容易让人困惑。
        if not self._projects and self._mode != MODE_NORMAL:
            self._mode = MODE_NORMAL
            self.statusBar().showMessage("实例已清空，已自动退出编辑 / 删除模式", 5000)

        self._pages.setCurrentWidget(self._list if self._projects else self._empty)
        self._update_mode_buttons()

    def _find_project(self, project_id: str) -> Optional[Project]:
        return next((p for p in self._projects if p.id == project_id), None)

    def _is_missing(self, project: Project) -> bool:
        """该实例的 yml 文件是否已不存在。"""
        return bool(self._missing.get(project.id, False))

    def _refresh_missing_flags(self) -> bool:
        """重新检测各实例的 yml 是否存在。

        :returns: 是否有实例的存在状态发生了变化
        """
        changed = False
        for project in self._projects:
            missing = not Path(project.yml_path).is_file()
            if self._missing.get(project.id) != missing:
                self._missing[project.id] = missing
                changed = True
        return changed

    def project_count(self) -> int:
        """当前注册表里的实例数量（供自检 / 冒烟测试查询）。"""
        return len(self._projects)

    def missing_yml_names(self) -> list[str]:
        """当前被判定为「yml 文件已丢失」的实例名（供自检 / 冒烟测试查询）。"""
        return [project.name for project in self._projects if self._is_missing(project)]

    def _is_stale_lock(self, project: Project) -> bool:
        """该实例是否处于「残留锁文件」状态。"""
        return bool(self._stale_lock.get(project.id, False))

    def stale_lock_names(self) -> list[str]:
        """当前被判定为「状态残留」的实例名（供自检 / 冒烟测试查询）。"""
        return [project.name for project in self._projects if self._is_stale_lock(project)]

    def _refresh_stale_lock_flags(self, sessions: dict[str, SessionState]) -> bool:
        """按最新会话状态重算「残留锁文件」标记。

        :param sessions: 实例名 -> 会话状态（当前真实在跑的会话）。
        :returns: 是否有实例的标记发生了变化
        """
        changed = False
        for project in self._projects:
            stale = has_stale_lock(
                project.yml_path,
                session_exists=sessions.get(project.name) is not None,
            )
            if self._stale_lock.get(project.id) != stale:
                self._stale_lock[project.id] = stale
                changed = True
        return changed

    def current_mode(self) -> str:
        """当前应用模式（供自检 / 冒烟测试查询）。"""
        return self._mode

    def _update_mode_buttons(self) -> None:
        has_projects = bool(self._projects)

        self._btn_edit.set_active(self._mode == MODE_EDIT)
        self._btn_delete.set_active(self._mode == MODE_DELETE, danger=True)

        # 需求 6.6：Edit / Delete 模式下 Add Connection 必须禁用变灰
        self._btn_add.setEnabled(self._mode == MODE_NORMAL)

        # 需求 6.7：空状态下 Edit / Delete 不可用。
        #
        # ⚠️ 但「正在生效的那个模式按钮」必须保持可用：否则在删除模式下删掉最后一个
        # 实例后，按钮自己也被禁用，就再也点不出去、卡死在删除模式里。
        self._btn_edit.setEnabled(has_projects or self._mode == MODE_EDIT)
        self._btn_delete.setEnabled(has_projects or self._mode == MODE_DELETE)

    # -------------------------------------------------------------- 模式 --

    def _toggle_mode_edit(self) -> None:
        self._set_mode(MODE_NORMAL if self._mode == MODE_EDIT else MODE_EDIT)

    def _toggle_mode_delete(self) -> None:
        self._set_mode(MODE_NORMAL if self._mode == MODE_DELETE else MODE_DELETE)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._rebuild_list()
        if mode != MODE_NORMAL:
            self.statusBar().showMessage(
                f"{'编辑' if mode == MODE_EDIT else '删除'}模式已开启："
                "点击列表中的实例执行操作；再点一次按钮退出",
                6000,
            )

    # ------------------------------------------------------ 行交互分发 --

    def _on_row_activated(self, project_id: str) -> None:
        project = self._find_project(project_id)
        if project is None:
            return

        # yml 已经没了：任何模式下的操作都无从执行，统一走「处理丢失文件」引导
        if self._is_missing(project):
            self._handle_missing_yml(project)
            return

        if self._mode == MODE_DELETE:
            self._delete_project(project)
        elif self._mode == MODE_EDIT:
            self._edit_project(project)
        else:
            self._open_instance(project)

    def _on_inline_action(self, project_id: str, action: str) -> None:
        project = self._find_project(project_id)
        if project is None:
            return

        # 复制路径跟文件在不在无关，照常执行
        if action == "copy-alpha":
            QGuiApplication.clipboard().setText(project.alpha)
            self.statusBar().showMessage("已复制本地路径", 3000)
            return

        if self._is_missing(project):
            self._handle_missing_yml(project)
            return

        if action == "edit":
            self._edit_project(project)
        elif action == "delete":
            self._delete_project(project)

    # ------------------------------------------------------------ 各操作 --

    def _open_instance(self, project: Project) -> None:
        dialog = InstanceDialog(
            self.cli, project, self._states.get(project.name), self
        )
        dialog.exec()
        self._refresh_states()

    def _on_add_connection(self) -> None:
        dialog = AddConnectionDialog(self.cli, self.settings, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reload_projects()
            self._refresh_states()

    def _edit_project(self, project: Project) -> None:
        if not project.is_editable:
            QMessageBox.information(
                self,
                "只读实例",
                "这个 yml 里包含多个同步会话。\n\n"
                "v1 只支持单会话 yml（方案 A），请手动拆分后再导入。",
            )
            return

        dialog = AddConnectionDialog(self.cli, self.settings, project=project, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reload_projects()
            self._refresh_states()

    def _delete_project(self, project: Project) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("删除 yml")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"确定要删除实例「{project.name}」的 yml 文件吗？")
        box.setInformativeText(
            f"文件：{project.yml_path}\n\n"
            "只会删除这个 yml 文件；不会动本地代码，也不会删除远程内容。"
        )
        checkbox = QCheckBox("同时终止对应的 Mutagen 会话")
        checkbox.setChecked(True)
        box.setCheckBox(checkbox)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)

        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        if checkbox.isChecked():
            # ⚠️ 必须先走 project terminate：它会**连锁文件一起清掉**。
            #    只用 sync terminate 的话，yml 旁边的 `<yml>.lock` 会残留，
            #    导致以后用同一个会话名再 start 时**永远**报「already running」
            #    （实测：连重启 daemon 都没用，必须清锁文件）。
            #    所以这里不能用 sync_terminate 代替。
            self.cli.project_terminate(project.yml_path)
            # 再兜一次：会话也可能是**绕过项目**建的
            #（如「先建会话、等远端上线」流程用的是 sync create），
            # 那种情况下 project terminate 找不到它。不存在不算错误。
            self.cli.sync_terminate(project.name, force=True)

        path = Path(project.yml_path)
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            QMessageBox.critical(self, "删除失败", f"无法删除文件：\n{exc}")
            return

        self.registry.remove(project.id)
        self._reload_projects()
        self._refresh_states()
        self.statusBar().showMessage(f"已删除「{project.name}」", 4000)

    def _remove_from_list(self, project: Project) -> None:
        """只从注册表移除条目（不删 yml、不停会话）。"""
        self.registry.remove(project.id)
        self._reload_projects()
        self.statusBar().showMessage(f"已从列表移除「{project.name}」（yml 文件保留）", 4000)

    def _handle_missing_yml(self, project: Project) -> None:
        """yml 文件已丢失：引导用户移除条目，或重新指定文件位置。"""
        box = QMessageBox(self)
        box.setWindowTitle("yml 文件不存在")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"实例「{project.name}」对应的 yml 文件找不到了。")
        box.setInformativeText(
            f"期望路径：{project.yml_path}\n\n"
            "可能是文件被外部删除或移动了。\n"
            "注册表里仍然记着这个条目，但它对应的所有操作都无法执行。"
        )
        remove_button = box.addButton(
            "移除这个条目", QMessageBox.ButtonRole.DestructiveRole
        )
        relocate_button = box.addButton(
            "重新定位文件…", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is remove_button:
            # 文件本来就不在了，只需摘掉注册表条目
            self.registry.remove(project.id)
            self._reload_projects()
            self.statusBar().showMessage(f"已移除条目「{project.name}」", 4000)
        elif clicked is relocate_button:
            self._relocate_yml(project)

    def _relocate_yml(self, project: Project) -> None:
        """让用户重新指定这个实例的 yml 文件。"""
        start_dir = str(Path(project.yml_path).parent)
        if not Path(start_dir).is_dir():
            start_dir = str(self.settings.yml_dir())

        path, _ = QFileDialog.getOpenFileName(
            self, "重新指定 yml 文件", start_dir, "YAML 文件 (*.yml *.yaml)"
        )
        if not path:
            return

        # 先解析成功再改注册表——避免指向一个用不了的文件
        try:
            document = template.parse_yaml_text(Path(path).read_text(encoding="utf-8"))
            config_obj = template.load_config(document)
        except Exception as exc:  # noqa: BLE001 - YAMLError / OSError / ValueError
            QMessageBox.critical(
                self, "无法使用这个文件", f"解析失败，注册表未被修改：\n\n{exc}"
            )
            return

        project.yml_path = str(path)
        project.name = config_obj.name or project.name
        project.alpha = config_obj.alpha
        project.beta = config_obj.beta
        project.session_count = template.count_sessions(document)

        try:
            self.registry.update(project)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "注册表写入失败", str(exc))

        self._reload_projects()
        self._refresh_states()
        self.statusBar().showMessage(f"已重新定位到：{path}", 6000)

    def _open_yml_folder(self, project: Project) -> None:
        folder = Path(project.yml_path).parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -------------------------------------------------------- 右键菜单 --

    def _show_context_menu(self, pos) -> None:  # noqa: ANN001 - Qt 回调
        item = self._list.itemAt(pos)
        if item is None:
            return
        row = self._list.itemWidget(item)
        if not isinstance(row, widgets.InstanceRow):
            return
        project = row.project

        menu = QMenu(self)
        menu.addAction("打开操作面板", lambda: self._open_instance(project))
        menu.addAction("编辑 yml", lambda: self._edit_project(project))
        menu.addSeparator()
        menu.addAction(
            "复制本地路径",
            lambda: QGuiApplication.clipboard().setText(project.alpha),
        )
        menu.addAction(
            "复制远程端点",
            lambda: QGuiApplication.clipboard().setText(project.beta),
        )
        menu.addAction(
            "复制 yml 路径",
            lambda: QGuiApplication.clipboard().setText(project.yml_path),
        )
        menu.addAction("打开 yml 所在目录", lambda: self._open_yml_folder(project))
        menu.addSeparator()
        menu.addAction("从列表移除（保留文件）", lambda: self._remove_from_list(project))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ 轮询 --

    def _start_polling(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(max(2, self.settings.poll_interval_sec) * 1000)
        self._timer.timeout.connect(self._refresh_states)
        self._timer.start()

    def _refresh_states(self) -> None:
        if self._polling:
            return
        self._polling = True

        submit(PollTask(self.cli), self, self._on_states_ready, self._on_states_failed)

    def _on_states_ready(self, sessions: object) -> None:
        self._polling = False
        self._daemon_ok = True

        states = index_by_name(sessions)  # type: ignore[arg-type]
        self._states = states

        # 顺便重测 yml 是否还在：外部删除 / 恢复文件都能实时反映到列表
        missing_changed = self._refresh_missing_flags()
        # 顺带检出残留锁文件（几次 stat 而已）——让用户在点 Start **之前**
        # 就看到「状态残留」，而不是点了之后收到一句莫名的 already running
        stale_changed = self._refresh_stale_lock_flags(states)

        for project in self._projects:
            row = self._rows.get(project.id)
            if row is None:
                continue
            if missing_changed:
                row.set_missing(self._is_missing(project))
            if stale_changed:
                row.set_stale_lock(self._is_stale_lock(project))
            row.update_state(states.get(project.name))

        self._update_status_label()

    def _on_states_failed(self, message: str) -> None:
        self._polling = False
        self._daemon_ok = False
        self._last_poll_error = message
        self._update_status_label()

    # ---------------------------------------------------------- 状态栏 --

    def _initial_environment_check(self) -> None:
        result = self.cli.version()
        if result.ok:
            lines = [line.strip() for line in result.output.splitlines() if line.strip()]
            self._mutagen_version = lines[-1] if lines else ""
        self._update_status_label()

    def _update_status_label(self) -> None:
        ssh_path = os.environ.get("MUTAGEN_SSH_PATH", "")
        daemon = "● daemon 运行中" if self._daemon_ok else "● daemon 未就绪"

        parts = [
            f"Mutagen {self._mutagen_version or '未知版本'}",
            daemon,
            f"MUTAGEN_SSH_PATH：{'已配置' if ssh_path else '未设置'}",
        ]
        if not self._daemon_ok and getattr(self, "_last_poll_error", ""):
            parts.append("（悬停查看错误）")
            self._status_label.setToolTip(self._last_poll_error)
        else:
            self._status_label.setToolTip(ssh_path)
        self._status_label.setText("    ".join(parts))

    # -------------------------------------------------------- Settings --

    def _on_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        dialog.apply_to(self.settings)
        self.settings.save()

        # mutagen 可执行文件路径可能变了，就地更新 CLI
        resolved = self.settings.mutagen_path()
        if resolved is not None:
            self.cli.exe = resolved
            self._mutagen_version = ""

        self._timer.setInterval(max(2, self.settings.poll_interval_sec) * 1000)
        self._initial_environment_check()
        self._refresh_states()

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 MutagenGUI",
            f"<b>MutagenGUI {APP_VERSION}</b><br><br>"
            "用图形界面管理多个 Mutagen 同步项目。<br>"
            "<b>一个 yml 文件 = 一个实例</b>，所有操作都围绕 yml 文件进行。<br><br>"
            f"Mutagen：{self._mutagen_version or '未知'}<br>"
            f"SSH 配置：{SSH_CONFIG_PATH}<br>"
            "需求文档：requirements.md",
        )

    # ------------------------------------------------------------ 关闭 --

    def closeEvent(self, event) -> None:  # noqa: D102, N802 - Qt 命名
        box = QMessageBox(self)
        box.setWindowTitle("关闭 MutagenGUI")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("关闭窗口不会停止同步。")
        box.setInformativeText(
            "Mutagen 会话由后台 daemon 独立维护，\n重新打开本程序时会自动恢复显示当前状态。"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Ok)

        if box.exec() == QMessageBox.StandardButton.Ok:
            self._timer.stop()
            event.accept()
        else:
            event.ignore()


__all__ = ["MainWindow"]
