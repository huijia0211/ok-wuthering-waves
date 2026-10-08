r"""批量验证 ScoreEchoTask 的评分路径（不依赖框架线程机制）。

为什么不用 framework 跑 task.run()
---------------------------------
任务 run() 末尾会调 self.sleep()，而框架的 sleep 依赖 task executor 线程在运行
（需要 ok.start_runtime() 真正启动执行器）。测试里没有启动执行器，就会卡住。
这是测试脚手架的限制，不是任务代码的问题——真实使用（GUI 点开始）时执行器是在跑的。

本脚本因此直接装载任务模块，复用它自己的 _list_images / _encode_image / _score_image
/ _output_path 跑完整流程。评分结果由 ScoreEcho 服务端产生，与调用方式无关。

用法：
    .\.venv\Scripts\python.exe tools\verify_scoring.py 目录 [目录...]
"""
import glob
import importlib.util
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)


class _Recorder:
    def __init__(self):
        self.lines = []

    def info(self, msg, *a, **k):
        self.lines.append(f"[INFO ] {msg}")
        print(f"    [INFO ] {msg}", flush=True)

    def error(self, msg, *a, **k):
        self.lines.append(f"[ERROR] {msg}")
        print(f"    [ERROR] {msg}", flush=True)

    warning = info
    debug = lambda self, *a, **k: None  # noqa: E731


def load_task():
    path = os.path.join(REPO_ROOT, "ok_tasks", "ScoreEchoTask.py")
    spec = importlib.util.spec_from_file_location("ScoreEchoTask_verify", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ScoreEchoTask_verify"] = mod
    spec.loader.exec_module(mod)

    # 绕过 BaseWWTask.__init__（它要读全局配置 og，需要完整框架），
    # 但复用任务自己的 configure()，保证配置定义只有一份。
    task = mod.ScoreEchoTask.__new__(mod.ScoreEchoTask)
    task.logger = _Recorder()
    task.name = "🎯 声骸识图评分(ScoreEcho)"
    task.info = {}          # 框架 BaseTask.__init__ 里的定义，log_info 会写入
    task.default_config = {}
    task.config_description = {}
    task.config_type = {}
    task.configure()
    task.load_config()
    return task


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        args = [os.path.join(REPO_ROOT, "logs", "smoke", "samples")]
    else:
        args = [os.path.abspath(a) for a in args]

    out_dir = os.path.join(REPO_ROOT, "screenshots_scored")
    os.makedirs(out_dir, exist_ok=True)

    print("装载 ok_tasks/ScoreEchoTask.py ...")
    task = load_task()
    print(f"任务名: {task.name}")
    print(f"配置项: {sorted(dict(task.config).keys())}\n")

    images = []
    for d in args:
        if os.path.isdir(d):
            images.extend(task._list_images(d))
    if not images:
        print("没有找到图片")
        return 1

    print(f"共 {len(images)} 张待评分\n" + "=" * 66)
    ok_count = fail_count = 0
    t_all = time.time()

    for i, path in enumerate(images, 1):
        name = os.path.basename(path)
        print(f"\n[{i}/{len(images)}] {name}")
        t0 = time.time()
        try:
            result = task._score_image(path)
        except Exception as exc:
            import traceback
            print(f"    [EXC ] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            fail_count += 1
            continue

        if not result:
            fail_count += 1
            print(f"    ❌ 失败，耗时 {time.time() - t0:.1f}s")
            continue

        message, matched, image_b64, raw = result
        cost = time.time() - t0
        if not image_b64:
            fail_count += 1
            print(f"    ❌ 未出分：{message}  耗时 {cost:.1f}s")
            continue

        out_path = task._output_path(out_dir, path)
        with open(out_path, "wb") as fh:
            import base64 as _b64
            fh.write(_b64.b64decode(image_b64))
        ok_count += 1
        print(f"    ✅ 角色={matched or '未识别'}  评分图={os.path.basename(out_path)}  "
              f"({os.path.getsize(out_path)} bytes, 耗时 {cost:.1f}s)")

    print("\n" + "=" * 66)
    print(f"完成：成功 {ok_count}，失败 {fail_count}，总耗时 {time.time() - t_all:.1f}s")
    print(f"输出目录: {out_dir}")
    for p in sorted(glob.glob(os.path.join(out_dir, "*.jpg"))):
        print(f"  {os.path.basename(p)}")
    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
