r"""鸣潮声骸实机截图评分任务（ScoreEcho 接入）。

与 ScoreEchoTask 的分工
----------------------
- ScoreEchoTask:        读磁盘上的截图文件，适合批量、事后评分。
- ScoreEchoCaptureTask: 直接从运行中的游戏窗口抓帧评分，不用你手动截图。
                         本文件。

使用姿势
--------
1. 用 ok-ww 启动（`启动-okww.bat`），**手动进游戏**并把声骸/面板界面摆在屏幕上。
2. 在「任务」页点亮本任务的 Enabled。
3. 按 F9（Basic Options 里的 Start/Stop）开始。后台模式下游戏窗口被遮挡也能抓。
4. 本任务每隔「抓帧间隔」秒抓一帧送去评分，成功就保存评分图并自动停止；
   也可以自己按 F9 停。

设计取舍
--------
- 抓帧用 `next_frame()` 而不是框架的 `screenshot()`：后者只写到 screenshots/ 并且会
  套用 blur_area 处理（给月卡弹窗打码），那会降低 OCR 识别率；我们这里要原始帧。
- 不自动点游戏菜单：每个版本的 UI 坐标都会变，盲写不可维护。界面由用户摆好。
"""

import base64
import cv2
import json
import os
import socket
import time
import urllib.error
import urllib.request

from ok import TriggerTask

DEFAULT_ENDPOINT = "https://scoreecho.loping151.site/web/score"
MAX_BASE64_BYTES = 1_000_000

TEMPLATE_CHOICES = ["ribbon", "porcelain", "midnight", "scoreband", "legacy_dark"]
COST_CHOICES = ["自动", "4c", "3c", "1c"]


class ScoreEchoCaptureTask(TriggerTask):
    """从游戏窗口抓帧 -> 调 ScoreEcho 评分 -> 保存结果。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "📸 实机抓帧评分(ScoreEcho)"
        self.description = (
            "游戏里摆好声骸/面板界面后按 F9 开始，后台抓帧送评分，"
            "出分后保存并自动停止。仅支持简中界面。"
        )
        self.supported_languages = ["zh_CN"]
        self.trigger_interval = 3

        self.default_config.update({
            "_enabled": False,
            "抓帧间隔": 5,
            "指定角色": "",
            "声骸部位": "自动",
            "评分模板": "ribbon",
            "分析模式": False,
            "结果保存目录": "screenshots_scored",
            "保留原始截图": False,
            "接口地址": DEFAULT_ENDPOINT,
            "超时秒数": 60,
            "成功后自动停止": True,
        })

        self.config_description.update({
            "抓帧间隔": "每多少秒抓一帧送评分。太短会打到服务端限额。",
            "指定角色": "留空自动识别；填角色名可强制指定（如 珂莱塔）。",
            "声骸部位": "自动=服务端判断；也可强制 4c/3c/1c。",
            "评分模板": "评分图的视觉模板。",
            "分析模式": "整面板截图时开启（use_analysis）。",
            "结果保存目录": "评分图输出目录。",
            "保留原始截图": "同时把抓到的原始帧存成 PNG，便于排查识别失败。",
            "接口地址": "ScoreEcho 评分接口，网页版有限额，量大建议自建。",
            "超时秒数": "单次请求超时（含读响应）。实测服务端会间歇性挂起，别设太大。",
            "成功后自动停止": "评分成功后置回 Enabled 关闭，避免一直抓帧刷接口。",
        })

        self.config_type.update({
            "声骸部位": {"type": "drop_down", "options": COST_CHOICES},
            "评分模板": {"type": "drop_down", "options": TEMPLATE_CHOICES},
            "分析模式": {"type": "drop_down", "options": [True, False]},
            "保留原始截图": {"type": "drop_down", "options": [True, False]},
            "成功后自动停止": {"type": "drop_down", "options": [True, False]},
        })

    # --- 触发任务主循环 -----------------------------------------------------

    def run(self):
        """TriggerTask 每次触发调用一次，返回真值表示已处理。

        触发间隔由框架的 should_trigger() 依据 self.trigger_interval 控制，
        这里不要再自己算一次，否则间隔会被放大成两倍配置值。
        """
        frame = self.next_frame(time_out=5)
        if frame is None:
            self.log_info("抓不到游戏画面（游戏没开或窗口没找到），跳过本次")
            return False

        now = time.time()
        out_dir = self._abs_dir(self.config.get("结果保存目录", "screenshots_scored"))
        os.makedirs(out_dir, exist_ok=True)

        raw_path = None
        if self.config.get("保留原始截图", False):
            raw_path = os.path.join(out_dir, f"raw_{int(now)}.png")
            cv2.imwrite(raw_path, frame)

        b64 = self._encode_frame(frame)
        if not b64:
            self.log_error("画面编码失败，跳过本次")
            return False

        result = self._score(b64)
        if not result:
            return False

        message, matched, image_b64 = result
        if not image_b64:
            self.log_info(f"未出分（{message}），继续等下一帧")
            return False

        out_path = os.path.join(out_dir, f"capture_{matched or 'unknown'}_{int(now)}.jpg")
        with open(out_path, "wb") as fh:
            fh.write(base64.b64decode(image_b64))

        detail = f"角色={matched or '未识别'}"
        if raw_path:
            detail += f"  原图={os.path.basename(raw_path)}"
        self.log_info(f"✅ 评分完成 {detail}  评分图={os.path.basename(out_path)}", notify=True)

        if self.config.get("成功后自动停止", True):
            self.disable()
            self.log_info("已自动关闭本任务，避免持续请求评分接口")
        return True

    # --- 评分调用 -----------------------------------------------------------

    def _score(self, image_b64):
        parts = []
        char = (self.config.get("指定角色") or "").strip()
        cost = self.config.get("声骸部位", "自动")
        if char:
            parts.append(f"{char}评分")
        if cost and cost != "自动":
            parts.append(cost)
        command = " ".join(parts)

        payload = {
            "command_str": command,
            "user_data": {"user_name": "okww", "uid": "okww",
                          "union_level": 80, "world_level": 8},
            "images_base64": [image_b64],
            "use_analysis": bool(self.config.get("分析模式", False)),
            "lang": "chs",
            "templates": [self.config.get("评分模板", "ribbon")],
        }

        timeout = int(self.config.get("超时秒数", 60))
        req = urllib.request.Request(
            self.config.get("接口地址", DEFAULT_ENDPOINT),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "ok-ww-ScoreEchoCapture"},
            method="POST",
        )

        # urlopen 的 timeout 只覆盖连接阶段；服务端返回响应头后卡住不发 body 时仍会
        # 无限等待。实测 scoreecho 网页版有这种间歇性挂起，故用 socket 级超时兜底。
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            self.log_error(f"接口 HTTP {exc.code}：{exc.read().decode('utf-8', 'replace')[:200]}")
            return None
        except socket.timeout:
            self.log_info(f"请求超时（{timeout}s），服务端可能繁忙，跳过本帧")
            return None
        except Exception as exc:
            self.log_error(f"请求失败：{type(exc).__name__}: {exc}")
            return None
        finally:
            socket.setdefaulttimeout(previous)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.log_error(f"响应非 JSON：{body[:200]}")
            return None
        return (data.get("message") or "", data.get("matched_character") or "",
                data.get("result_image_base64"))

    # --- 工具 ---------------------------------------------------------------

    def _encode_frame(self, frame):
        """把 BGR 帧编码成不超过 1MB 的 base64（先 PNG，超限转降质 JPEG）。"""
        ok, buf = cv2.imencode(".png", frame)
        if ok:
            b64 = base64.b64encode(buf.tobytes()).decode()
            if len(b64) <= MAX_BASE64_BYTES:
                return b64
        for quality in (90, 80, 70, 60, 50, 40):
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok:
                b64 = base64.b64encode(buf.tobytes()).decode()
                if len(b64) <= MAX_BASE64_BYTES:
                    return b64
        h, w = frame.shape[:2]
        scale = 0.75
        while scale > 0.2:
            resized = cv2.resize(frame, (int(w * scale), int(h * scale)))
            ok, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            if ok:
                b64 = base64.b64encode(buf.tobytes()).decode()
                if len(b64) <= MAX_BASE64_BYTES:
                    return b64
            scale -= 0.15
        return None

    @staticmethod
    def _abs_dir(path):
        path = path or ""
        return path if os.path.isabs(path) else os.path.abspath(path)
