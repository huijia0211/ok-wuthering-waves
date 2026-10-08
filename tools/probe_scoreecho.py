r"""ScoreEcho 接口直连探测：不经过 ok-ww 框架，带短超时和耗时统计。

用来区分「服务端慢/挂起」和「任务代码问题」。

用法：
    .\.venv\Scripts\python.exe tools\probe_scoreecho.py            # 测默认样例
    .\.venv\Scripts\python.exe tools\probe_scoreecho.py 图1 图2 ...
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
TIMEOUT = 45


def encode(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    b64 = base64.b64encode(raw).decode()
    if len(b64) <= 1_000_000:
        return b64, "原图"
    import cv2
    img = cv2.imread(path)
    for q in (90, 80, 70, 60, 50, 40):
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if ok:
            b64 = base64.b64encode(buf.tobytes()).decode()
            if len(b64) <= 1_000_000:
                return b64, f"JPEG q={q}"
    return b64, "JPEG 最低质"


def score(path):
    b64, how = encode(path)
    payload = {
        "command_str": "",
        "user_data": {"user_name": "probe", "uid": "probe",
                      "union_level": 80, "world_level": 8},
        "images_base64": [b64],
        "use_analysis": False,
        "lang": "chs",
        "templates": ["ribbon"],
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "probe"}, method="POST")

    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(TIMEOUT)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
        cost = time.time() - t0
        data = json.loads(body)
        return {
            "ok": True, "cost": cost, "how": how, "b64_len": len(b64),
            "message": data.get("message"),
            "matched": data.get("matched_character"),
            "has_image": bool(data.get("result_image_base64")),
            "image": data.get("result_image_base64"),
        }
    except socket.timeout:
        return {"ok": False, "cost": time.time() - t0, "how": how,
                "b64_len": len(b64), "error": f"超时（{TIMEOUT}s）"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "cost": time.time() - t0, "how": how,
                "b64_len": len(b64),
                "error": f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:150]}"}
    except Exception as exc:
        return {"ok": False, "cost": time.time() - t0, "how": how,
                "b64_len": len(b64), "error": f"{type(exc).__name__}: {exc}"}
    finally:
        socket.setdefaulttimeout(prev)


def main():
    args = sys.argv[1:]
    if not args:
        args = [os.path.join(REPO_ROOT, "screenshots_to_score", n)
                for n in ("example4.png", "example6.png")]
    for path in args:
        name = os.path.basename(path)
        if not os.path.isfile(path):
            print(f"{name}: 文件不存在 {path}")
            continue
        print(f"\n>>> {name}")
        r = score(path)
        print(f"    编码: {r['how']}  base64={r['b64_len']}  耗时={r['cost']:.2f}s")
        if r["ok"]:
            print(f"    角色={r['matched']}  有评分图={r['has_image']}")
            print(f"    message={r['message']}")
            if r.get("image"):
                out = os.path.join(REPO_ROOT, "logs", "smoke", f"probe_{name.rsplit('.', 1)[0]}.jpg")
                with open(out, "wb") as fh:
                    fh.write(base64.b64decode(r["image"]))
                print(f"    已保存: {out}")
        else:
            print(f"    ❌ {r['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
