"""AimLoot 应用入口。

启动流程:
1. 加载本地数据 (AppState);
2. 启动全局键鼠监听 (QTimer + GetAsyncKeyState 轮询, 主线程);
3. 创建主悬浮 Widget；
4. 主线程接收键鼠「操作」事件，统一更新数据 & UI。
"""
from __future__ import annotations

import logging
import signal
import sys

from PySide6.QtCore import QLockFile, QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QMenu,
    QSystemTrayIcon,
    QTextEdit,
)

from . import chest_opening
from .branding import APP_NAME, TRAY_TOOLTIP
from .game_launcher import launch_pet_arena, launch_pixel_tactics, launch_word_arena
from .input_monitor import InputMonitor
from .power_monitor import PowerMonitor
from .inventory_dialog import InventoryDialog
from .logging_setup import setup_logging
from .models import AppState, Reward
from .reward_system import (
    SHUFFLE_INTERVAL_SEC,
    ensure_roll_runtime,
    maybe_roll,
    reshuffle_roll_params,
)
from .sfx import SfxPlayer
from .storage import (
    SaveRejectedError,
    get_data_dir,
    get_data_file,
    load_state,
    save_state,
    take_load_warning,
    take_migration_warning,
)
from .task_dialog import TaskDialog
from .task_manager import TaskManager
from .ui_text import format_reward_gain
from .widget import FloatingWidget

logger = logging.getLogger(__name__)


class OpBridge(QObject):
    """把后台线程的操作事件搬运到 Qt 主线程。"""
    op_happened = Signal()


class Application(QObject):
    def __init__(self, qt_app: QApplication):
        # 高 DPI 处理 (PySide6 默认即开启)
        super().__init__()
        self.qt_app = qt_app
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.qt_app.setApplicationName(APP_NAME)

        self.state: AppState = load_state()
        mig_warning = take_migration_warning()
        if mig_warning:
            logger.warning("存档迁移: %s", mig_warning.replace("\n", " "))
            QMessageBox.warning(None, APP_NAME, mig_warning)
        load_warning = take_load_warning()
        if load_warning:
            logger.warning("存档恢复: %s", load_warning.replace('\n', ' '))
            QMessageBox.warning(None, APP_NAME, load_warning)
        ensure_roll_runtime(self.state)
        self.power_monitor = PowerMonitor()
        self.manager = TaskManager(self.state, self.power_monitor)
        data_path = get_data_file()
        mtime = data_path.stat().st_mtime if data_path.exists() else None
        self.manager.load_runtime_log(data_mtime=mtime)
        if self.manager.recover_stuck_subtask_rewards():
            n = self.manager.last_repaired_claimed_count
            logger.info(
                "debug-4283d4 启动修复完成 repaired_claimed=%d",
                n,
            )
            logger.info("启动时恢复了卡住的子任务奖励")
            self._safe_save()
            if n:
                QMessageBox.information(
                    None,
                    APP_NAME,
                    f"已修复 {n} 个无法点完成的子目标。\n"
                    "时长已经达标的，选中后即可点「完成」。",
                )
        self.sfx = SfxPlayer(self.state.settings)
        if self.sfx.capable():
            self.sfx.prewarm()
            # 休眠唤醒后音频设备常失效；丢掉旧 QMediaPlayer，下次预热再建
            self._last_app_state: Qt.ApplicationState = Qt.ApplicationState.ApplicationActive
            self.qt_app.applicationStateChanged.connect(self._on_app_state_changed)

        self.widget = FloatingWidget(self.state, self.manager)
        self.widget.request_task_dialog.connect(self.show_task_dialog)
        self.widget.request_inventory_dialog.connect(self.show_inventory)
        self.widget.request_quit.connect(self.quit)
        self.widget.subtask_claimed.connect(self._on_subtask_claimed)
        self.widget.state_changed.connect(self._on_widget_state_changed)
        self.widget.ease_point_reached.connect(self._on_ease_point_reached)
        self.widget.goal_completed.connect(self._on_goal_completed)
        self.widget.chest_bagged.connect(self._on_chest_bagged)

        # 桥接全局输入事件
        self.bridge = OpBridge()
        self.bridge.op_happened.connect(self._on_operation)
        self.monitor = InputMonitor(on_op=self.bridge.op_happened.emit)
        if not self.monitor.available():
            logger.warning("全局键鼠监听不可用")
            QMessageBox.warning(
                None, APP_NAME,
                "全局键鼠监听不可用。\n操作计数仅限点击悬浮窗按钮时触发。",
            )
        else:
            self.monitor.start()

        # 周期性自动存档 + 操作后防抖存档（避免仅依赖 15s 定时器）
        self.save_timer = QTimer(self)
        self.save_timer.setInterval(15_000)
        self.save_timer.timeout.connect(self._auto_save)
        self.save_timer.start()
        self._save_debounce = QTimer(self)
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(3_000)
        self._save_debounce.timeout.connect(self._safe_save)

        # 每 10 分钟重抽开奖概率与奖励范围
        self._roll_shuffle_timer = QTimer(self)
        self._roll_shuffle_timer.setInterval(SHUFFLE_INTERVAL_SEC * 1000)
        self._roll_shuffle_timer.timeout.connect(self._on_roll_shuffle_timer)
        self._roll_shuffle_timer.start()

        # 系统托盘
        self.tray = self._build_tray()

        # 引用子窗口避免被 GC
        self._task_dialog = None
        self._inv_dialog = None
        self._save_reject_notified = False

        # 合并高频按键触发的 UI 刷新，避免每键整窗重绘
        self._roll_changed = False
        self._pending_roll_reward: Reward | None = None
        self._ui_flush_timer = QTimer(self)
        self._ui_flush_timer.setSingleShot(True)
        self._ui_flush_timer.setInterval(100)
        self._ui_flush_timer.timeout.connect(self._flush_ui)

        # 让 Ctrl+C 在终端启动时也能干净退出
        try:
            signal.signal(signal.SIGINT, lambda *_: self.quit())
        except Exception:
            pass

        self._place_widget_initial()
        self.widget.show()

        inv = self.state.inventory
        logger.info(
            "%s 启动完成 (data_dir=%s, total_ops=%d, gold=%.1f, diamond=%.1f, tasks=%d)",
            APP_NAME,
            get_data_dir(), self.state.total_operations, inv.gold, inv.diamond,
            len(self.state.tasks),
        )
        # #region agent log
        from .task_manager import _agent_dbg
        _agent_dbg("A", "main.py:startup", "lag-v2 process loaded", {"ops": self.state.total_operations})
        # #endregion

    # ---------- 系统托盘 ----------
    def _build_tray(self) -> QSystemTrayIcon:
        icon = self._make_icon()
        tray = QSystemTrayIcon(icon, parent=self)
        tray.setToolTip(TRAY_TOOLTIP)
        menu = QMenu()

        act_show = QAction("显示悬浮窗", menu)
        act_show.triggered.connect(self._show_widget)
        menu.addAction(act_show)

        act_tasks = QAction("目标管理", menu)
        act_tasks.triggered.connect(self.show_task_dialog)
        menu.addAction(act_tasks)

        act_inv = QAction("奖励背包", menu)
        act_inv.triggered.connect(self.show_inventory)
        menu.addAction(act_inv)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        self._tray_menu = menu  # 保留引用以防 GC
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _make_icon(self) -> QIcon:
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#3a5cff"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(2, 2, 60, 60, 14, 14)
        p.setPen(QColor("#ffffff"))
        f = QFont("Segoe UI", 28, QFont.Bold)
        p.setFont(f)
        p.drawText(pix.rect(), Qt.AlignCenter, "A")
        p.end()
        return QIcon(pix)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_widget()

    def _show_widget(self) -> None:
        self.widget.showNormal()
        self.widget.raise_()
        self.widget.activateWindow()

    # ---------- 初始位置 ----------
    def _place_widget_initial(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        # 默认放置到屏幕右上角，留出 24px 边距。
        # 不要 adjustSize()：启动时布局未算完会算出错误高度，
        # 显示后重排把窗口“撑大”，用户感知为窗口自己变大。
        size = self.widget.size()
        x = geo.right() - size.width() - 24
        y = geo.top() + 80
        self.widget.move(x, y)

    # ---------- 事件处理 ----------
    @staticmethod
    def _typing_in_app() -> bool:
        """焦点在应用内文本框时，不计操作、不刷新（避免子任务输入卡顿）。"""
        w = QApplication.focusWidget()
        if w is None:
            return False
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))

    def _schedule_ui_flush(
        self,
        *,
        roll_changed: bool = False,
        reward: Reward | None = None,
    ) -> None:
        if roll_changed:
            self._roll_changed = True
            self._pending_roll_reward = reward
        self._ui_flush_timer.start()

    def _flush_ui(self) -> None:
        # 鼠标按着时不刷新：避免 press→重建/polish 把点击吃掉
        if self.widget.is_user_moving():
            self._ui_flush_timer.start()
            return
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self._ui_flush_timer.start()
            return
        roll_changed = self._roll_changed
        reward = self._pending_roll_reward if roll_changed else None
        self._roll_changed = False
        self._pending_roll_reward = None
        self.widget.refresh_stats(roll_changed=roll_changed, reward=reward)
        if self._task_dialog is not None and self._task_dialog.isVisible():
            self._task_dialog.refresh_stats()
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()

    def _notify_subtask_claim(self, reward: Reward, *, title: str = "") -> None:
        prefix = f"「{title}」" if title else "目标"
        text = f"{prefix}已领取 {format_reward_gain(reward.gold, reward.diamond)}"
        if self.tray.isVisible():
            self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Information, 2500)

    def _on_app_state_changed(self, state: Qt.ApplicationState) -> None:
        # 仅在休眠唤醒时丢掉旧播放器，避免点击悬浮窗获得焦点时误杀正在播放的音效
        if (
            state == Qt.ApplicationState.ApplicationActive
            and self._last_app_state == Qt.ApplicationState.ApplicationSuspended
        ):
            self.sfx.invalidate()
        self._last_app_state = state

    def _on_operation(self) -> None:
        if self._typing_in_app():
            return
        self.manager.note_activity()
        if self.manager.will_count_operation():
            self.sfx.play_op_tick()
        self.state.total_operations += 1
        reward = maybe_roll(self.state)
        if reward is not None:
            if self.manager.will_count_operation():
                self.sfx.play_grid_full()
            logger.debug("操作 #%d: 开奖 gold=%.1f diamond=%.1f",
                         self.state.total_operations, reward.gold, reward.diamond)
        subtask_reward = self.manager.record_operation(reward)
        if (
            subtask_reward is not None
            and not subtask_reward.is_empty()
            and self.state.settings.get("sound_on_roll_hit", True)
        ):
            self.sfx.play_roll_hit(subtask_reward)
        self.widget.note_operation()
        # #region agent log
        if subtask_reward is not None and not subtask_reward.is_empty():
            from .task_manager import _agent_dbg
            _agent_dbg(
                "D",
                "main.py:_on_operation",
                "roll then kick",
                {"ops": self.state.total_operations, "g": subtask_reward.gold, "d": subtask_reward.diamond},
            )
        # #endregion
        # 金钻只在记入目标时变；暂停中的开奖不要 kick
        if subtask_reward is not None and not subtask_reward.is_empty():
            self.widget.kick_currency_display()
        self._schedule_ui_flush(roll_changed=reward is not None, reward=reward)
        self._save_debounce.start()

    def _on_roll_shuffle_timer(self) -> None:
        reshuffle_roll_params(self.state)
        self.widget.refresh_roll_meta()
        self._safe_save()
        logger.info("10 分钟定时重抽开奖参数完成")

    # ---------- 子窗口 ----------
    def _on_ease_point_reached(self) -> None:
        if self.manager.will_count_operation():
            self.sfx.play_ease_full()

    def _on_goal_completed(self) -> None:
        self.sfx.play_goal_complete()

    def _on_subtask_claimed(self, title: str, reward: Reward) -> None:
        logger.info("领取子目标「%s」: gold=%.1f diamond=%.1f", title, reward.gold, reward.diamond)
        self._notify_subtask_claim(reward, title=title)
        QMessageBox.information(
            self.widget,
            "领取成功",
            f"「{title}」\n获得 {format_reward_gain(reward.gold, reward.diamond)}",
        )

    def _on_chest_bagged(self) -> None:
        self.sfx.play_chest_get()
        n = len(self.state.inventory.chests)
        logger.info("宝箱进背包 (chests=%d)", n)
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()
        self._save_debounce.start()

    def _on_widget_state_changed(self) -> None:
        # 先刷新 UI，再写盘，避免暂停/开始时先卡在存档上
        self.widget.refresh()
        if self._task_dialog is not None and self._task_dialog.isVisible():
            self._task_dialog.refresh()
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()
        self._save_debounce.start()

    def show_task_dialog(self) -> None:
        if self._task_dialog is None:
            self._task_dialog = TaskDialog(self.state, self.manager, parent=self.widget)
            self._task_dialog.state_changed.connect(self._on_widget_state_changed)
            self._task_dialog.subtask_claimed.connect(self._on_subtask_claimed)
            self._task_dialog.goal_completed.connect(self._on_goal_completed)
        self._task_dialog.refresh()
        self._task_dialog.show()
        self._task_dialog.raise_()
        self._task_dialog.activateWindow()

    def show_inventory(self) -> None:
        if self._inv_dialog is None:
            self._inv_dialog = InventoryDialog(self.state, parent=self.widget)
            self._inv_dialog.request_play_game.connect(self.play_pet_arena)
            self._inv_dialog.request_play_grid_game.connect(self.play_pixel_tactics)
            self._inv_dialog.request_play_word_game.connect(self.play_word_arena)
            self._inv_dialog.request_start_unlock.connect(self._on_request_start_unlock)
            self._inv_dialog.request_open_chest.connect(self._on_request_open_chest)
            self._inv_dialog.request_speedup.connect(self._on_request_speedup)
            self._inv_dialog.request_instant_open.connect(self._on_request_instant_open)
            self._inv_dialog.request_open_all_ready.connect(self._on_request_open_all_ready)
        self._inv_dialog.refresh()
        self._inv_dialog.show()
        self._inv_dialog.raise_()
        self._inv_dialog.activateWindow()

    def _on_request_start_unlock(self, rarity: int) -> None:
        """点「解锁」：为该稀有度第一把未解锁的箱子启动倒计时。"""
        state = self.state
        for chest in state.inventory.chests:
            if chest.rarity == rarity and chest.unlock_started_at is None:
                if not chest_opening.start_unlock(state, chest):
                    # 槽位已满：刷新让按钮恢复禁用态即可
                    break
                logger.info("宝箱开始解锁 (rarity=%d, chests=%d)", rarity, len(state.inventory.chests))
                break
        self._safe_save()
        if self._inv_dialog is not None:
            self._inv_dialog.refresh()

    def _on_request_open_chest(self, rarity: int) -> None:
        """点「开箱」：生成概率结果 → 提交状态 → 播放动画 → 刷新。"""
        state = self.state
        chest = next(
            (c for c in state.inventory.chests
             if c.rarity == rarity and chest_opening.is_ready(c)),
            None,
        )
        if chest is None:
            return
        result = chest_opening.generate_open_result(rarity)
        chest_opening.open_chest(state, chest, result)
        logger.info(
            "开箱 (rarity=%d, letters=%d, gold=%+.2f, diamond=%+.2f)",
            rarity, len(result.letters), result.gold, result.diamond,
        )
        self._safe_save()  # 先提交+保存：动画中崩溃不丢奖励
        self.widget.refresh_stats(roll_changed=False)
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()

        from .ui_open_chest_dialog import OpenChestDialog
        totals = {
            (letter, rar): state.inventory.letters.get(letter, [0] * 5)[rar]
            for letter, rar in result.letters
        }
        dialog = OpenChestDialog(result, rarity, parent=self.widget, letter_totals=totals)
        dialog.exec()
        self.widget.refresh_stats(roll_changed=False)
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()

    def _first_chest_of_rarity(self, rarity: int):
        return next(
            (c for c in self.state.inventory.chests if c.rarity == rarity),
            None,
        )

    def _refresh_after_chest(self) -> None:
        self._safe_save()
        self.widget.refresh_stats(roll_changed=False)
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()

    def _on_request_speedup(self, rarity: int) -> None:
        """花金币立刻完成该稀有度第一只未就绪箱的解锁。"""
        chest = self._first_chest_of_rarity(rarity)
        if chest is None:
            return
        ok, msg = chest_opening.try_speedup(self.state, chest)
        if not ok:
            QMessageBox.warning(self.widget, "无法加速", msg)
            if self._inv_dialog is not None:
                self._inv_dialog.refresh()
            return
        logger.info("宝箱加速 (rarity=%d): %s", rarity, msg)
        self._refresh_after_chest()

    def _on_request_instant_open(self, rarity: int, count: int = 1) -> None:
        """花金币秒开该稀有度最多 count 只未就绪箱（跳过动画）。"""
        ok, msg, opens = chest_opening.try_instant_open_n(
            self.state, rarity, count,
        )
        if not ok or not opens:
            QMessageBox.warning(self.widget, "无法秒开", msg)
            if self._inv_dialog is not None:
                self._inv_dialog.refresh()
            return
        total_letters = sum(len(r.letters) for _, r in opens)
        total_gold = sum(r.gold for _, r in opens)
        total_diam = sum(r.diamond for _, r in opens)
        logger.info(
            "宝箱秒开 (rarity=%d, count=%d, letters=%d, gold=%+.2f, diamond=%+.2f): %s",
            rarity, len(opens), total_letters, total_gold, total_diam, msg,
        )
        self._refresh_after_chest()
        from .ui_chest_summary import show_chest_summary
        show_chest_summary(
            self.widget,
            opens,
            title="秒开结果",
        )
        self.widget.refresh_stats(roll_changed=False)
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()

    def _on_request_open_all_ready(self) -> None:
        """批量打开全部已就绪宝箱，跳过逐箱动画。"""
        opens = chest_opening.open_all_ready(self.state)
        if not opens:
            return
        logger.info("批量开箱 (count=%d)", len(opens))
        self._refresh_after_chest()
        from .ui_chest_summary import show_chest_summary
        show_chest_summary(
            self.widget,
            opens,
            title="批量开箱结果",
        )
        self.widget.refresh_stats(roll_changed=False)
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()

    def play_pet_arena(self) -> None:
        """暂停主窗交互感，启动 pygame 子进程并结算。"""
        ok, msg, _result = launch_pet_arena(self.state)
        self._safe_save()
        self.widget.refresh()
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()
        if ok:
            logger.info("宠物竞技场结算: %s", msg.replace('\n', ' '))
            QMessageBox.information(self.widget, "竞技场结算", msg)
        else:
            logger.warning("宠物竞技场失败: %s", msg)
            QMessageBox.warning(self.widget, "无法开始", msg)

    def play_pixel_tactics(self) -> None:
        ok, msg, _result = launch_pixel_tactics(self.state)
        self._safe_save()
        self.widget.refresh()
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()
        if ok:
            logger.info("像素战场结算: %s", msg.replace('\n', ' '))
            QMessageBox.information(self.widget, "像素战场结算", msg)
        else:
            logger.warning("像素战场失败: %s", msg)
            QMessageBox.warning(self.widget, "无法开始", msg)

    def play_word_arena(self) -> None:
        """启动计算机词汇自走棋：泵事件；目标窗保持置顶可见，输入监控不暂停。"""
        ok, msg = False, ""
        inv_vis = self._inv_dialog is not None and self._inv_dialog.isVisible()
        try:
            if inv_vis:
                self._inv_dialog.hide()
            self.qt_app.processEvents()
            ok, msg, _result = launch_word_arena(
                self.state, pump=self.qt_app.processEvents,
            )
        finally:
            if inv_vis and self._inv_dialog is not None:
                self._inv_dialog.show()
        self._safe_save()
        self.widget.refresh()
        if self._inv_dialog is not None and self._inv_dialog.isVisible():
            self._inv_dialog.refresh()
        if ok:
            logger.info("词汇自走棋结算: %s", msg.replace('\n', ' '))
            QMessageBox.information(self.widget, "词汇自走棋结算", msg)
        else:
            logger.warning("词汇自走棋失败: %s", msg)
            QMessageBox.warning(self.widget, "无法开始", msg)

    # ---------- 退出 ----------
    def quit(self) -> None:
        inv = self.state.inventory
        logger.info(
            "%s 退出 (total_ops=%d, gold=%.1f, diamond=%.1f, tasks=%d)",
            APP_NAME,
            self.state.total_operations, inv.gold, inv.diamond,
            len(self.state.tasks),
        )
        try:
            self.monitor.stop()
        except Exception:
            pass
        try:
            self.sfx.shutdown()
        except Exception:
            pass
        try:
            self.manager.close_runtime_log()
        except Exception:
            logger.warning("退出时关闭运行段失败", exc_info=True)
        self._safe_save()
        self.qt_app.quit()

    def _persist_runtime_quiet(self) -> None:
        try:
            self.manager.persist_runtime_log()
        except Exception:
            logger.warning("运行日志保存失败", exc_info=True)

    def _safe_save(self) -> None:
        """保存状态，失败时弹窗提示用户。"""
        try:
            save_state(self.state)
            self._save_reject_notified = False
        except SaveRejectedError as exc:
            logger.warning("手动保存被拒绝: %s", exc.reason)
            QMessageBox.warning(
                self.widget,
                "保存已拒绝",
                f"{exc.reason}\n\n"
                "内存数据可能已损坏，磁盘存档与备份未被覆盖。\n"
                "请重启应用从 data.json.anchor / data.json.safety 恢复。",
            )
        except Exception as exc:
            logger.error("保存失败: %s", exc)
            QMessageBox.warning(
                self.widget,
                "保存失败",
                f"数据保存失败，请检查磁盘空间和权限。\n\n{exc}",
            )
        self._persist_runtime_quiet()

    def _auto_save(self) -> None:
        """定时自动保存：静默失败；拒绝写入时托盘通知一次。"""
        try:
            save_state(self.state)
            self._save_reject_notified = False
        except SaveRejectedError:
            if not self._save_reject_notified:
                logger.warning("自动保存被拒绝（仅首次通知）")
                self._save_reject_notified = True
                if self.tray.isVisible():
                    self.tray.showMessage(
                        APP_NAME,
                        "检测到内存数据异常，已拒绝自动保存以保护备份。请重启应用。",
                        QSystemTrayIcon.MessageIcon.Warning,
                        5000,
                    )
        except Exception:
            pass
        self._persist_runtime_quiet()

    def run(self) -> int:
        return self.qt_app.exec()


def _acquire_single_instance() -> QLockFile | None:
    """防止多开互相覆盖存档；返回持有锁的对象，进程退出时自动释放。"""
    lock_path = get_data_dir() / "instance.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if lock.tryLock(200):
        return lock
    return None


def main() -> int:
    data_dir = get_data_dir()
    setup_logging(data_dir)
    logger.info("--- %s 启动 (v2) ---", APP_NAME)

    qt_app = QApplication(sys.argv)
    instance_lock = _acquire_single_instance()
    if instance_lock is None:
        logger.info("检测到已有实例运行，退出")
        QMessageBox.information(
            None,
            APP_NAME,
            f"{APP_NAME} 已在运行中。\n请使用系统托盘中的实例，避免多开导致存档互相覆盖。",
        )
        return 0
    app = Application(qt_app)
    # 持有锁引用，防止被 GC 提前释放
    app._instance_lock = instance_lock  # type: ignore[attr-defined]
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
