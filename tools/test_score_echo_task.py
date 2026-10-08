r"""ScoreEchoTask 端到端测试（不需要游戏运行）。

为什么在 Qt 事件循环里跑
----------------------
ok-script 的很多机制（next_frame / communicate / sleep 调度）依赖 Qt 事件循环。
先前版本构造 OK 实例后直接调 task.run()，**没有 exec() 事件循环**，会死锁
（表现为第 1 张成功后永久挂起）。所以这里用 QTimer 把任务调度进事件循环内执行，
这才是与 GUI 点「开始」等价的真实路径。

配置对象通过框架自己的 load_config() 创建（而不是手搓 dict），确保和真实运行一致。

用法：
    .\.venv\Scripts\python.exe tools\test_score_echo_task.py            # 重建样例
    .\.venv\Scripts\python.exe tools\test_score_echo_task.py --keep     # 用现有图片
"""
import glob
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

IN_DIR = os.path.join(REPO_ROOT, "screenshots_to_score")
OUT_DIR = os.path.join(REPO_ROOT, "screenshots_scored")
SAMPLES = os.path.join(REPO_ROOT, "logs", "smoke", "samples")


def prepare_inputs():
    if "--keep" in sys.argv:
        existing = [n for n in sorted(os.listdir(IN_DIR))
                    if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))]
        print(f"保留现有待评分图片: {existing}")
        return existing

    shutil.rmtree(IN_DIR, ignore_errors=True)
    os.makedirs(IN_DIR, exist_ok=True)
    picked = []
    for name in ("example.png", "example2.png", "example4.png", "example6.png"):
        src = os.path.join(SAMPLES, name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(IN_DIR, name))
            picked.append(name)
    print(f"放入待评分目录: {picked}")
    return picked


def load_task(ok):
    """按框架 TaskManager.init_tasks 的方式实例化任务。"""
    import importlib.util
    from src.task.BaseWWTask import BaseWWTask

    path = os.path.join(REPO_ROOT, "ok_tasks", "ScoreEchoTask.py")
    spec = importlib.util.spec_from_file_location("ScoreEchoTask_mod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ScoreEchoTask_mod"] = mod
    spec.loader.exec_module(mod)

    task = mod.ScoreEchoTask(executor=ok.task_executor, app=ok.app)
    task.after_init(executor=ok.task_executor, scene=ok.task_executor.scene)
    return task


def main():
    prepare_inputs()

    from config import config
    from ok import OK

    print("构造 OK 实例 ...")
    ok = OK(config)

    task = load_task(ok)
    print(f"任务名:     {task.name}")
    print(f"支持语言:   {task.supported_languages}")
    print(f"配置类型:   {type(task.config).__name__}")
    print(f"配置项:     {sorted(dict(task.config).keys()) if task.config else 'None'}")
    print()

    from PySide6.QtCore import QTimer

    state = {"done": False}

    def do_run():
        print("=" * 64)
        print("事件循环内执行 task.run()")
        print("=" * 64)
        try:
            task.run()
        except Exception as exc:
            import traceback
            print("task.run() 异常:")
            traceback.print_exc()
        finally:
            state["done"] = True
            ok.app.quit()

    # 给框架初始化（device/OCR）留时间，然后在事件循环里执行任务
    QTimer.singleShot(3000, do_run)
    ok.app.exec()

    produced = sorted(glob.glob(os.path.join(OUT_DIR, "*.jpg")))
    print(f"\n输出目录 {OUT_DIR}: {len(produced)} 张")
    for p in produced:
        print(f"  {os.path.basename(p)}  ({os.path.getsize(p)} bytes)")
    archived = glob.glob(os.path.join(IN_DIR, "processed", "*"))
    print(f"归档原图: {len(archived)} 张")

    code = 0 if (produced and state["done"]) else 1
    sys.stdout.flush()
    # 框架的 start_runtime() 会拉起非守护线程，app.quit() 之后进程未必退出；
    # 显式硬退出，避免测试脚本挂住。
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
