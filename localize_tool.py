#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 接口本地化工具 v6.3
============================================================
架构原则（最重要）：
    阶段一【获取 JSON】= 完全交给同目录的 tvbox_get_api.py
        （UA 池 / 多源容灾 / 超时 / AES-128-CBC / 2423 / Base64 / Gzip /
         extract_json / absolutize_urls 全部复用，本脚本【不重写】）
    阶段二【本地化】   = 本脚本负责
        （下载文件到 lib/、替换路径、强制覆盖、输出产物）

部署：把本文件与 tvbox_get_api.py 放在【同一目录】，运行本文件即可。
输出：tvbox/{接口名}/api.json
      tvbox/{接口名}/lib/*.jar / *.py / *.png ...
      manifest.json （脚本同目录，合集报告）
============================================================
"""

import os
import sys
import re
import json
import shutil
import time
import hashlib
import importlib.util

# ==================== 用户设置区 ====================
# ★ 强制覆盖：True=每次都重新下载并覆盖旧文件；False=本地已存在则跳过
FORCE_REDOWNLOAD = True

# ★ 输出根目录（相对本脚本所在目录）
OUTPUT_ROOT = "tvbox"

# ★ 同目录抓取脚本的文件名（不要改，除非你改名了）
FETCH_SCRIPT = "tvbox_get_api.py"
# ==================== 用户设置区结束 ====================


# ----------------------------------------------------------------------
# 阶段一：动态加载同目录的 tvbox_get_api.py，复用其全部抓取/解密能力
# ----------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
# 测试钩子（优先级最高）：环境变量 > 模块属性 HERE_OVERRIDE > 默认 HERE
#  - 测试时：os.environ["LOCALIZE_TEST_WORK"] = "/tmp/..." （最干净，无需改源码）
#  - 或：import localize; localize.HERE_OVERRIDE = "/tmp/..." （需在 import 前设置）
HERE_OVERRIDE = None


def _here():
    env = os.environ.get("LOCALIZE_TEST_WORK")
    if env:
        return env
    if HERE_OVERRIDE is not None:
        return HERE_OVERRIDE
    return HERE


def _load_fetch_module():
    """
    加载抓取脚本，返回其 module 对象。
    查找顺序：
      1. HERE_OVERRIDE 目录下的 FETCH_SCRIPT（测试用钩子，优先）
      2. 本脚本同目录下的 FETCH_SCRIPT（生产默认）
    找不到 → 抛 RuntimeError，由调用处决定是否退出（测试时可接管）。
    """
    tried = []
    for base in (HERE_OVERRIDE, HERE):
        if base is None:
            continue
        path = os.path.join(base, FETCH_SCRIPT)
        tried.append(path)
        if os.path.exists(path):
            break
    else:
        raise RuntimeError(
            f"[FATAL] 找不到抓取脚本: {tried[-1]}\n"
            "        请把 tvbox_get_api.py 与本脚本放在【同一目录】"
        )

    # 用 importlib 从文件路径加载（避免文件名带连字符导致 import 失败）
    spec = importlib.util.spec_from_file_location("tvbox_get_api", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 校验关键能力是否存在
    need = ["API_LIST", "fetch_json"]
    missing = [n for n in need if not hasattr(mod, n)]
    if missing:
        raise RuntimeError(
            f"[FATAL] {FETCH_SCRIPT} 缺少必需成员: {missing}\n"
            "        请确认该脚本是完整的 tvbox_get_api.py"
        )
    return mod


# 顶层加载；测试代码可在 import localize 前先设置 HERE_OVERRIDE，
# 或捕获 RuntimeError 后注入自定义的 api 模块。
try:
    api = _load_fetch_module()
except RuntimeError as e:
    # 生产环境直接退出；测试环境可在此后手动设置 localize.api
    print(str(e))
    sys.exit(1)

# 复用：已按接口名分组 + 合并镜像的 API_LIST、抓取函数、AES 工具等
API_LIST_GROUPED = api.API_LIST   # [(name, [url1, url2, ...]), ...]
fetch_json = api.fetch_json       # fetch_json(urls) -> (dict|None, used_url|None)


# ----------------------------------------------------------------------
# 阶段二工具：文件型 URL 判定 / 路径替换
# ----------------------------------------------------------------------
# 需要本地化的文件后缀（出现这些后缀 = 视为文件链接，下载）
_FILE_EXTS = (
    ".jar", ".js", ".py", ".json", ".txt",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".zip", ".apk", ".node",
)

# 强制保留在线的后缀（即使看起来像文件也不下载）
_SKIP_EXTS = (".m3u", ".xml")

# 整体保留在线、不做本地化的字段
_KEEP_ONLINE_FIELDS = {"wallpaper", "parses", "rules", "lives"}


def _url_path(url):
    """取 URL 的路径尾段（含查询前），用于判断后缀"""
    if not isinstance(url, str):
        return ""
    no_q = url.split("?")[0].split("#")[0]
    return no_q.rsplit("/", 1)[-1]


def is_file_url(url):
    """
    判断是否为【需要本地化】的文件型 URL：
      - http(s) 开头
      - 非 csp_ 协议
      - 后缀在 _FILE_EXTS 内
      - 后缀不在 _SKIP_EXTS（.m3u/.xml 强制保留在线）
    """
    if not isinstance(url, str):
        return False
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        return False
    if low.split("://", 1)[1].split("/", 1)[0].startswith("csp_"):
        return False
    tail = _url_path(url).lower()
    if any(tail.endswith(e) for e in _SKIP_EXTS):
        return False
    return any(tail.endswith(e) for e in _FILE_EXTS)


def url_to_name(url):
    """从 URL 推导出本地文件名（去掉 ;md5; 尾巴、查询串）"""
    name = _url_path(url)
    if ";md5;" in name:
        name = name.split(";md5;")[0]
    if not name or "." not in name:
        # 无扩展名的哈希兜底（如 Cloud-drive 类 key）
        name = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return name


# ----------------------------------------------------------------------
# 下载器（强制覆盖 + 原子重命名 + 去重缓存）
# ----------------------------------------------------------------------
class Downloader:
    def __init__(self, api_dir, force):
        self.api_dir = api_dir
        self.lib_dir = os.path.join(api_dir, "lib")
        os.makedirs(self.lib_dir, exist_ok=True)
        self.force = force
        # 缓存键 = (url, 目标文件名)，避免同 URL 不同名互相吞掉
        self.cache = {}
        self.stats = {"downloaded": 0, "kept": 0, "failed": 0, "skipped": 0}

    def _clean_url(self, url):
        """去掉可能的代理前缀，取最后一个 http(s):// 起点"""
        if not isinstance(url, str):
            return url
        u = url.strip()
        matches = list(re.finditer(r"https?://", u))
        if len(matches) >= 2:
            u = u[matches[-1].start():]
        return u

    def get(self, url, hint_name=None):
        """
        下载 url 到 lib/，返回本地相对路径（相对 api_dir，如 ./lib/xxx.jar）。
        不需要/不能下载 → 返回 None（调用方据此保留原 URL）。
        """
        url = self._clean_url(url)
        if not is_file_url(url):
            return None

        # 确定目标文件名
        name = hint_name or url_to_name(url)
        cache_key = (url, name)
        if cache_key in self.cache:
            return self.cache[cache_key]

        local = os.path.join(self.lib_dir, name)

        # 强制覆盖：先删旧文件
        if self.force and os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass

        # 非强制且已存在 → 复用
        if (not self.force) and os.path.exists(local):
            self.stats["skipped"] += 1
            rel = os.path.relpath(local, self.api_dir).replace("\\", "/")
            self.cache[cache_key] = f"./{rel}"
            return self.cache[cache_key]

        # 委托给抓取脚本的下载能力（它已处理好 UA/超时/SSL）
        # 优先用它的 download_file；没有则用 requests/urllib 直接拉
        data = self._raw_download(url)
        if data is None or len(data) == 0:
            self.stats["failed"] += 1
            self.cache[cache_key] = None
            return None

        # 原子写入：先 .tmp 再重命名
        tmp = local + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, local)
        except OSError:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.stats["failed"] += 1
            self.cache[cache_key] = None
            return None

        self.stats["downloaded"] += 1
        rel = os.path.relpath(local, self.api_dir).replace("\\", "/")
        result = f"./{rel}"
        self.cache[cache_key] = result
        return result

    def _raw_download(self, url):
        """下载原始字节。优先复用抓取脚本的下载函数（保持与阶段一一致的 UA/超时/SSL 行为）。"""
        # 1) 优先调用抓取脚本提供的下载函数（tvbox_get_api.download_file 等）
        for attr in ("download_file", "download", "_get", "raw_get", "get_bytes"):
            fn = getattr(api, attr, None)
            if callable(fn):
                try:
                    ret = fn(url)
                except Exception:
                    ret = None
                if isinstance(ret, bytes) and len(ret) > 0:
                    return ret
                if isinstance(ret, str) and len(ret) > 0:
                    return ret.encode("utf-8", errors="ignore")
                # 返回空/None → 继续尝试下一个候选函数

        # 2) 兜底：自行下载（带 UA 池首个指纹）
        headers = {}
        ua_pool = getattr(api, "TVBOX_UAS", None) or getattr(api, "UA_POOL", None)
        if ua_pool:
            first = ua_pool[0]
            if isinstance(first, tuple):
                headers["User-Agent"] = first[0]
                if len(first) > 1 and first[1]:
                    headers["X-Requested-With"] = first[1]
            elif isinstance(first, dict):
                headers.update(first)

        if "requests" in sys.modules:
            try:
                import requests
                r = requests.get(url, headers=headers, timeout=20, verify=False)
                if r.status_code == 200 and len(r.content) > 0:
                    return r.content
            except Exception:
                pass

        try:
            from urllib.request import Request, urlopen
            req = Request(url, headers=headers)
            with urlopen(req, timeout=20) as resp:
                return resp.read()
        except Exception:
            return None


# ----------------------------------------------------------------------
# ext 字段递归本地化
# ----------------------------------------------------------------------
def localize_ext(value, dl):
    """
    递归处理 ext 字段：
      - 字符串：文件 URL → 下载；否则保留
      - dict：遍历每个 value（支持嵌套对象）
      - list：逐个元素（如 apiUrls 这类 → 通常保留原样）
    返回处理后的新值。
    """
    if isinstance(value, str):
        if is_file_url(value):
            local = dl.get(value)
            return local if local else value
        return value  # 接口地址 / host / 版本号 / Base64 → 保留

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            # Cloud-drive 等特殊 key：用原文件名
            if isinstance(v, str) and is_file_url(v):
                local = dl.get(v, hint_name=url_to_name(v))
                out[k] = local if local else v
            else:
                out[k] = localize_ext(v, dl)
        return out

    if isinstance(value, list):
        # 数组（apiUrls 等）：整体保留在线
        return value

    return value


# ----------------------------------------------------------------------
# 阶段二：对一个接口的 JSON 做本地化
# ----------------------------------------------------------------------
def stage2(name, data, out_root):
    api_dir = os.path.join(out_root, name)
    # ★ 关键：先确保目录存在（修复饭太硬 No such file or directory）
    os.makedirs(api_dir, exist_ok=True)
    os.makedirs(os.path.join(api_dir, "lib"), exist_ok=True)

    dl = Downloader(api_dir, force=FORCE_REDOWNLOAD)

    # --- spider → 强制 ./lib/spider.jar（无视原后缀，保留 ;md5; 语义）---
    spider_url = data.get("spider")
    if isinstance(spider_url, str) and spider_url.startswith(("http://", "https://")):
        local = dl.get(spider_url, hint_name="spider.jar")
        data["spider"] = "./lib/spider.jar" if local else spider_url
    else:
        dl.stats["kept"] += 1

    # --- wallpaper / logo ---
    if "wallpaper" in data:
        # wallpaper 强制保留在线（即使恰好是文件 URL）
        dl.stats["kept"] += 1
    logo_url = data.get("logo")
    if isinstance(logo_url, str) and is_file_url(logo_url):
        local = dl.get(logo_url, hint_name=url_to_name(logo_url))
        if local:
            data["logo"] = local
        else:
            dl.stats["kept"] += 1
    else:
        dl.stats["kept"] += 1

    # --- lives[]：整体保留在线（含 .m3u / .php）---
    if "lives" in data:
        dl.stats["kept"] += 1

    # --- sites[] ---
    for site in data.get("sites", []):
        if not isinstance(site, dict):
            continue

        # api
        api_val = site.get("api")
        if isinstance(api_val, str):
            if is_file_url(api_val):
                local = dl.get(api_val, hint_name=url_to_name(api_val))
                if local:
                    site["api"] = local
            else:
                dl.stats["kept"] += 1

        # jar（保留 ;md5; 尾巴 → 去掉尾巴后作为文件名，路径用 ./lib/xxx）
        jar_val = site.get("jar")
        if isinstance(jar_val, str):
            if is_file_url(jar_val):
                base = jar_val.split(";md5;")[0]
                local = dl.get(base, hint_name=url_to_name(base))
                if local:
                    site["jar"] = local
            else:
                dl.stats["kept"] += 1

        # ext（递归）
        if "ext" in site:
            site["ext"] = localize_ext(site["ext"], dl)

    # --- parses[] / rules[]：整体保留在线 ---
    if "parses" in data:
        dl.stats["kept"] += 1
    if "rules" in data:
        dl.stats["kept"] += 1

    # ★ 写入 api.json（目录已 ensure，不再报 No such file）
    with open(os.path.join(api_dir, "api.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return dl.stats


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  TVBox 接口本地化工具 v6.3")
    print("  阶段一【获取】= 复用 tvbox_get_api.py")
    print("  阶段二【本地化】= 本脚本")
    print("=" * 60)
    print(f"  抓取脚本 : {FETCH_SCRIPT}")
    print(f"  输出根目录: {OUTPUT_ROOT}")
    print(f"  强制覆盖 : {'是' if FORCE_REDOWNLOAD else '否'}")
    print(f"  接口数量 : {len(API_LIST_GROUPED)}")
    print()

    out_root = os.path.join(HERE, OUTPUT_ROOT)
    os.makedirs(out_root, exist_ok=True)

    results = []

    for item in API_LIST_GROUPED:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name, urls = item[0], item[1]
        if isinstance(urls, str):
            urls = [urls]

        print("=" * 60)
        print(f"  接口: {name}")
        print(f"  源数量: {len(urls)}")
        print("=" * 60)

        # ---------- 阶段一：完全交给抓取脚本 ----------
        try:
            data, used_url = fetch_json(urls)
        except Exception as e:
            data, used_url = None, None
            print(f"  ✗ 抓取异常: {e}")

        if not data or not isinstance(data, (dict, list)):
            print(f"  ✗ {name}: 所有地址均无法获取 JSON")
            api_dir = os.path.join(out_root, name)
            if os.path.exists(api_dir):
                shutil.rmtree(api_dir)
            results.append({
                "name": name, "success": False,
                "error": "所有地址均无法获取 JSON",
                "used_url": None, "downloaded": 0, "kept": 0,
            })
            continue

        print(f"  ✓ 已获取 JSON（源: {used_url}）")

        # ---------- 阶段二：本地化 ----------
        try:
            stats = stage2(name, data, out_root)
        except Exception as e:
            print(f"  ✗ 本地化异常: {e}")
            api_dir = os.path.join(out_root, name)
            if os.path.exists(api_dir):
                shutil.rmtree(api_dir)
            results.append({
                "name": name, "success": False,
                "error": f"本地化异常: {e}",
                "used_url": used_url, "downloaded": 0, "kept": 0,
            })
            continue

        print(f"  ✓ {name} 完成: 下载 {stats['downloaded']} / 保留 {stats['kept']}"
              + (f" / 失败 {stats['failed']}" if stats["failed"] else ""))
        results.append({
            "name": name, "success": True, "used_url": used_url,
            "downloaded": stats["downloaded"], "kept": stats["kept"],
            "failed": stats["failed"],
        })

    # ---------- 合集报告（脚本同目录唯一一份） ----------
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "fetch_script": FETCH_SCRIPT,
            "force_redownload": FORCE_REDOWNLOAD,
            "output_root": OUTPUT_ROOT,
        },
        "summary": {
            "total": len(results),
            "success": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
        },
        "results": results,
    }
    manifest_path = os.path.join(HERE, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # ---------- 汇总 ----------
    print()
    print("=" * 60)
    print("  汇总")
    print("=" * 60)
    for r in results:
        if r["success"]:
            print(f"  ✓ {r['name']:10s} | 下载 {r['downloaded']:3d} / 保留 {r['kept']:3d}")
        else:
            print(f"  ✗ {r['name']:10s} | {r.get('error', '未知')}")
    s = manifest["summary"]
    print()
    print(f"  成功: {s['success']}  失败: {s['failed']}  总计: {s['total']}")
    print(f"  合集报告: {manifest_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
