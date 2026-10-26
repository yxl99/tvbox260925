# coding=utf-8
"""
目标站: 短剧大全 (https://lssy.net)
特性: 自定义PHP前端、分页列表(?p=N)、搜索过滤(?keyword=xxx&p=N)、详情页直出m3u8
优化: 分页加载全量、搜索模拟分类、parse=0直链秒播

========================== 本次改动 ===========================
[调整] 分类精简: 分类栏只保留"短剧大全"(全部), 原 11 个题材分类
       (重生/穿越/都市/甜宠/虐恋/战神/逆袭/古装/家庭/悬疑/剧情)
       移入筛选器"题材"(实测 ?keyword= 搜索接口内容真实有效);
[配套] categoryContent 支持 extend 传 {"kw":"重生"} (dict/JSON字符串双解析,
       壳子传字符串不再崩); 未知 tid 归一到"全部"兜底
==============================================================
"""

import re
import sys
import json
import urllib.parse
import time
sys.path.append('..')
from base.spider import Spider

class Spider(Spider):
    # 题材关键词(实测搜索接口有效: 重生32页/穿越8页/战神6页)
    _KEYWORDS = ['重生', '穿越', '都市', '甜宠', '虐恋',
                 '战神', '逆袭', '古装', '家庭', '悬疑', '剧情']

    def init(self, extend=""):
        self.site_url = "https://lssy.net"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site_url + '/',
            'Connection': 'keep-alive',
        }

        # [调整] 一级分类只保留"短剧大全"(全部), 题材移入筛选器
        self.categories = [
            {"type_id": "1", "type_name": "短剧大全"},
        ]
        # [调整] 题材作为"短剧大全"分类下的筛选器(?keyword= 搜索实现)
        self.filters = {
            "1": [
                {"key": "kw", "name": "题材",
                 "value": [{"n": "全部", "v": ""}] +
                          [{"n": k, "v": k} for k in self._KEYWORDS]},
            ]
        }

    def _safe_fetch(self, url, max_retry=1, timeout=8):
        headers = self.headers.copy()
        headers['Referer'] = url if 'detail.php' in url else self.site_url + '/'
        for i in range(max_retry + 1):
            try:
                resp = self.fetch(url, headers=headers)
                if resp and resp.status_code == 200:
                    return resp
            except Exception:
                pass
            if i < max_retry:
                time.sleep(0.2)
        return None

    def _fix_url(self, url):
        if not url:
            return ''
        url = url.strip()
        if url.startswith('http'):
            return url
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return self.site_url + url
        return self.site_url + '/' + url

    def _parse_video_list(self, html):
        video_list = []
        seen = set()
        cards = re.findall(r'<div class="card">(.*?)</div>\s*</div>', html, re.DOTALL)
        for block in cards:
            vid_m = re.search(r'detail\.php\?vid=(\d+)', block)
            if not vid_m:
                continue
            vod_id = vid_m.group(1)
            if vod_id in seen:
                continue
            seen.add(vod_id)
            title = ''
            title_m = re.search(r'<div class="title">(.*?)</div>', block, re.DOTALL)
            if title_m:
                title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
            pic = ''
            pic_m = re.search(r'data-original="([^"]+)"', block)
            if not pic_m:
                pic_m = re.search(r'src="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', block)
            if pic_m:
                pic = self._fix_url(pic_m.group(1))
            remark = ''
            ep_m = re.search(r'(\d+)\s*集', block)
            if ep_m:
                remark = f"{ep_m.group(1)}集"
            if title:
                video_list.append({
                    "vod_id": vod_id,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": remark,
                })
        return video_list

    def _extract_page_info(self, html):
        """从分页区域提取总页码"""
        pagecount = 1
        pages = re.findall(r'\?p=(\d+)&keyword=', html)
        if pages:
            pagecount = max(pagecount, max(map(int, pages)))
        total = 0
        total_m = re.search(r'共找到\s*([\d,]+)\s*部', html)
        if total_m:
            total = int(total_m.group(1).replace(',', ''))
        if not total:
            total = 20 * pagecount
        return pagecount, total

    def homeContent(self, filter):
        # [调整] 按需求去掉推荐页: 首页仅展示分类入口, 不再拉取推荐列表
        return {"class": self.categories, "list": [], "filters": self.filters}

    def homeVideoContent(self):
        # [调整] 推荐页已去掉(返回空列表而非None, 避免部分壳子异常)
        return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        """
        [调整] 分类内容: 仅"短剧大全"(全部)一个分类;
        题材经筛选器 extend 传 {"kw": "重生"} 走 ?keyword= 搜索接口
        """
        page = int(pg) if str(pg).isdigit() else 1  # [修复] 非数字pg不再崩

        # [配套] extend dict/JSON字符串双解析(壳子传字符串不再崩)
        ext = {}
        if extend:
            try:
                ext = json.loads(extend) if isinstance(extend, str) else extend
                if not isinstance(ext, dict):
                    ext = {}
            except Exception:
                ext = {}
        keyword = str(ext.get('kw') or '').strip()

        if keyword:
            # 有题材关键词: 用搜索接口
            encoded_kw = urllib.parse.quote(keyword)
            url = f"{self.site_url}/?keyword={encoded_kw}&p={page}"
        else:
            # 无关键词: 用全部分页接口(未知 tid 也归到全部)
            if page <= 1:
                url = self.site_url + "/"
            else:
                url = f"{self.site_url}/?p={page}"

        resp = self._safe_fetch(url)
        if not resp:
            return {"list": [], "page": page, "pagecount": 1, "limit": 20, "total": 0}

        video_list = self._parse_video_list(resp.text)
        pagecount, total = self._extract_page_info(resp.text)

        return {
            "list": video_list,
            "page": page,
            "pagecount": pagecount,
            "limit": 20,
            "total": total
        }

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vod_id = ids[0]
        url = f"{self.site_url}/detail.php?vid={vod_id}"
        resp = self._safe_fetch(url)
        if not resp:
            return {"list": []}
        html = resp.text

        vod_name = ''
        title_m = re.search(r'<title>(.*?)</title>', html)
        if title_m:
            vod_name = title_m.group(1).split('-')[0].split('_')[0].strip()
        if not vod_name or vod_name == '短剧大全':
            name_m = re.search(r'<div class="video-title">(.*?)</div>', html, re.DOTALL)
            if name_m:
                vod_name = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()

        vod_pic = ''
        pic_m = re.search(r'<video[^>]*poster="([^"]+)"', html)
        if not pic_m:
            pic_m = re.search(r'data-original="([^"]+)"', html)
        if pic_m:
            vod_pic = self._fix_url(pic_m.group(1))

        vod_content = ''
        desc_m = re.search(r'<meta name="description" content="([^"]+)"', html)
        if desc_m:
            vod_content = desc_m.group(1).strip()
            vod_content = re.sub(r'短剧大全-短剧网提供.*?在线观看[。，]?', '', vod_content).strip()

        vod_year = ''
        year_m = re.search(r'(\d{4})[年/-]', html)
        if year_m:
            vod_year = year_m.group(1)

        play_urls = []
        episodes = re.findall(
            r'<div[^>]*class="episode[^"]*"[^>]*data-src="([^"]+)"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        if episodes:
            for idx, (src, ep_raw) in enumerate(episodes, 1):
                ep_name = re.sub(r'<[^>]+>', '', ep_raw).strip()
                if not ep_name:
                    ep_name = f"第{idx}集"
                m3u8_url = src if src.startswith('http') else self._fix_url(src)
                play_urls.append(f"{ep_name}${m3u8_url}")
        else:
            m3u8_links = re.findall(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", html)
            m3u8_links = list(dict.fromkeys(m3u8_links))
            for idx, m3u8_url in enumerate(m3u8_links, 1):
                play_urls.append(f"第{idx}集${m3u8_url}")

        if play_urls:
            vod_play_from = "直链播放"
            vod_play_url = '#'.join(play_urls)
        else:
            vod_play_from = "默认线路"
            vod_play_url = f"播放${self.site_url}/detail.php?vid={vod_id}"

        return {"list": [{
            "vod_id": vod_id,
            "vod_name": vod_name,
            "vod_pic": vod_pic,
            "vod_content": vod_content,
            "vod_actor": '',
            "vod_director": '',
            "vod_area": '',
            "vod_year": vod_year,
            "vod_play_from": vod_play_from,
            "vod_play_url": vod_play_url,
        }]}

    def searchContent(self, key, quick, pg="1"):
        """搜索内容：真实搜索接口，支持分页"""
        page = int(pg) if pg else 1
        encoded_key = urllib.parse.quote(key)
        url = f"{self.site_url}/?keyword={encoded_key}&p={page}"

        resp = self._safe_fetch(url)
        if not resp:
            return {"list": [], "page": page, "pagecount": 1, "total": 0}

        video_list = self._parse_video_list(resp.text)
        pagecount, total = self._extract_page_info(resp.text)

        return {
            "list": video_list,
            "page": page,
            "pagecount": pagecount,
            "limit": 20,
            "total": total
        }

    def playerContent(self, flag, id, vipFlags):
        if id.startswith('http'):
            play_url = id
        else:
            play_url = self._fix_url(id)
        if '.m3u8' in play_url:
            return {
                "parse": 0,
                "url": play_url,
                "header": {
                    'User-Agent': self.headers['User-Agent'],
                    'Referer': self.site_url + '/',
                }
            }
        resp = self._safe_fetch(play_url)
        if resp:
            m3u8_m = re.search(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", resp.text)
            if m3u8_m:
                return {
                    "parse": 0,
                    "url": m3u8_m.group(0),
                    "header": {
                        'User-Agent': self.headers['User-Agent'],
                        'Referer': play_url,
                    }
                }
        return {"parse": 1, "url": play_url, "header": self.headers}

    def localProxy(self, param):
        return [200, "video/MP2T", "", ""]

    def isVideoFormat(self, url):
        return any(url.endswith(ext) for ext in ['.m3u8', '.mp4', '.ts', '.flv', '.avi', '.mkv'])
