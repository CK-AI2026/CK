#!/usr/bin/env python3
"""
Notion 逐字稿自动生成监听器
后台运行，每60秒检查一次 Notion 数据库
发现勾选「生成逐字稿」的条目 → 自动生成逐字稿 → 写回 Notion → 取消勾选

用法: python3 notion-transcript-monitor.py
"""

import json
import os
import sys
import time
import subprocess
import urllib.request
import urllib.error

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
TOPIC_DB_ID = "3cfb48a1-123f-81a3-bd3e-ef018991ed28"
WHISPER_MODEL = "small"
CHECK_INTERVAL = 60
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")

NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}


def notion_request(method, endpoint, data=None):
    url = f"{NOTION_API}{endpoint}"
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f"  ❌ Notion API 错误: {e.code} {e.reason}")
        return None
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return None


def find_checked_items(db_id, db_name):
    data = notion_request("POST", f"/databases/{db_id}/query", {
        "filter": {
            "property": "生成逐字稿",
            "checkbox": {"equals": True}
        }
    })
    if not data or "results" not in data:
        return []

    items = []
    for page in data["results"]:
        props = page.get("properties", {})
        title = ""
        for key in ["标题", "知识标题", "Name", "title", "选题标题"]:
            if key in props and props[key].get("title"):
                title = props[key]["title"][0].get("plain_text", "")
                break
        url = ""
        for key in ["来源链接", "链接", "URL", "来源"]:
            if key in props:
                if props[key].get("url"):
                    url = props[key]["url"]
                    break
                elif props[key].get("rich_text"):
                    url = props[key]["rich_text"][0].get("plain_text", "")
                    break
        has_transcript = False
        if "文字稿" in props and props["文字稿"].get("rich_text"):
            existing = props["文字稿"]["rich_text"][0].get("plain_text", "")
            if len(existing) > 50:
                has_transcript = True
        items.append({
            "page_id": page["id"],
            "title": title,
            "url": url,
            "db_name": db_name,
            "has_transcript": has_transcript
        })
    return items


def download_and_transcribe(url):
    os.makedirs(TEMP_DIR, exist_ok=True)
    print(f"  尝试提取内置字幕...")
    cmd_sub = [
        "yt-dlp", "--write-subs", "--write-auto-subs",
        "--sub-lang", "zh,zh-Hans,zh-CN",
        "--skip-download", "--sub-format", "json3",
        "-o", os.path.join(TEMP_DIR, "%(id)s"), url
    ]
    subprocess.run(cmd_sub, capture_output=True, text=True, timeout=60)

    for f in os.listdir(TEMP_DIR):
        if f.endswith(('.json3', '.srt', '.vtt', '.txt')):
            text = extract_subtitle_text(os.path.join(TEMP_DIR, f))
            if text and len(text) > 50:
                print(f"  ✅ 提取到字幕({len(text)}字)")
                cleanup_temp()
                return text

    print(f"  未找到字幕，下载音频...")
    cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
        "-o", os.path.join(TEMP_DIR, "%(id)s.%(ext)s"),
        "--no-playlist", url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"  ❌ 下载失败: {result.stderr[:200]}")
        cleanup_temp()
        return None

    audio_file = None
    for f in os.listdir(TEMP_DIR):
        if f.endswith(('.mp3', '.m4a', '.webm', '.opus')):
            audio_file = os.path.join(TEMP_DIR, f)
            break
    if not audio_file:
        print(f"  ❌ 未找到音频文件")
        cleanup_temp()
        return None

    print(f"  🎙️ 语音识别中...")
    try:
        import whisper
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(audio_file, language="zh", verbose=False)
        text = result.get("text", "").strip()
        print(f"  ✅ 识别完成({len(text)}字)")
    except Exception as e:
        print(f"  ❌ 识别失败: {e}")
        text = None

    cleanup_temp()
    return text


def extract_subtitle_text(filepath):
    text = ""
    try:
        if filepath.endswith('.json3'):
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for event in data.get('events', []):
                    if 'segs' in event:
                        for seg in event['segs']:
                            if 'utf8' in seg:
                                text += seg['utf8']
        elif filepath.endswith('.srt') or filepath.endswith('.vtt'):
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.isdigit() and '-->' not in line and not line.startswith('WEBVTT'):
                        text += line
        elif filepath.endswith('.txt'):
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
    except:
        pass
    return text.strip()


def cleanup_temp():
    try:
        for f in os.listdir(TEMP_DIR):
            os.remove(os.path.join(TEMP_DIR, f))
    except:
        pass


def update_notion_page(page_id, transcript_text):
    chunks = []
    for i in range(0, len(transcript_text), 2000):
        chunks.append({"text": {"content": transcript_text[i:i+2000]}})
    data = {
        "properties": {
            "文字稿": {"rich_text": chunks},
            "生成逐字稿": {"checkbox": False}
        }
    }
    result = notion_request("PATCH", f"/pages/{page_id}", data)
    return result is not None


def main():
    if not NOTION_TOKEN:
        print("❌ 未设置 NOTION_TOKEN，监听器未启动")
        return
    print("=" * 50)
    print("🔄 Notion 逐字稿监听器已启动")
    print(f"   检查间隔: {CHECK_INTERVAL}秒")
    print(f"   模型: Whisper {WHISPER_MODEL}")
    print("   按 Ctrl+C 停止")
    print("=" * 50)

    # 逐字稿是选题库工作流的一部分。知识库不要求拥有“生成逐字稿”、
    # “文字稿”或“来源链接”字段，因此不监听知识库，避免字段不存在的 API 错误。
    db_list = [(TOPIC_DB_ID, "选题库")]

    while True:
        for db_id, db_name in db_list:
            items = find_checked_items(db_id, db_name)
            for item in items:
                print(f"\n{'='*50}")
                print(f"📋 [{db_name}] {item['title'][:30]}...")
                if not item["url"]:
                    print("  ⚠️ 没有来源链接，跳过")
                    notion_request("PATCH", f"/pages/{item['page_id']}", {
                        "properties": {"生成逐字稿": {"checkbox": False}}
                    })
                    continue
                if item["has_transcript"]:
                    print("  ℹ️ 已有文字稿，跳过")
                    notion_request("PATCH", f"/pages/{item['page_id']}", {
                        "properties": {"生成逐字稿": {"checkbox": False}}
                    })
                    continue
                print(f"  🔗 链接: {item['url'][:50]}...")
                transcript = download_and_transcribe(item["url"])
                if transcript:
                    print(f"  📝 写入 Notion...")
                    if update_notion_page(item["page_id"], transcript):
                        print(f"  ✅ 完成！逐字稿已写入")
                    else:
                        print(f"  ❌ 写入失败")
                else:
                    print(f"  ❌ 逐字稿生成失败")
                    notion_request("PATCH", f"/pages/{item['page_id']}", {
                        "properties": {"生成逐字稿": {"checkbox": False}}
                    })
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 监听器已停止")
