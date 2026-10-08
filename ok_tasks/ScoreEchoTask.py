r"""鸣潮声骸评分任务（接入 ScoreEcho）。

设计要点
--------
1. **只做识别评分，不做菜单导航。** 游戏内进哪个界面、翻几页，每个版本都可能变；
   盲写坐标点不会有可维护性。所以本任务从磁盘读取截图，界面操作交给用户（或以后
   另外写导航逻辑）。
2. **零侵入。** 放在 ok_tasks/ 下，由 ok-script 的 custom_tasks 机制自动发现，
   不改 src/、config.py、i18n/ 任何上游文件，上游更新不会冲突。
3. **中文 UI 优先。** ScoreEcho 的 lang 传 'chs'；识别引擎是服务端的 PP-OCRv5，
   已实测对简体中文界面识别正常（守岸人/珂莱塔/嘉贝莉娜/今汐 均识别正确）。
4. **1MB 硬限制。** /web/score 要求每张图 base64 后不超过 1MB，超了自动降质重编码。

用法
----
    .\.venv\Scripts\python.exe main.py        # 启动 GUI
然后在「任务」页点本任务的「开始」。先把声骸截图放进 screenshots_to_score/ 目录。

接口契约（实测于 scoreecho.loping151.site）
-----------------------------------------
    POST /web/score
    Body: {command_str, user_data{user_name,uid,union_level,world_level},
           images_base64[], use_analysis, lang, templates[]}
    Resp: {result_image_base64, message, score_results, matched_character}
注意 result_image_base64 实际是 JPEG（FF D8 FF E0），不是 PNG。
"""

import base64
import json
import os
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request

import cv2

from src.task.BaseWWTask import BaseWWTask

# --- 常量 -------------------------------------------------------------------

DEFAULT_ENDPOINT = "https://scoreecho.loping151.site/web/score"
# 服务端要求单图 base64 不超过 1MB
MAX_BASE64_BYTES = 1_000_000

SUPPORTED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

TEMPLATE_CHOICES = ["ribbon", "porcelain", "midnight", "scoreband", "legacy_dark"]

# 声骸部位（cost），留空则交给服务端自动判断
COST_CHOICES = ["自动", "4c", "3c", "1c"]


class ScoreEchoTask(BaseWWTask):
    """读取截图 -> 调 ScoreEcho 评分 -> 保存评分图。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🎯 声骸识图评分(ScoreEcho)"
        self.description = (
            "把声骸/面板截图放进 screenshots_to_score 目录后点开始，"
            "自动识别并生成评分图，结果保存在 screenshots_scored。仅支持简中界面。"
        )
        # 依赖中文 OCR 场景的文案习惯，只对简中界面开放
        self.supported_languages = ["zh_CN"]
        self.configure()

    def configure(self):
        """装载本任务的配置模式。

        独立成方法是为了让测试可以在不跑 BaseTask.__init__（依赖框架 og/全局配置）
        的情况下复用同一份配置定义，避免测试里出现第二份真相。
        """
        self.default_config.update({
            "待评分目录": "screenshots_to_score",
            "结果保存目录": "screenshots_scored",
            "识别语言": "chs",
            "指定角色": "",
            "声骸部位": "自动",
            "评分模板": "ribbon",
            "分析模式": False,
            "评分后归档原图": True,
            "接口地址": DEFAULT_ENDPOINT,
            "超时秒数": 180,
        })

        self.config_description.update({
            "待评分目录": "相对仓库根目录的文件夹，把要评分的截图放进去。",
            "结果保存目录": "评分图输出目录，文件名格式 原文件名_scored_时间戳.jpg。",
            "识别语言": "chs=简体中文，cht=繁体中文，en=英文。中文界面请保持 chs。",
            "指定角色": "留空则由服务端自动识别角色；填角色名可强制指定（如 珂莱塔）。",
            "声骸部位": "自动=服务端判断；也可强制 4c/3c/1c。",
            "评分模板": "评分图的视觉模板。",
            "分析模式": "开启后使用面板分析（use_analysis），适合整面板截图。",
            "评分后归档原图": "已评分的原图移动到 待评分目录/processed/，避免重复计分。",
            "接口地址": "ScoreEcho 评分接口。网页版有限额，量大建议自建。",
            "超时秒数": "单次请求超时，网络慢或图片多时调大。",
        })

        self.config_type.update({
            "识别语言": {"type": "drop_down", "options": ["chs", "cht", "en"]},
            "声骸部位": {"type": "drop_down", "options": COST_CHOICES},
            "评分模板": {"type": "drop_down", "options": TEMPLATE_CHOICES},
            "分析模式": {"type": "drop_down", "options": [True, False]},
            "评分后归档原图": {"type": "drop_down", "options": [True, False]},
        })

    # --- 主流程 -------------------------------------------------------------

    def run(self):
        # 支持命令行覆盖目录：--input <dir> --output <dir>
        # 便于批量跑而不必改配置，也便于自动化测试。
        override_in, override_out = self._parse_dir_args()
        input_dir = override_in or self._abs_dir(self.config.get("待评分目录", "screenshots_to_score"))
        output_dir = override_out or self._abs_dir(self.config.get("结果保存目录", "screenshots_scored"))

        if not os.path.isdir(input_dir):
            os.makedirs(input_dir, exist_ok=True)
            self.log_error(f"待评分目录不存在，已创建：{input_dir}\n请把截图放进去后重试。", notify=True)
            return

        images = self._list_images(input_dir)
        if not images:
            self.log_error(f"待评分目录里没有图片：{input_dir}", notify=True)
            return

        os.makedirs(output_dir, exist_ok=True)

        self.info_set("待评分", len(images))
        self.info_set("已评分", 0)
        self.info_set("失败", 0)

        self.log_info(f"发现 {len(images)} 张图片，开始评分", notify=True)

        for index, path in enumerate(images, 1):
            if self._stop_requested():
                self.log_info("收到停止请求，提前结束", notify=True)
                break
            self.log_info(f"[{index}/{len(images)}] 处理 {os.path.basename(path)}")

            try:
                result = self._score_image(path)
            except Exception as exc:
                self.info_incr("失败")
                self.log_error(f"  {os.path.basename(path)} 评分异常：{type(exc).__name__}: {exc}")
                continue

            if not result:
                self.info_incr("失败")
                continue

            message, matched, image_b64, raw = result
            if not image_b64:
                self.info_incr("失败")
                self.log_error(f"  未生成评分图：{message}")
                continue

            out_path = self._output_path(output_dir, path)
            with open(out_path, "wb") as fh:
                fh.write(base64.b64decode(image_b64))

            self.info_incr("已评分")
            self.log_info(f"  ✅ 角色={matched or '未识别'}  评分图={os.path.basename(out_path)}")

            if self.config.get("评分后归档原图", True):
                self._archive(input_dir, path)

            self.sleep(0.2)

        summary = (
            f"评分完成：成功 {self.info_get('已评分')} 张，"
            f"失败 {self.info_get('失败')} 张，结果在 {output_dir}"
        )
        self.log_info(summary, notify=True)

    # --- 接口调用 -----------------------------------------------------------

    def _score_image(self, path):
        """对单张图评分。返回 (message, matched_character, result_image_b64, 原始响应) 或 None。"""
        image_b64 = self._encode_image(path)
        if not image_b64:
            self.log_error(f"  图片读取/编码失败：{path}")
            return None

        command = self._build_command()
        lang = self.config.get("识别语言", "chs")
        payload = {
            "command_str": command,
            "user_data": {
                "user_name": "okww",
                "uid": "okww",
                "union_level": 80,
                "world_level": 8,
            },
            "images_base64": [image_b64],
            "use_analysis": bool(self.config.get("分析模式", False)),
            "lang": lang,
            "templates": [self.config.get("评分模板", "ribbon")],
        }

        endpoint = self.config.get("接口地址", DEFAULT_ENDPOINT)
        timeout = int(self.config.get("超时秒数", 180))
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "ok-ww-ScoreEchoTask"},
            method="POST",
        )

        # 注意：urlopen 的 timeout 只作用于连接阶段，服务端返回响应头之后如果卡住不
        # 发 body，仍然会无限期等待。实测 scoreecho 网页版出现过这种挂起，所以这里
        # 额外用 socket 级超时兜底，并在结束后恢复原值。
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        started = time.time()
        response = None
        try:
            self.log_info(f"    请求中…（超时上限 {timeout}s）")
            response = urllib.request.urlopen(req, timeout=timeout)
            self.log_info(f"    已收到响应头 {time.time() - started:.1f}s")
            body = response.read().decode("utf-8", "replace")
            self.log_info(f"    响应读取完成 {time.time() - started:.1f}s，{len(body)} 字符")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            self.log_error(f"  接口返回 HTTP {exc.code}：{detail}")
            return None
        except socket.timeout:
            self.log_error(f"  请求超时（{timeout}s，已等待 {time.time() - started:.1f}s），跳过该图")
            return None
        except Exception as exc:
            self.log_error(f"  请求失败（{time.time() - started:.1f}s）：{type(exc).__name__}: {exc}")
            return None
        finally:
            socket.setdefaulttimeout(previous_timeout)
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            self.log_error(f"  响应不是合法 JSON：{body[:200]}")
            return None

        message = result.get("message") or ""
        matched = result.get("matched_character") or ""
        image = result.get("result_image_base64")
        return message, matched, image, result

    def _build_command(self):
        """拼装 ScoreEcho 的指令串。留空则让服务端自动识别角色。"""
        parts = []
        char = (self.config.get("指定角色") or "").strip()
        cost = self.config.get("声骸部位", "自动")
        if char:
            parts.append(f"{char}评分")
        if cost and cost != "自动":
            parts.append(cost)
        return " ".join(parts)

    def _encode_image(self, path):
        """读图并转 base64，超过 1MB 时逐级降质重编码。"""
        img = cv2.imread(path)
        if img is None:
            return None

        with open(path, "rb") as fh:
            raw = fh.read()
        b64 = base64.b64encode(raw).decode()
        if len(b64) <= MAX_BASE64_BYTES:
            return b64

        # 超限：转 JPEG 并逐档降质
        for quality in (90, 80, 70, 60, 50, 40):
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue
            b64 = base64.b64encode(buf.tobytes()).decode()
            if len(b64) <= MAX_BASE64_BYTES:
                self.log_info(f"  图片超 1MB，已压缩为 JPEG quality={quality}")
                return b64

        # 仍然超限：缩小分辨率再试
        h, w = img.shape[:2]
        scale = 0.75
        while scale > 0.2:
            resized = cv2.resize(img, (int(w * scale), int(h * scale)))
            ok, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            if ok:
                b64 = base64.b64encode(buf.tobytes()).decode()
                if len(b64) <= MAX_BASE64_BYTES:
                    self.log_info(f"  图片超 1MB，已缩放至 {int(scale * 100)}%")
                    return b64
            scale -= 0.15
        return b64

    # --- 工具方法 -----------------------------------------------------------

    @staticmethod
    def _parse_dir_args():
        """从命令行解析 --input/--output，解析不到返回 (None, None)。"""
        argv = sys.argv
        in_dir = out_dir = None
        for flag, setter in (("--input", "in"), ("--output", "out")):
            if flag in argv:
                idx = argv.index(flag)
                if idx + 1 < len(argv):
                    if setter == "in":
                        in_dir = argv[idx + 1]
                    else:
                        out_dir = argv[idx + 1]
        return in_dir, out_dir

    def _stop_requested(self):
        """框架停止任务时会设置 executor 的退出事件；拿不到就当作未停止。"""
        try:
            executor = getattr(self, "executor", None)
            event = getattr(executor, "exit_event", None) if executor else None
            return bool(event is not None and event.is_set())
        except Exception:
            return False

    @staticmethod
    def _abs_dir(path):
        path = path or ""
        return path if os.path.isabs(path) else os.path.abspath(path)

    @staticmethod
    def _list_images(folder):
        entries = []
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            if os.path.isfile(full) and name.lower().endswith(SUPPORTED_IMAGE_EXT):
                entries.append(full)
        return entries

    @staticmethod
    def _output_path(output_dir, src_path):
        stem = os.path.splitext(os.path.basename(src_path))[0]
        return os.path.join(output_dir, f"{stem}_scored_{int(time.time())}.jpg")

    @staticmethod
    def _archive(input_dir, src_path):
        processed = os.path.join(input_dir, "processed")
        os.makedirs(processed, exist_ok=True)
        target = os.path.join(processed, os.path.basename(src_path))
        if os.path.exists(target):
            stem, ext = os.path.splitext(os.path.basename(src_path))
            target = os.path.join(processed, f"{stem}_{int(time.time())}{ext}")
        try:
            shutil.move(src_path, target)
        except Exception:
            pass
