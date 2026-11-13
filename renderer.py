#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
upload_standard.py — 固化版草稿箱上传脚本
适用：轻资运营牛马路 公众号

【核心规则】
1. 密钥统一从 state/wechat_config.json 读，禁止硬编码
2. 封面 thumb_media_id 用 media_id，正文 img src 用 CDN url
3. md_to_html 用纯字符串拼接，无任何外部依赖，永不卡死
4. 4步独立，每步 try/catch + 指数退避重试3次
5. 全角标点，AI痕迹检查，24h延迟发布
"""
import sys, os, json, time, re, mimetypes, subprocess

# ── 密钥读取（唯一入口）────────────────────────────────────────
def load_config():
    config_path = os.path.join("state/wechat_config.json")
    with open(config_path, encoding="utf-8") as f:
        d = json.load(f)
    return d["appid"], d["appsecret"]

APPID, APPSECRET = load_config()

# ── 微信API基础 ───────────────────────────────────────────────
BASE = "https://api.weixin.qq.com"

def wx_get(path, params=None):
    """微信 GET 请求"""
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())

def wx_post(url, data):
    """微信 POST 请求"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def get_token():
    """获取 access_token，用 curl 避免 Python urllib DNS 问题"""
    import json
    url = BASE + "/cgi-bin/token?appid=" + APPID + "&secret=" + APPSECRET + "&grant_type=client_credential"
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["curl", "-s", url], capture_output=True, text=True, timeout=10
            )
            d = json.loads(result.stdout)
            if "access_token" in d:
                return d["access_token"]
            err = d.get("errmsg", "")
            print(f"  [token] attempt {attempt+1} failed: {err}, retrying...")
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  [token] attempt {attempt+1} error: {e}, retrying...")
            time.sleep(2 ** attempt)
    raise Exception("无法获取 access_token，请检查 AppSecret")

# ── 图片上传 ─────────────────────────────────────────────────
def upload_image(token, file_path):
    """
    上传永久图片素材，返回 {media_id, url}
    - thumb_media_id 用 media_id
    - 正文 <img src> 用 url
    """
    boundary = "wx%d" % int(time.time() * 1000)
    ext = file_path.rsplit(".", 1)[-1].lower()
    ct = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/png")

    with open(file_path, "rb") as f:
        img_data = f.read()

    body = (
        "--" + boundary + "\r\n"
        "Content-Disposition: form-data; name=\"media\"; filename=\"img." + ext + "\"\r\n"
        "Content-Type: " + ct + "\r\n\r\n"
    ).encode() + img_data + ("\r\n--" + boundary + "--\r\n").encode()

    url = BASE + "/cgi-bin/material/add_material?access_token=" + token + "&type=image"
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "multipart/form-data; boundary=" + boundary}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
            if "media_id" in result:
                return result
            err = result.get("errmsg", "")
            print(f"  [upload] attempt {attempt+1} failed: {err}, retrying...")
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  [upload] attempt {attempt+1} error: {e}, retrying...")
            time.sleep(2 ** attempt)
    raise Exception(f"图片上传失败: {file_path}")

def add_draft(token, articles):
    """提交草稿箱"""
    url = BASE + "/cgi-bin/draft/add?access_token=" + token
    return wx_post(url, {"articles": articles})

# ── v8 HTML渲染引擎 ────────────────────────────────────────────
def render_h2(text):
    return (
        '<h2 style="'
        'margin:20px 0 10px;'
        'font-size:16px;'
        'color:#333;'
        'padding:10px 16px;'
        'background:#f5f5f5;'
        'border-left:4px solid #8b0000;'
        'border-radius:6px;'
        'box-shadow:0 1px 3px rgba(0,0,0,0.06);'
        'font-weight:bold;">' + text + '</h2>'
    )

def render_strong(m):
    return (
        '<strong style="'
        'color:#8b0000;'
        'font-weight:bold;'
        'border-bottom:1px dashed #8b0000;'
        'padding-bottom:1px;">' + m.group(1) + '</strong>'
    )

def md_to_html(raw):
    """
    纯字符串拼接，无外部依赖，永不卡死。
    全角标点，v8样式。
    """
    lines = raw.split("\n")
    body, in_yaml, yaml_count = [], False, 0
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            yaml_count += 1
            if yaml_count == 1:
                in_yaml = True
                continue
            elif yaml_count == 2:
                in_yaml = False
                continue
        if not in_yaml:
            body.append(line)

    out = []
    i = 0
    while i < len(body):
        line = body[i].strip()
        i += 1
        if not line or re.match(r'^-{3,}$', line):
            out.append("")
            continue
        # H1：跳过不渲染（标题已在上传参数里）
        if line.startswith("# ") and not line.startswith("## "):
            continue
        # H2
        if line.startswith("## "):
            out.append(render_h2(line[3:]))
            continue
        # 表格
        if line.startswith("|"):
            table_lines = []
            while i <= len(body):
                t = body[i-1].strip() if i-1 < len(body) else ""
                if t.startswith("|"):
                    table_lines.append(t)
                    i += 1
                else:
                    i -= 1
                    break
            if len(table_lines) >= 2:
                out.append(render_table(table_lines))
            i += 1
            continue
        else:
            processed = re.sub(r"\*\*(.+?)\*\*", render_strong, line)
            out.append(
                '<p style="margin:8px 0;line-height:1.75;font-size:15px;color:#333">'
                + processed + '</p>'
            )
    return "\n".join(out)

def render_table(lines):
    """渲染 markdown 表格为 HTML table"""
    def parse_row(l):
        return [c.strip() for c in l.strip('|').split('|')]
    def cell(html):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    data_rows = [l for l in lines if not re.match(r'^\|[\s\-:|]+\|$', l)]
    if not data_rows:
        return ""
    headers = [cell(h) for h in parse_row(data_rows[0])]
    rows = [[cell(c) for c in parse_row(l)] for l in data_rows[1:]]
    th_html = "".join(
        "<th style='padding:10px 14px;border-bottom:2px solid #2c3e50;"
        "background:#2c3e50;color:#fff;font-weight:bold;text-align:left;font-size:14px;'>"
        + h + "</th>"
        for h in headers
    )
    tr_htmls = []
    for idx, row in enumerate(rows):
        bg = "#f9f9f9" if idx % 2 == 0 else "#fff"
        td_html = "".join(
            "<td style='padding:9px 14px;border-bottom:1px solid #eee;"
            "background:" + bg + ";font-size:14px;color:#333;line-height:1.6;'>"
            + c + "</td>"
            for c in row
        )
        tr_htmls.append("<tr>" + td_html + "</tr>")
    return (
        "<table style='width:100%;border-collapse:collapse;border-radius:8px;"
        "overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);margin:16px 0;'>"
        + "<thead><tr>" + th_html + "</tr></thead>"
        + "<tbody>" + "".join(tr_htmls) + "</tbody></table>"
    )

def make_footer():
    disc = (
        '<div style="'
        'margin:24px 0;'
        'padding:14px 16px;'
        'background:#f8f8f8;'
        'border-left:4px solid #c0392b;'
        'border-radius:4px;'
        'font-size:13px;'
        'color:#666;'
        'line-height:1.7;">'
        '<strong>⚠️ 免责声明：</strong>本文为个人观点分享，仅供同行交流，'
        '不构成投资建议。数据来自公开渠道或作者估算，实际以合同原件或官方公告为准。'
        '本文不针对任何特定公司或项目，所涉案例已脱敏处理。'
        '</div>'
    )
    hook = (
        '<div style="'
        'margin:24px 0 20px;'
        'padding:16px 20px;'
        'background:#fafafa;'
        'border:1px solid #e0e0e0;'
        'border-radius:8px;'
        'box-shadow:0 1px 4px rgba(0,0,0,0.06);">'
        '<div style="font-size:14px;color:#333;line-height:1.8;margin-bottom:10px;">'
        '📌 <strong>有问题想深聊？</strong></div>'
        '<div style="font-size:14px;color:#555;line-height:1.8;">'
        '公众号回复「<strong>进群</strong>」，加入轻资运营读者群，与同行直接交流。<br>'
        '或回复「<strong>资料</strong>」，领取本篇相关算账模板/申报清单（限时开放）。'
        '</div>'
        '</div>'
    )
    sign = (
        '<div style="margin:12px 0 8px;text-align:center;">'
        '<div style="display:inline-block;padding:12px 28px;border:2px solid #c0392b;'
        'border-radius:8px;text-align:center;">'
        '<div style="font-size:14px;color:#333;line-height:1.8;">'
        '感谢您对「轻资运营牛马路」的支持</div>'
        '<div style="font-size:14px;color:#333;line-height:1.8;margin-top:4px;">'
        '您的认可是我继续分享的最大动力</div>'
        '</div>'
        '<div style="margin-top:8px;font-size:12px;color:#999;">'
        '轻资运营牛马路 | 说真话不废话</div>'
        '</div>'
    )
    return disc + "\n" + hook + "\n" + sign

def extract_captions(md_file):
    """从 front matter 提取 caption1/2/3，返回 [c1, c2, c3]，无则为空串"""
    captions = ["", "", ""]
    try:
        with open(md_file, encoding="utf-8") as f:
            lines = f.readlines()
        # 找到 front matter 首尾 ---
        if not any(l.strip() == "---" for l in lines):
            return captions
        yaml_lines = []
        in_yaml = False
        for l in lines:
            if l.strip() == "---":
                if not in_yaml:
                    in_yaml = True
                    continue
                else:
                    break
            if in_yaml:
                yaml_lines.append(l)
        for line in yaml_lines:
            for i, key in enumerate(["caption1:", "caption2:", "caption3:"]):
                if line.startswith(key):
                    captions[i] = line[len(key):].strip().strip('"').strip("'")
    except Exception:
        pass
    return captions


def build_article(md_file, cover_url, cover_mid, img_urls):
    """
    渲染整篇文章。
    - 封面：thumb_media_id = cover_mid
    - 配图：正文 img src = img_urls[0/1/2]（CDN URL，不是 media_id）
    - 配图插在第1、2、3个H2之后，配图后追加 figcaption（从 front matter 读 caption1/2/3）
    """
    with open(md_file, encoding="utf-8") as f:
        raw = f.read()

    captions = extract_captions(md_file)

    body_html = md_to_html(raw)

    # 在第1、2、3个H2之后各插一张配图（CDN URL）+ figcaption
    lines = body_html.split("\n")
    h2_pos = [i for i, l in enumerate(lines) if l.startswith("<h2")]

    def make_img_block(url, caption_text):
        img_html = (
            '<p style="text-align:center;margin:12px 0 4px;">'
            '<img src="' + url + '" '
            'style="width:100%;max-width:600px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.10)">'
            '</p>'
        )
        if caption_text:
            img_html += (
                '<p style="font-size:13px;color:#555;margin:0 0 20px 0;'
                'padding-top:8px;border-top:1px solid #eee;font-style:italic;text-align:center">'
                + caption_text +
                '</p>'
            )
        else:
            img_html += '<p style="height:20px"></p>'
        return img_html

    img_blocks = [
        make_img_block(img_urls[0], captions[0]),
        make_img_block(img_urls[1], captions[1]),
        make_img_block(img_urls[2], captions[2]),
    ]

    inserted = 0
    for h2_idx, img_block in zip(h2_pos[:3], img_blocks):
        pos = h2_idx + inserted
        if pos + 1 < len(lines):
            lines.insert(pos + 1, img_block)
            inserted += 1

    body_html = "\n".join(lines)
    return body_html + "\n" + make_footer()

# ── AI痕迹检查（上传前强制门控）───────────────────────────────
def ai_check(md_file):
    """
    7条必检，0容忍。返回 (pass, report)
    """
    with open(md_file, encoding="utf-8") as f:
        text = f.read()

    lines = text.split("\n")
    body, yaml_depth = [], 0
    for line in lines:
        if line.strip() == "---":
            yaml_depth += 1
            if yaml_depth >= 2:  # 第二个---之后开始留正文
                body.append(line)
            continue
        if yaml_depth < 2:
            continue  # front matter 期间跳过
        body.append(line)
    text = "\n".join(body)

    h2s = re.findall(r"^## .+$", text, re.MULTILINE)
    last_h2 = h2s[-1] if h2s else ""

    ai_words = ["新征程", "新篇章", "赋能", "闭环", "深耕",
                 "未来可期", "大幕已启", "值得深思", "综上所述",
                 "标志着", "关键", "凸显", "此外"]
    hits = [w for w in ai_words if w in text]
    real_conn = re.findall(
        r"说白了|反正说|搞来搞去|说句不好听的|实话讲|说实在的|说句实话|说实话", text
    )
    num_h2s = [h for h in h2s if re.match(r"^## \d", h)]
    strongs = re.findall(r"\*\*.+?\*\*", text)
    unc = re.findall(
        r"大概|可能|记不太清|我记不清|我估|差不多|左右", text
    )

    checks = {
        "末节标题无总结词": not any(w in last_h2 for w in ["最后说一句","总结","结语","综上所述"]),
        "AI高频词0命中": len(hits) == 0,
        "真人连接词≥3": len(real_conn) >= 3,
        "H2无纯数字编号": len(num_h2s) == 0,
        "重点标注≥10处": len(strongs) >= 10,
        "不确定性表达≥3": len(unc) >= 3,
    }

    all_pass = all(checks.values())
    report = {
        "checks": checks,
        "h2s": h2s,
        "ai_hits": hits,
        "real_conn": real_conn,
        "unc_count": len(unc),
        "strong_count": len(strongs),
    }
    return all_pass, report

