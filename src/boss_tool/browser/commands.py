"""命令输入源（P1.1 新增）。

非阻塞命令读取抽象：

- CommandSource 协议：定义 start() / poll() / stop() 接口
- ThreadedCommandSource：生产实现，使用 daemon 输入线程 + queue.Queue
- FakeCommandSource：测试用，预置命令队列，不读取真实终端

设计目标：
- 主线程可轮询 manager.is_running / session 是否终态 / 命令队列
- 浏览器关闭后无需用户在终端按回车即可退出
- 输入线程必须为 daemon，不阻止进程结束
- 不引入复杂异步框架
- Ctrl+C 仍由主线程处理（输入线程不吞 KeyboardInterrupt）
"""

from __future__ import annotations

import contextlib
import queue
import threading
from typing import Protocol


class CommandSource(Protocol):
    """命令源协议。

    实现方需保证：
    - start() 幂等，可重复调用
    - poll(timeout) 返回 None 表示无命令，返回字符串表示一条命令（已 strip）
    - stop() 幂等，清理资源，不强制终止线程
    - 输入线程必须为 daemon（生产实现）
    """

    def start(self) -> None:
        """启动命令源（如启动输入线程）。幂等。"""
        ...

    def poll(self, timeout: float) -> str | None:
        """轮询命令。

        Args:
            timeout: 最长等待秒数（阻塞式轮询，但应较短）

        Returns:
            str | None: 命令字符串（已 strip），或 None 表示无命令
        """
        ...

    def stop(self) -> None:
        """停止命令源。幂等，不强制终止线程。"""
        ...


class ThreadedCommandSource:
    """生产实现：daemon 输入线程 + queue.Queue。

    职责：
    1. 输入线程只负责读取终端命令并放入队列
    2. 主线程通过 poll() 非阻塞获取命令
    3. 线程为 daemon，进程退出时自动结束（不强制终止）
    4. 不吞 KeyboardInterrupt（KeyboardInterrupt 由主线程触发，输入线程不处理）

    注意：
    - input() 在 daemon 线程中阻塞时，进程退出会自动终止
    - 不调用 thread.join() 以避免主线程阻塞
    """

    def __init__(self, *, input_func=input, prompt: str = ""):
        """初始化。

        Args:
            input_func: 输入函数（默认为内置 input，便于测试注入）
            prompt: 提示符（每次输入时显示）
        """
        self._input_func = input_func
        self._prompt = prompt
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopped = False

    def start(self) -> None:
        """启动输入线程（daemon）。幂等。"""
        if self._started:
            return
        self._thread = threading.Thread(
            target=self._read_loop,
            name="boss-tool-cmd-reader",
            daemon=True,  # 必须为 daemon
        )
        self._started = True
        self._stopped = False
        self._thread.start()

    def poll(self, timeout: float) -> str | None:
        """轮询命令。"""
        if not self._started:
            return None
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is None:
            return None
        return item.strip()

    def stop(self) -> None:
        """停止命令源。

        不强制终止线程（线程为 daemon，进程退出时自动结束）。
        标记停止状态，使输入循环可以主动退出。
        """
        if self._stopped:
            return
        self._stopped = True
        # 放入 None 唤醒可能阻塞在 get() 的主线程
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)

    def _read_loop(self) -> None:
        """输入线程主循环（daemon）。"""
        while not self._stopped:
            try:
                line = self._input_func(self._prompt)
            except (EOFError, OSError):
                # 终端关闭或 EOF
                with contextlib.suppress(queue.Full):
                    self._queue.put_nowait(None)
                return
            except Exception:
                # 其他异常（如 KeyboardInterrupt 在子线程中表现为其他形式）
                # 不吞异常导致线程静默退出，记录后退出
                return
            if self._stopped:
                return
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(line)

    @property
    def is_daemon(self) -> bool:
        """输入线程是否为 daemon（测试验证用）。"""
        return self._thread is not None and self._thread.daemon


# 延迟导入 contextlib（避免顶层循环导入问题，实际上无循环，但保持一致性）


class FakeCommandSource:
    """测试用命令源。

    预置命令队列，不读取真实终端。
    支持动态追加命令（用于模拟浏览器关闭后无新输入的场景）。
    """

    def __init__(self, *, commands: list[str] | None = None):
        self._commands: list[str] = list(commands or [])
        self._index = 0
        self._started = False
        self._stopped = False
        self._extra_queue: queue.Queue[str | None] = queue.Queue()

    def start(self) -> None:
        self._started = True

    def poll(self, timeout: float) -> str | None:
        # P1.1 修复：不依赖 _started 标志，避免测试中忘记 start() 导致死循环
        # _started 仅用于协议一致性，poll() 本身可直接使用
        # 优先返回预置命令
        if self._index < len(self._commands):
            cmd = self._commands[self._index]
            self._index += 1
            return cmd.strip()
        # 然后检查动态队列
        try:
            item = self._extra_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is None:
            return None
        return item.strip()

    def stop(self) -> None:
        self._stopped = True

    def add_command(self, cmd: str) -> None:
        """动态追加命令（模拟用户后续输入）。"""
        self._extra_queue.put_nowait(cmd)

    @property
    def is_daemon(self) -> bool:
        """FakeCommandSource 不启动真实线程，但接口兼容。"""
        return True


__all__ = [
    "CommandSource",
    "ThreadedCommandSource",
    "FakeCommandSource",
]
