--- /dev/null
+++ b/wogg.py
@@ -0,0 +1,512 @@
+#!/usr/bin/env python3
+"""玩偶哥哥共享站（wogg.net）命令行工具：搜索影片、浏览分类、提取网盘资源链接。
+
+无第三方依赖，直接使用 Python 3 标准库。
+
+示例:
+  python3 wogg.py search 折月亮
+  python3 wogg.py search 喜剧 --limit 5 --links
+  python3 wogg.py list --type 2 --page 2 --limit 5
+  python3 wogg.py home --limit 10
+  python3 wogg.py detail 133871
+  python3 wogg.py detail 133871 --json
+  python3 wogg.py types
+"""
+
+import argparse
+import html as htmllib
+import json
+import os
+import re
+import sys
+import time
+import urllib.error
+import urllib.parse
+import urllib.request
+
+DEFAULT_BASE = os.environ.get("WOGG_BASE", "https://www.wogg.net").rstrip("/")
+
+HEADERS = {
+    "User-Agent": (
+        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
+        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
+    ),
+    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
+    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
+}
+
+PROVIDERS = [
+    ("drive.uc.cn", "UC网盘"),
+    ("fast.uc.cn", "UC网盘"),
+    ("pan.quark.cn", "夸克网盘"),
+    ("pan.baidu.com", "百度网盘"),
+    ("yun.baidu.com", "百度网盘"),
+    ("alipan.com", "阿里云盘"),
+    ("aliyundrive.com", "阿里云盘"),
+    ("pan.xunlei.com", "迅雷云盘"),
+    ("115.com", "115网盘"),
+    ("115cdn.com", "115网盘"),
+    ("123pan.com", "123云盘"),
+    ("123684.com", "123云盘"),
+    ("mypikpak.com", "PikPak"),
+    ("caiyun.139.com", "移动云盘"),
+]
+
+TAG_RE = re.compile(r"<[^>]+>")
+SPACE_RE = re.compile(r"[\s\u3000]+")
+PWD_RE = re.compile(r"[?&]pwd=([0-9A-Za-z]{4})")
+NOTE_PWD_RE = re.compile(r"提取码\s*[:：]?\s*([0-9A-Za-z]{4})")
+
+
+class WoggError(Exception):
+    pass
+
+
+class Client:
+    def __init__(self, base=DEFAULT_BASE, timeout=25.0, retries=3):
+        self.base = base.rstrip("/")
+        self.timeout = timeout
+        self.retries = retries
+        self._cache = {}
+
+    def url(self, path):
+        if path.startswith("http://") or path.startswith("https://"):
+            return path
+        if not path.startswith("/"):
+            path = "/" + path
+        return self.base + path
+
+    def get(self, path, use_cache=True):
+        url = self.url(path)
+        if use_cache and url in self._cache:
+            return self._cache[url]
+        last = None
+        for attempt in range(self.retries):
+            try:
+                req = urllib.request.Request(url, headers=HEADERS)
+                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
+                    raw = resp.read()
+                    charset = resp.headers.get_content_charset() or "utf-8"
+                text = raw.decode(charset, "ignore")
+                if "Just a moment" in text and "challenge-platform" in text:
+                    raise WoggError("站点返回了人机校验页，请稍后重试或换网络")
+                self._cache[url] = text
+                return text
+            except (urllib.error.URLError, TimeoutError, OSError, WoggError) as exc:
+                last = exc
+                if isinstance(exc, WoggError):
+                    raise
+                if attempt + 1 < self.retries:
+                    time.sleep(1.5 * (attempt + 1))
+        raise WoggError("请求失败: {} ({})".format(url, last))
+
+
+def clean(fragment, maxlen=None):
+    if not fragment:
+        return ""
+    text = TAG_RE.sub(" ", fragment)
+    text = htmllib.unescape(text)
+    if "&quot;" in text or "&amp;" in text:
+        text = htmllib.unescape(text)
+    text = SPACE_RE.sub(" ", text).strip()
+    for marker in ("[展开全部]", "[收起部分]"):
+        text = text.replace(marker, "")
+    text = text.strip()
+    if maxlen and len(text) > maxlen:
+        text = text[: maxlen - 1] + "…"
+    return text
+
+
+def first(pattern, text, default=""):
+    match = re.search(pattern, text, re.S)
+    if not match:
+        return default
+    if match.groups():
+        return match.group(1)
+    return match.group(0)
+
+
+def anchors(fragment):
+    return [
+        (href, clean(label))
+        for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', fragment, re.S)
+    ]
+
+
+def detect_provider(url, fallback=""):
+    host = urllib.parse.urlparse(url).netloc.lower()
+    for suffix, name in PROVIDERS:
+        if host == suffix or host.endswith("." + suffix):
+            return name
+    if "uc.cn" in host:
+        return "UC网盘"
+    if "quark" in host:
+        return "夸克网盘"
+    if "baidu" in host:
+        return "百度网盘"
+    return fallback or "网盘"
+
+
+def extract_pwd(url, note=""):
+    match = PWD_RE.search(url) or NOTE_PWD_RE.search(note or "")
+    return match.group(1) if match else ""
+
+
+def parse_cards(page):
+    results = []
+    seen = set()
+    blocks = page.split('<div class="module-item">')[1:]
+    for block in blocks:
+        href = first(r'<a href="(/voddetail/(\d+)\.html)" title="([^"]*)"', block)
+        if not href:
+            continue
+        match = re.search(r'<a href="(/voddetail/(\d+)\.html)" title="([^"]*)"', block, re.S)
+        path, vod_id, title = match.groups()
+        title = clean(title)
+        if vod_id in seen:
+            continue
+        seen.add(vod_id)
+        poster = htmllib.unescape(first(r'data-src="([^"]+)"', block))
+        caption = first(r'<div class="module-item-caption">(.*?)</div>', block)
+        parts = [clean(part) for part in re.findall(r"<span[^>]*>(.*?)</span>", caption, re.S)]
+        parts = [part for part in parts if part]
+        status = clean(first(r'<div class="module-item-text">(.*?)</div>', block))
+        synopsis = clean(first(r'<div class="module-item-style video-text">(.*?)</div>', block))
+        results.append(
+            {
+                "id": vod_id,
+                "title": title,
+                "url": path,
+                "poster": poster,
+                "year": parts[0] if len(parts) > 0 else "",
+                "category": parts[1] if len(parts) > 1 else "",
+                "region": parts[2] if len(parts) > 2 else "",
+                "status": status,
+                "synopsis": synopsis,
+            }
+        )
+    return results
+
+
+def parse_search(page):
+    results = []
+    seen = set()
+    blocks = page.split('<div class="module-search-item">')[1:]
+    for block in blocks:
+        match = re.search(r'<h3>\s*<a href="(/voddetail/(\d+)\.html)" title="([^"]*)"', block, re.S)
+        if not match:
+            match = re.search(r'<a class="video-serial" href="(/voddetail/(\d+)\.html)"[^>]*>(.*?)</a>', block, re.S)
+        if match:
+            path, vod_id, title = match.groups()
+        else:
+            continue
+        title = clean(title)
+        if vod_id in seen:
+            continue
+        seen.add(vod_id)
+        poster = htmllib.unescape(first(r'data-src="([^"]+)"', block))
+        status = clean(first(r'<a class="video-serial"[^>]*>(.*?)</a>', block))
+        aux = first(r'<div class="video-info-aux">(.*?)</div>', block)
+        aux_texts = [label for _, label in anchors(aux) if label]
+        aux_texts = [x for x in aux_texts if not x.startswith("icon-")]
+        year = next((x for x in aux_texts if re.fullmatch(r"\d{4}", x)), "")
+        category = aux_texts[0] if aux_texts and aux_texts[0] != year else ""
+        region = next((x for x in aux_texts if x not in (year, category) and len(x) <= 30), "")
+        director = []
+        actors = []
+        for label, fragment in re.findall(
+            r'<span class="video-info-itemtitle">(.*?)</span>\s*<div class="video-info-item[^"]*">(.*?)</div>',
+            block,
+            re.S,
+        ):
+            names = [name for _, name in anchors(fragment) if name]
+            label = clean(label).rstrip("：:")
+            if label == "导演":
+                director = names
+            elif label == "主演":
+                actors = names
+        synopsis = clean(
+            first(r'<span class="video-info-itemtitle">剧情：</span>\s*<div class="video-info-item">(.*?)</div>', block)
+        )
+        results.append(
+            {
+                "id": vod_id,
+                "title": title,
+                "url": path,
+                "poster": poster,
+                "year": year,
+                "category": category,
+                "region": region,
+                "status": status,
+                "director": director,
+                "actors": actors,
+                "synopsis": synopsis,
+            }
+        )
+    return results
+
+
+def parse_detail(page):
+    title = clean(first(r'<h1 class="page-title">(.*?)</h1>', page))
+    if not title:
+        title = clean(first(r"<title>(.*?)--", page)).replace("剧情介绍", "").strip()
+
+    labels = {}
+    for label, fragment in re.findall(
+        r'<span class="video-info-itemtitle">(.*?)</span>\s*<div class="video-info-item[^"]*">(.*?)</div>',
+        page,
+        re.S,
+    ):
+        label = clean(label).rstrip("：:")
+        fragment = fragment.replace('<p class="sqjj_a" style="display: none;">', "")
+        value = clean(fragment)
+        if label in ("导演", "主演"):
+            labels[label] = [name for _, name in anchors(fragment) if name] or [value]
+        else:
+            labels.setdefault(label, value)
+
+    aux = first(r'<div class="video-info-aux">(.*?)</div>', page)
+    aux_texts = [label for _, label in anchors(aux) if label]
+    genres = [x for x in aux_texts if x and not re.fullmatch(r"\d{4}", x)]
+    category = genres[0] if genres else ""
+    genres = genres[1:]
+
+    full_plot = clean(first(r'<p class="sqjj_a"[^>]*>(.*?)</p>', page))
+    plot = full_plot or labels.get("剧情", "") or clean(first(r'<p class="zkjj_a"[^>]*>(.*?)</p>', page))
+
+    tabs = re.findall(r'<div class="module-tab-item downtab-item[^"]*"><span data-dropdown-value="([^"]+)">', page)
+    rows = []
+    for block in page.split('<div class="module-row-one">')[1:]:
+        url = first(r'data-clipboard-text="([^"]+)"', block)
+        if not url:
+            continue
+        url = htmllib.unescape(url)
+        note = clean(first(r"<h4>(.*?)</h4>", block), maxlen=120)
+        if not note:
+            note = clean(first(r'title="复制《(.*?)》', block), maxlen=120)
+        rows.append({"url": url, "note": note})
+
+    links = []
+    seen = set()
+    for index, row in enumerate(rows):
+        if row["url"] in seen:
+            continue
+        seen.add(row["url"])
+        fallback = tabs[index] if index < len(tabs) else ""
+        links.append(
+            {
+                "provider": detect_provider(row["url"], fallback),
+                "url": row["url"],
+                "pwd": extract_pwd(row["url"], row["note"]),
+                "note": row["note"],
+            }
+        )
+
+    year = labels.get("年代", "") or next((x for x in aux_texts if re.fullmatch(r"\d{4}", x)), "")
+    serial = clean(first(r'<a class="video-serial"[^>]*>(.*?)</a>', page))
+    vod_id = first(r"/voddetail/(\d+)\.html", page)
+    poster = htmllib.unescape(first(r'<div class="module-item-pic"><img[^>]*data-src="([^"]+)"', page))
+    return {
+        "id": vod_id,
+        "title": title,
+        "url": "/voddetail/{}.html".format(vod_id),
+        "poster": poster,
+        "year": year,
+        "category": category,
+        "genres": genres,
+        "status": serial or labels.get("集数", ""),
+        "episodes": labels.get("集数", ""),
+        "director": labels.get("导演", []),
+        "actors": labels.get("主演", []),
+        "synopsis": plot,
+        "links": links,
+    }
+
+
+def parse_types(page):
+    found = []
+    seen = set()
+    for href, label in anchors(page):
+        match = re.match(r"/vodtype/(\d+)\.html", href)
+        if match and label and match.group(1) not in seen:
+            seen.add(match.group(1))
+            found.append({"id": match.group(1), "name": label})
+    return found
+
+
+def build_parser():
+    parser = argparse.ArgumentParser(
+        prog="wogg.py",
+        description="玩偶哥哥共享站（wogg.net）搜索与网盘资源提取工具",
+        formatter_class=argparse.RawDescriptionHelpFormatter,
+        epilog=(
+            "示例:\n"
+            "  python3 wogg.py search 折月亮\n"
+            "  python3 wogg.py search 喜剧 --limit 5 --links\n"
+            "  python3 wogg.py list --type 2 --page 2\n"
+            "  python3 wogg.py home --limit 10\n"
+            "  python3 wogg.py detail 133871 --json\n"
+            "  python3 wogg.py types\n"
+        ),
+    )
+    parser.add_argument("--base", default=DEFAULT_BASE, help="站点地址，默认 %(default)s")
+    parser.add_argument("--timeout", type=float, default=25.0, help="单次请求超时秒数，默认 25")
+    parser.add_argument("--json", action="store_true", help="输出 JSON")
+    parser.add_argument("-o", "--output", help="把结果写入 JSON 文件")
+    sub = parser.add_subparsers(dest="command", required=True)
+
+    search = sub.add_parser("search", aliases=["s"], help="按关键词搜索影片")
+    search.add_argument("keyword", help="片名、演员或导演")
+    search.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
+    search.add_argument("--links", action="store_true", help="同时抓取每部影片的网盘链接")
+
+    listing = sub.add_parser("list", aliases=["l"], help="按分类浏览影片")
+    listing.add_argument("--type", type=str, default="1", help="分类 ID，默认 1（电影），可用 types 查看")
+    listing.add_argument("--page", type=int, default=1, help="页码，默认 1")
+    listing.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
+    listing.add_argument("--links", action="store_true", help="同时抓取每部影片的网盘链接")
+
+    home = sub.add_parser("home", help="读取首页最新影片")
+    home.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
+
+    detail = sub.add_parser("detail", aliases=["d"], help="查看影片详情与网盘链接")
+    detail.add_argument("target", help="影片 ID 或 voddetail 链接")
+
+    sub.add_parser("types", help="列出全部分类 ID")
+    return parser
+
+
+def print_results(results, limit):
+    if not results:
+        print("没有找到结果。")
+        return
+    for index, item in enumerate(results[:limit], 1):
+        meta = " / ".join(x for x in (item.get("year"), item.get("category"), item.get("region")) if x)
+        print("{}. {}  [{}]".format(index, item["title"], item["status"] or "-"))
+        if meta:
+            print("   " + meta)
+        if item.get("synopsis"):
+            print("   " + item["synopsis"][:90])
+        print("   https://www.wogg.net" + item["url"].split("://")[-1])
+        if item.get("_links") is not None:
+            if item["_links"]:
+                for link in item["_links"]:
+                    tail = "  提取码: {}".format(link["pwd"]) if link["pwd"] else ""
+                    print("     - [{}] {}{}".format(link["provider"], link["url"], tail))
+            else:
+                print("     - 未找到网盘链接")
+        print()
+
+
+def print_detail(data):
+    print(data["title"])
+    meta = " / ".join(x for x in (data.get("year"), data.get("category")) if x)
+    if data.get("genres"):
+        meta += " / " + " / ".join(data["genres"])
+    if meta.strip(" /"):
+        print(meta.strip(" /"))
+    if data.get("status"):
+        print("状态: {}".format(data["status"]))
+    if data.get("director"):
+        print("导演: {}".format(" / ".join(data["director"])))
+    if data.get("actors"):
+        print("主演: {}".format(" / ".join(data["actors"][:12])))
+    if data.get("synopsis"):
+        print("简介: {}".format(data["synopsis"][:300]))
+    print("资源: {} 个".format(len(data["links"])))
+    for link in data["links"]:
+        note = "  ({})".format(link["note"]) if link["note"] and link["note"] != data["title"] else ""
+        pwd = "  提取码: {}".format(link["pwd"]) if link["pwd"] else ""
+        print("  [{}] {}{}{}".format(link["provider"], link["url"], pwd, note))
+
+
+def fetch_details(client, results, limit, links_only=False):
+    out = []
+    for item in results[:limit]:
+        try:
+            data = parse_detail(client.get(item["url"], use_cache=False))
+            item["_links"] = data["links"]
+            out.append(data)
+        except WoggError as exc:
+            print("警告: {} 详情获取失败 ({})".format(item["title"], exc), file=sys.stderr)
+            item["_links"] = []
+        time.sleep(0.4)
+    return out
+
+
+def main(argv=None):
+    args = build_parser().parse_args(argv)
+    client = Client(base=args.base, timeout=args.timeout)
+
+    try:
+        if args.command == "types":
+            data = parse_types(client.get("/"))
+            payload = {"types": data}
+            if args.json:
+                print(json.dumps(payload, ensure_ascii=False, indent=2))
+            else:
+                for item in data:
+                    print("{:>3}  {}".format(item["id"], item["name"]))
+        elif args.command in ("search", "s"):
+            keyword = args.keyword.strip()
+            path = "/vodsearch/{}{}.html".format(urllib.parse.quote(keyword), "-" * 13)
+            results = parse_search(client.get(path))
+            details = []
+            if args.links:
+                details = fetch_details(client, results, args.limit)
+            if args.json:
+                payload = {"keyword": keyword, "results": details or results[: args.limit]}
+                print(json.dumps(payload, ensure_ascii=False, indent=2))
+            else:
+                print("关键词: {}  共 {} 条\n".format(keyword, len(results)))
+                print_results(results, args.limit)
+        elif args.command in ("list", "l"):
+            path = "/vodshow/{0}--------{1}---.html".format(args.type, args.page)
+            results = parse_cards(client.get(path))
+            if args.links:
+                fetch_details(client, results, args.limit)
+            if args.json:
+                payload = {"type": args.type, "page": args.page, "results": results[: args.limit]}
+                print(json.dumps(payload, ensure_ascii=False, indent=2))
+            else:
+                print("分类 {}  第 {} 页  共 {} 条\n".format(args.type, args.page, len(results)))
+                print_results(results, args.limit)
+        elif args.command == "home":
+            results = parse_cards(client.get("/"))
+            payload = {"results": results[: args.limit]}
+            if args.json:
+                print(json.dumps(payload, ensure_ascii=False, indent=2))
+            else:
+                print("首页最新影片\n")
+                print_results(results, args.limit)
+        elif args.command in ("detail", "d"):
+            match = re.search(r"(\d+)", args.target)
+            if not match:
+                raise WoggError("请提供影片 ID 或 /voddetail/<id>.html 链接")
+            path = "/voddetail/{}.html".format(match.group(1))
+            data = parse_detail(client.get(path))
+            payload = data
+            if args.json:
+                print(json.dumps(payload, ensure_ascii=False, indent=2))
+            else:
+                print_detail(data)
+        else:
+            build_parser().print_help()
+            return 2
+    except WoggError as exc:
+        print("错误: {}".format(exc), file=sys.stderr)
+        return 1
+
+    if args.output:
+        try:
+            with open(args.output, "w", encoding="utf-8") as handle:
+                json.dump(payload, handle, ensure_ascii=False, indent=2)
+            print("\n已写入: {}".format(args.output), file=sys.stderr)
+        except OSError as exc:
+            print("写入失败: {}".format(exc), file=sys.stderr)
+            return 1
+    return 0
+
+
+if __name__ == "__main__":
+    sys.exit(main())
@@ -1,7 +1,7 @@
             print("   " + meta)
         if item.get("synopsis"):
             print("   " + item["synopsis"][:90])
-        print("   https://www.wogg.net" + item["url"].split("://")[-1])
+        print("   " + absolute_url(item["url"]))
         if item.get("_links") is not None:
             if item["_links"]:
                 for link in item["_links"]:
@@ -1,6 +1,22 @@
         raise WoggError("请求失败: {} ({})".format(url, last))
 
 
+def absolute_url(path, base=DEFAULT_BASE):
+    if path.startswith("http://") or path.startswith("https://"):
+        return path
+    return base.rstrip("/") + "/" + path.lstrip("/")
+
+
+def tidy_note(note, title=""):
+    note = SPACE_RE.sub(" ", note or "").strip(" -—")
+    for suffix in ("- 玩偶哥哥", "-玩偶哥哥"):
+        if note.endswith(suffix):
+            note = note[: -len(suffix)].strip()
+    if title and note in (title, title + " "):
+        return ""
+    return note
+
+
 def clean(fragment, maxlen=None):
     if not fragment:
         return ""
@@ -1,7 +1,8 @@
         print("简介: {}".format(data["synopsis"][:300]))
     print("资源: {} 个".format(len(data["links"])))
     for link in data["links"]:
-        note = "  ({})".format(link["note"]) if link["note"] and link["note"] != data["title"] else ""
+        note = tidy_note(link["note"], data["title"])
+        note = "  ({})".format(note) if note else ""
         pwd = "  提取码: {}".format(link["pwd"]) if link["pwd"] else ""
         print("  [{}] {}{}{}".format(link["provider"], link["url"], pwd, note))
 
@@ -1,10 +1,9 @@
     seen = set()
     blocks = page.split('<div class="module-item">')[1:]
     for block in blocks:
-        href = first(r'<a href="(/voddetail/(\d+)\.html)" title="([^"]*)"', block)
-        if not href:
-            continue
         match = re.search(r'<a href="(/voddetail/(\d+)\.html)" title="([^"]*)"', block, re.S)
+        if not match:
+            continue
         path, vod_id, title = match.groups()
         title = clean(title)
         if vod_id in seen:
@@ -1,8 +1,7 @@
         synopsis = clean(
             first(r'<span class="video-info-itemtitle">剧情：</span>\s*<div class="video-info-item">(.*?)</div>', block)
         )
-        results.append(
-            {
+        results.append(            {
                 "id": vod_id,
                 "title": title,
                 "url": path,
@@ -243,7 +243,8 @@
         synopsis = clean(
             first(r'<span class="video-info-itemtitle">剧情：</span>\s*<div class="video-info-item">(.*?)</div>', block)
         )
-        results.append(            {
+        results.append(
+            {
                 "id": vod_id,
                 "title": title,
                 "url": path,
@@ -1,9 +1,14 @@
     parser.add_argument("--timeout", type=float, default=25.0, help="单次请求超时秒数，默认 25")
     parser.add_argument("--json", action="store_true", help="输出 JSON")
     parser.add_argument("-o", "--output", help="把结果写入 JSON 文件")
+
+    common = argparse.ArgumentParser(add_help=False)
+    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出 JSON")
+    common.add_argument("-o", "--output", default=argparse.SUPPRESS, help="把结果写入 JSON 文件")
+
     sub = parser.add_subparsers(dest="command", required=True)
 
-    search = sub.add_parser("search", aliases=["s"], help="按关键词搜索影片")
+    search = sub.add_parser("search", aliases=["s"], parents=[common], help="按关键词搜索影片")
     search.add_argument("keyword", help="片名、演员或导演")
     search.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
     search.add_argument("--links", action="store_true", help="同时抓取每部影片的网盘链接")
@@ -386,13 +386,13 @@
     listing.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
     listing.add_argument("--links", action="store_true", help="同时抓取每部影片的网盘链接")
 
-    home = sub.add_parser("home", help="读取首页最新影片")
+    home = sub.add_parser("home", parents=[common], help="读取首页最新影片")
     home.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
 
-    detail = sub.add_parser("detail", aliases=["d"], help="查看影片详情与网盘链接")
+    detail = sub.add_parser("detail", aliases=["d"], parents=[common], help="查看影片详情与网盘链接")
     detail.add_argument("target", help="影片 ID 或 voddetail 链接")
 
-    sub.add_parser("types", help="列出全部分类 ID")
+    sub.add_parser("types", parents=[common], help="列出全部分类 ID")
     return parser
 
 
@@ -1,7 +1,7 @@
     search.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
     search.add_argument("--links", action="store_true", help="同时抓取每部影片的网盘链接")
 
-    listing = sub.add_parser("list", aliases=["l"], help="按分类浏览影片")
+    listing = sub.add_parser("list", aliases=["l"], parents=[common], help="按分类浏览影片")
     listing.add_argument("--type", type=str, default="1", help="分类 ID，默认 1（电影），可用 types 查看")
     listing.add_argument("--page", type=int, default=1, help="页码，默认 1")
     listing.add_argument("--limit", type=int, default=20, help="最多显示条数，默认 20")
@@ -203,6 +203,27 @@
     return results
 
 
+def section(page, start, end=None):
+    index = page.find(start)
+    if index < 0:
+        return ""
+    index += len(start)
+    if end:
+        stop = page.find(end, index)
+        if stop > index:
+            return page[index:stop]
+    return page[index:]
+
+
+def aux_labels(page):
+    aux = section(page, '<div class="video-info-aux">', '<div class="video-info-main">')
+    labels = []
+    for _, label in anchors(aux):
+        if label and label not in labels and not label.startswith("icon-"):
+            labels.append(label)
+    return labels
+
+
 def parse_search(page):
     results = []
     seen = set()
@@ -221,12 +242,13 @@
         seen.add(vod_id)
         poster = htmllib.unescape(first(r'data-src="([^"]+)"', block))
         status = clean(first(r'<a class="video-serial"[^>]*>(.*?)</a>', block))
-        aux = first(r'<div class="video-info-aux">(.*?)</div>', block)
-        aux_texts = [label for _, label in anchors(aux) if label]
-        aux_texts = [x for x in aux_texts if not x.startswith("icon-")]
+        aux_texts = aux_labels(block)
         year = next((x for x in aux_texts if re.fullmatch(r"\d{4}", x)), "")
-        category = aux_texts[0] if aux_texts and aux_texts[0] != year else ""
-        region = next((x for x in aux_texts if x not in (year, category) and len(x) <= 30), "")
+        category = next((x for x in aux_texts if x != year), "")
+        region = next(
+            (x for x in aux_texts if x not in (year, category) and len(x) <= 30),
+            "",
+        )
         director = []
         actors = []
         for label, fragment in re.findall(
@@ -302,8 +302,7 @@
         else:
             labels.setdefault(label, value)
 
-    aux = first(r'<div class="video-info-aux">(.*?)</div>', page)
-    aux_texts = [label for _, label in anchors(aux) if label]
+    aux_texts = aux_labels(page)
     genres = [x for x in aux_texts if x and not re.fullmatch(r"\d{4}", x)]
     category = genres[0] if genres else ""
     genres = genres[1:]
@@ -340,8 +340,13 @@
 
     year = labels.get("年代", "") or next((x for x in aux_texts if re.fullmatch(r"\d{4}", x)), "")
     serial = clean(first(r'<a class="video-serial"[^>]*>(.*?)</a>', page))
-    vod_id = first(r"/voddetail/(\d+)\.html", page)
-    poster = htmllib.unescape(first(r'<div class="module-item-pic"><img[^>]*data-src="([^"]+)"', page))
+    vod_id = first(r'data-mid="1" data-id="(\d+)"', page) or first(r"/voddetail/(\d+)\.html", page)
+    poster = htmllib.unescape(
+        first(r'<div class="video-cover">.*?data-src="([^"]+)"', page)
+        or first(r'<div class="module-item-pic"><img[^>]*data-src="([^"]+)"', page)
+    )
+    alias = clean(first(r'<h2 class="video-subtitle"[^>]*>(.*?)</h2>', page))
+    alias = re.sub(r"^又名[:：]\s*", "", alias)
     return {
         "id": vod_id,
         "title": title,
@@ -350,6 +350,7 @@
     return {
         "id": vod_id,
         "title": title,
+        "alias": alias,
         "url": "/voddetail/{}.html".format(vod_id),
         "poster": poster,
         "year": year,
@@ -1,6 +1,8 @@
 
 def print_detail(data):
     print(data["title"])
+    if data.get("alias"):
+        print("又名: {}".format(data["alias"]))
     meta = " / ".join(x for x in (data.get("year"), data.get("category")) if x)
     if data.get("genres"):
         meta += " / " + " / ".join(data["genres"])
@@ -1,6 +1,8 @@
     title = clean(first(r'<h1 class="page-title">(.*?)</h1>', page))
     if not title:
         title = clean(first(r"<title>(.*?)--", page)).replace("剧情介绍", "").strip()
+    if not first(r'data-mid="1" data-id="(\d+)"', page) and "voddetail" not in page:
+        raise WoggError("没有找到该影片，ID 或链接可能有误")
 
     labels = {}
     for label, fragment in re.findall(
