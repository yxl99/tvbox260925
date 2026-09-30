# -*- coding: utf-8 -*-
import gzip
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

sys.path.append('..')
from base.spider import Spider


CONFIG = {
    "host": "https://momodrift.top",
    # 平台盐：web 端固定值，用于派生 body 加密 key
    "salt": "7961beb44246e3012ce228d6b5ced05a",
    "version": "2.0.0",
    "page_size": 20,
    "play_from": "黄豆短剧",
    "headers": {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/126.0.6478.61 Safari/537.36'),
        'Referer': 'https://momodrift.top/',
    },
}


class Spider(Spider):

    def init(self, extend=""):
        self.salt = CONFIG['salt']
        self.host = CONFIG['host']
        self.session_id = uuid.uuid4().hex
        self.device_id = uuid.uuid4().hex
        self.headers = dict(CONFIG['headers'])

    def getName(self):
        return "黄豆短剧"

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    # ---------------- 协议层 ----------------
    def _derive_key(self, request_id):
        raw = bytes.fromhex(request_id.replace("-", ""))
        return hmac.new(self.salt.encode('utf-8'), raw, hashlib.sha256).digest()

    def _encrypt_body(self, request_id, data, token=""):
        payload = {"token": token, "deviceId": self.device_id, "data": data}
        plain = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode('utf-8'))
        key = self._derive_key(request_id)
        iv = os.urandom(16)
        ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plain, 16))
        return iv + ct

    def _decrypt_body(self, raw, request_id):
        if raw.startswith(b'{'):
            # 明文错误响应（如未登录/参数错误）
            return json.loads(raw.decode('utf-8'))
        key = self._derive_key(request_id)
        iv, ct = raw[:16], raw[16:]
        plain = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ct), 16)
        return json.loads(gzip.decompress(plain).decode('utf-8'))

    def fetch(self, path, data=None, timeout=8):
        url = self.host + path
        request_id = str(uuid.uuid4())
        t = str(int(time.time()))
        body = self._encrypt_body(request_id, data or {})
        u = url.replace("https://", "").replace("http://", "")
        sig = hashlib.md5(f"Dart|{self.session_id}|{request_id}|{t}|{u}".encode('utf-8')).hexdigest()
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "time": t,
            "requestid": request_id,
            "sessionid": self.session_id,
            "sign": sig + "-" + t,
            "devicetype": "web",
            "version": CONFIG['version'],
            "User-Agent": self.headers['User-Agent'],
        }
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=timeout)
            return self._decrypt_body(resp.content, request_id)
        except Exception:
            return None

    # ---------------- 数据映射 ----------------
    def _vod(self, i, cat_name=""):
        pic = i.get('img_y') or i.get('img') or i.get('img_x') or ''
        remark = i.get('update_label') or ''
        vod = {
            'vod_id': str(i['id']),
            'vod_name': i.get('name', ''),
            'vod_pic': pic,
            'vod_remarks': remark,
            'vod_content': i.get('description', '') or '',
            'vod_actor': '',
            'vod_director': '',
            'vod_area': i.get('category') or cat_name,
            'vod_year': '',
        }
        return vod

    # ---------------- TVBox 接口 ----------------
    def homeContent(self, filter):
        result = {'class': [], 'filters': {}}
        data = self.fetch('/api/drama/navList')
        if not data or data.get('status') != 'y':
            return result
        for c in data['data']['list']:
            result['class'].append({'type_id': str(c['id']), 'type_name': c['name']})
        return result

    def homeVideoContent(self):
        vods = []
        seen = set()
        # 抓取各导航分类的首页推荐块，聚合去重
        navs = self.fetch('/api/drama/navList')
        codes = [c['code'] for c in (navs or {}).get('data', {}).get('list', [])] or ['yuandou']
        for code in codes[:3]:
            data = self.fetch('/api/drama/navBlock', {"code": code, "tab": 0, "page": "1"})
            if not data or data.get('status') != 'y':
                continue
            for block in data['data'].get('list', []):
                for item in block.get('items', []):
                    vid = str(item['id'])
                    if vid in seen:
                        continue
                    seen.add(vid)
                    vods.append(self._vod(item))
        return {'list': vods}

    def categoryContent(self, tid, pg, filter, extend):
        params = {
            "page": str(pg),
            "page_size": str(CONFIG['page_size']),
            "navId": str(tid),
        }
        if extend:
            order = extend.get('order', '')
            if order:
                params['order'] = order
        data = self.fetch('/api/drama/list', params)
        result = {'list': [], 'page': int(pg), 'pagecount': 1, 'limit': CONFIG['page_size'], 'total': 0}
        if not data or data.get('status') != 'y' or not data.get('data'):
            return result
        lst = data['data'].get('list') or []
        result['list'] = [self._vod(i) for i in lst]
        result['total'] = len(lst)
        if lst:
            result['pagecount'] = int(pg) + 1 if len(lst) >= CONFIG['page_size'] else int(pg)
        return result

    def detailContent(self, ids):
        vod_id = str(ids[0])
        data = self.fetch('/api/drama/detail', {"id": vod_id})
        if not data or data.get('status') != 'y':
            return {'list': []}
        d = data['data']
        vod = self._vod(d)
        episodes = d.get('episodes') or []
        if episodes:
            names = []
            for ep in episodes:
                seq = ep.get('seq')
                name = ep.get('name') or f"第{seq}集"
                names.append(f"{name}${vod_id}@@{seq}")
            vod['vod_play_from'] = CONFIG['play_from']
            vod['vod_play_url'] = '#'.join(names)
        return {'list': [vod]}

    def searchContent(self, key, quick, pg="1"):
        data = self.fetch('/api/drama/list', {
            "page": str(pg),
            "page_size": "20",
            "keywords": key,
        })
        result = {'list': [], 'page': pg}
        if not data or data.get('status') != 'y' or not data.get('data'):
            return result
        lst = data['data'].get('list') or []
        result['list'] = [self._vod(i) for i in lst]
        return result

    def playerContent(self, flag, id, vipFlags):
        ids = str(id).split('@@')
        vod_id = ids[0]
        seq = ids[1] if len(ids) > 1 else '1'
        data = self.fetch('/api/drama/play', {"id": vod_id, "seq": seq})
        header = {
            'User-Agent': self.headers['User-Agent'],
            'Referer': self.headers['Referer'],
        }
        if not data or data.get('status') != 'y' or not data.get('data'):
            return {'parse': 0, 'url': '', 'header': header}
        d = data['data']
        m3u8 = d.get('m3u8') or ''
        if not m3u8:
            lines = d.get('lines') or []
            m3u8 = lines[0]['url'] if lines else ''
        return {'parse': 0, 'url': m3u8, 'header': header}

    def localProxy(self, param):
        pass

    def host_late(self, url_list):
        if isinstance(url_list, str):
            urls = [u.strip() for u in url_list.split(',')]
        else:
            urls = url_list
        if len(urls) <= 1:
            return urls[0] if urls else self.host
        results = {}
        threads = []

        def test_host(url):
            try:
                start_time = time.time()
                response = requests.head(url, timeout=1.0, allow_redirects=False)
                delay = (time.time() - start_time) * 1000
                results[url] = delay
            except Exception:
                results[url] = float('inf')

        for url in urls:
            t = threading.Thread(target=test_host, args=(url,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return min(results.items(), key=lambda x: x[1])[0]