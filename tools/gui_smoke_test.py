r"""GUI 冒烟自检：启动真实主窗口，截图存证后自动退出。

用途：验证 ok-ww 在本机从源码运行时能否正常创建 Qt GUI。
不会执行任何游戏任务，不会模拟输入。

用法（必须在仓库根目录下运行）：
    .\.venv\Scripts\python.exe tools\gui_smoke_test.py
"""
import os
import sys
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

OUT_DIR = os.path.join(REPO_ROOT, "logs", "smoke")
os.makedirs(OUT_DIR, exist_ok=True)

RESULT = {"ok": False, "steps": [], "error": None, "shot": None}


def step(msg):
    print(f"[STEP] {msg}", flush=True)
    RESULT["steps"].append(msg)


try:
    step("import config")
    from config import config

    step("import ok.OK")
    from ok import OK

    step("construct OK(config)  —— 会加载 Qt GUI 层")
    ok = OK(config)

    step("show_main_window()  —— 创建真实主窗口")
    ok.app.show_main_window()

    win = ok.app.main_window
    if win is None:
        raise RuntimeError("main_window 为 None")
    RESULT["window_class"] = type(win).__name__
    RESULT["window_title"] = win.windowTitle()
    step(f"主窗口已创建: {type(win).__name__} title={win.windowTitle()!r}")

    def grab_and_quit():
        try:
            # 处理一轮事件，让布局完成绘制
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

            shot = win.grab()
            path = os.path.join(OUT_DIR, "main_window.png")
            saved = shot.save(path, "PNG")
            RESULT["shot"] = path
            RESULT["shot_ok"] = bool(saved)
            RESULT["size"] = (win.width(), win.height())
            step(f"窗口截图已保存: {path} (save={saved}, {win.width()}x{win.height()})")
            RESULT["ok"] = True
        except Exception as e:
            RESULT["error"] = f"截图失败: {type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            try:
                ok.app.quit()
            except Exception:
                pass

    from PySide6.QtCore import QTimer

    # 给主窗口一点时间完成初始化（运行时初始化、设备发现等）
    QTimer.singleShot(8000, grab_and_quit)
    step("进入事件循环，8 秒后自动截图并退出")
    ok.app.exec()

except Exception as e:
    RESULT["error"] = f"{type(e).__name__}: {e}"
    traceback.print_exc()

print("\n================ 冒烟自检结果 ================", flush=True)
print(f"  成功: {RESULT['ok']}")
print(f"  窗口: {RESULT.get('window_class')} / {RESULT.get('window_title')!r}")
print(f"  尺寸: {RESULT.get('size')}")
print(f"  截图: {RESULT.get('shot')}")
print(f"  错误: {RESULT['error']}")
print("=============================================", flush=True)

sys.exit(0 if RESULT["ok"] else 1)
