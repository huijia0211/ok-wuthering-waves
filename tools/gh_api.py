r"""GitHub API 探测与操作辅助脚本。

凭据来源：调用 `git credential fill` 从系统凭据库取 GitHub token（GCM 存储的
`git:https://github.com`），token 只在进程内使用，不落盘、不回显完整值。

用法：
    .\.venv\Scripts\python.exe tools\gh_api.py probe
    .\.venv\Scripts\python.exe tools\gh_api.py fork
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
USER_AGENT = "dsh-okww-tool"


def get_token():
    """从 git 凭据库取 GitHub token（兼容 GCM）。"""
    inp = "protocol=https\nhost=github.com\n\n"
    p = subprocess.run(
        ["git", "credential", "fill"],
        input=inp, capture_output=True, text=True, timeout=60,
    )
    if p.returncode != 0:
        # git credential fill 在无凭据时会保持空输出并返回 0，这里宽松处理
        pass
    creds = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            creds[k] = v
    return creds.get("username"), creds.get("password")


class Gh:
    def __init__(self, token):
        self.token = token
        self.scopes = None

    def __call__(self, path, method="GET", body=None):
        headers = {
            "Authorization": "Bearer " + self.token,
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(API + path, method=method, headers=headers, data=data)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                self.scopes = r.headers.get("x-oauth-scopes", self.scopes)
                raw = r.read().decode() or "{}"
                try:
                    return r.status, json.loads(raw)
                except json.JSONDecodeError:
                    return r.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:500]
        except Exception as e:
            return -1, f"{type(e).__name__}: {e}"


def probe():
    user, token = get_token()
    print(f"凭据用户名: {user}")
    print(f"token 存在: {bool(token)}  长度: {len(token) if token else 0}")
    if not token:
        print("!! 凭据库里没有可用 token")
        return 1
    print(f"token 前缀: {token[:4]}...")

    gh = Gh(token)
    print("\n--- 1. 认证有效性 ---")
    st, r = gh("/user")
    print("GET /user ->", st, r.get("login") if isinstance(r, dict) else r)
    print("OAuth scopes:", gh.scopes)

    print("\n--- 2. 配额 ---")
    st, r = gh("/rate_limit")
    if isinstance(r, dict) and "rate" in r:
        print(f"GET /rate_limit -> {st}  剩余 {r['rate']['remaining']}/{r['rate']['limit']}")
    else:
        print("GET /rate_limit ->", st, r)

    print("\n--- 3. 目标 fork 仓库是否已存在 ---")
    st, r = gh("/repos/huijia0211/ok-wuthering-waves")
    if isinstance(r, dict):
        print(f"GET /repos/huijia0211/ok-wuthering-waves -> {st}  {r.get('full_name')}  fork={r.get('fork')}")
    else:
        print(f"GET /repos/huijia0211/ok-wuthering-waves -> {st}  {str(r)[:120]}")

    print("\n--- 4. 上游仓库 ---")
    st, r = gh("/repos/ok-oldking/ok-wuthering-waves")
    if isinstance(r, dict):
        print(f"上游 {r.get('full_name')} 默认分支={r.get('default_branch')}")
    else:
        print(st, r)
    return 0


def fork():
    user, token = get_token()
    if not token:
        print("!! 无 token")
        return 1
    gh = Gh(token)

    st, r = gh("/repos/huijia0211/ok-wuthering-waves")
    if st == 200 and isinstance(r, dict):
        print(f"fork 已存在: {r['full_name']}  clone_url={r.get('clone_url')}")
        return 0
    if st not in (404,):
        print(f"检查时返回意外状态 {st}: {str(r)[:200]}")
        return 1

    print("fork 不存在，正在通过 API 创建...")
    st, r = gh("/repos/ok-oldking/ok-wuthering-waves/forks", method="POST", body={})
    print("POST /repos/ok-oldking/ok-wuthering-waves/forks ->", st)
    if isinstance(r, dict):
        print("  full_name:", r.get("full_name"))
        print("  clone_url:", r.get("clone_url"))
        print("  fork:", r.get("fork"), " parent:", (r.get("parent") or {}).get("full_name"))
    else:
        print(" ", str(r)[:400])
    return 0 if st in (200, 201, 202) else 1


def verify():
    """核对 fork 仓库状态：默认分支、分支列表、关键文件是否都在。"""
    user, token = get_token()
    if not token:
        print("!! 无 token")
        return 1
    gh = Gh(token)

    st, r = gh("/repos/huijia0211/ok-wuthering-waves")
    if not isinstance(r, dict):
        print("读取仓库失败:", st, r)
        return 1
    print(f"仓库: {r['full_name']}")
    print(f"  默认分支:   {r.get('default_branch')}")
    print(f"  fork:       {r.get('fork')}   parent: {(r.get('parent') or {}).get('full_name')}")
    print(f"  私有:       {r.get('private')}")
    print(f"  大小:       {r.get('size')} KB")
    print(f"  html_url:   {r.get('html_url')}")

    st, branches = gh("/repos/huijia0211/ok-wuthering-waves/branches")
    if isinstance(branches, list):
        print("  分支:", ", ".join(b["name"] for b in branches))
    else:
        print("  分支读取失败:", st, branches)

    print("\n关键文件检查（main 分支）:")
    for path in ("main.py", "config.py", "requirements.txt",
                 "src/gui/CharacterCodeTab.py", "tools/gui_smoke_test.py",
                 "tools/gh_api.py", "assets/coco_annotations.json"):
        st, f = gh(f"/repos/huijia0211/ok-wuthering-waves/contents/{path}?ref=main")
        ok = st == 200 and isinstance(f, dict)
        size = f.get("size") if ok else "-"
        print(f"  {'OK ' if ok else 'MISS'} {path}  ({size} bytes)")
    return 0


def set_default(branch="main", delete_stale=("master",)):
    """把默认分支切到 branch，并删除残留的旧分支。"""
    user, token = get_token()
    gh = Gh(token)
    st, r = gh("/repos/huijia0211/ok-wuthering-waves",
               method="PATCH", body={"default_branch": branch})
    print(f"PATCH default_branch={branch} -> {st}")
    if isinstance(r, dict):
        print("  当前默认分支:", r.get("default_branch"))
    else:
        print(" ", str(r)[:300])

    for b in delete_stale:
        if b == branch:
            continue
        st, r = gh(f"/repos/huijia0211/ok-wuthering-waves/git/refs/heads/{b}", method="DELETE")
        print(f"DELETE branch {b} -> {st} {'(已删除)' if st == 204 else str(r)[:160]}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    table = {"probe": probe, "fork": fork, "verify": verify, "set-default": set_default}
    if cmd not in table:
        print(f"未知命令 {cmd!r}，可选: {', '.join(table)}")
        sys.exit(2)
    sys.exit(table[cmd]())
