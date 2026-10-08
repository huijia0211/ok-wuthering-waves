r"""连续调用 ScoreEcho 的对比实验：定位「第 1 张成功、第 2 张挂起」的根因。

三种模式对比同一张图连续调 2 次：
  A. 不开 keep-alive，显式 close 响应
  B. 复用同一个 opener（默认行为）
  C. 每次新建 opener + 显式 close + 关连接

用法：
    .\.venv\Scripts\python.exe tools\probe_sequential.py 图片路径
"""
import base64
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = "https://scoreecho.loping151.site/web/score"
TIMEOUT = 40


def payload_for(path):
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return {
        "command_str": "",
        "user_data": {"user_name": "probe", "uid": "probe",
                      "union_level": 80, "world_level": 8},
        "images_base64": [b64],
        "use_analysis": False,
        "lang": "chs",
        "templates": ["ribbon"],
    }


def attempt(body, mode, opener=None):
    """发一次请求，分阶段计时。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "probe-seq",
                 "Connection": "close" if mode in ("A", "C") else "keep-alive"},
        method="POST")

    t0 = time.time()
    marks = {}
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(TIMEOUT)
    resp = None
    try:
        marks["connect"] = time.time() - t0
        op = opener or urllib.request
        resp = op.urlopen(req, timeout=TIMEOUT)
        marks["headers"] = time.time() - t0
        raw = resp.read()
        marks["body"] = time.time() - t0
        data_out = json.loads(raw.decode("utf-8", "replace"))
        return {"ok": True, "marks": marks, "total": time.time() - t0,
                "matched": data_out.get("matched_character"),
                "has_image": bool(data_out.get("result_image_base64"))}
    except socket.timeout:
        return {"ok": False, "marks": marks, "total": time.time() - t0,
                "error": f"超时 @{marks}"}
    except Exception as exc:
        return {"ok": False, "marks": marks, "total": time.time() - t0,
                "error": f"{type(exc).__name__}: {exc} @{marks}"}
    finally:
        socket.setdefaulttimeout(prev)
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def run_mode(name, body, opener=None):
    print(f"\n--- 模式 {name} ---")
    for i in (1, 2):
        r = attempt(body, name, opener=opener)
        if r["ok"]:
            print(f"  第{i}次: OK  {r['total']:.2f}s  角色={r['matched']}  "
                  f"有图={r['has_image']}  阶段={ {k: round(v,2) for k,v in r['marks'].items()} }")
        else:
            print(f"  第{i}次: ❌ {r['error']}  已耗时 {r['total']:.2f}s")
            return False
    return True


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        REPO_ROOT, "screenshots_to_score", "example6.png")
    if not os.path.isfile(path):
        print("图片不存在:", path)
        return 1
    print(f"测试图片: {os.path.basename(path)}")
    body = payload_for(path)

    run_mode("A(Connection: close)", body)
    run_mode("B(默认 keep-alive)", body)
    # 模式 C：每次新建 opener
    print("\n--- 模式 C(每次新 opener) ---")
    for i in (1, 2):
        op = urllib.request.build_opener()
        r = attempt(body, "C", opener=op)
        if r["ok"]:
            print(f"  第{i}次: OK  {r['total']:.2f}s  角色={r['matched']}")
        else:
            print(f"  第{i}次: ❌ {r['error']}  已耗时 {r['total']:.2f}s")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
