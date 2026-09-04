#!/usr/bin/env python3
"""
抖音视频逐字稿生成工具
用法: python3 transcribe.py "https://v.douyin.com/xxxxx"
"""

import sys
import os
import subprocess
import json
import time

WHISPER_MODEL = "small"
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

def check_dependencies():
    print("🔍 检查依赖...")
    issues = []
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print("  ✅ ffmpeg 已安装")
    except (FileNotFoundError, subprocess.CalledProcessError):
        issues.append("ffmpeg 未安装。Mac安装命令: brew install ffmpeg")
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        print("  ✅ yt-dlp 已安装")
    except (FileNotFoundError, subprocess.CalledProcessError):
        issues.append("yt-dlp 未安装。安装命令: pip3 install yt-dlp")
    try:
        import whisper
        print("  ✅ openai-whisper 已安装")
    except ImportError:
        issues.append("openai-whisper 未安装。安装命令: pip3 install openai-whisper")
    if issues:
        print("\n❌ 以下依赖缺失:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    return True


def download_audio(url, output_path):
    print(f"\n📥 下载音频: {url}")
    print("  尝试提取内置字幕...")
    cmd_sub = [
        "yt-dlp", "--write-subs", "--write-auto-subs",
        "--sub-lang", "zh,zh-Hans,zh-CN",
        "--skip-download", "--sub-format", "json3",
        "-o", os.path.join(output_path, "%(id)s"), url
    ]
    result = subprocess.run(cmd_sub, capture_output=True, text=True)
    for f in os.listdir(output_path):
        if f.endswith(('.json3', '.srt', '.vtt', '.txt')):
            subtitle_path = os.path.join(output_path, f)
            print(f"  ✅ 找到内置字幕: {f}")
            text = extract_text_from_subtitle(subtitle_path)
            if text and len(text) > 50:
                print(f"  📝 字幕内容({len(text)}字), 无需语音识别")
                return text, None
            else:
                print(f"  ⚠️ 字幕内容太少({len(text)}字), 改用语音识别")
    print("  未找到可用字幕，下载音频进行语音识别...")
    audio_template = os.path.join(output_path, "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
        "-o", audio_template, "--no-playlist", url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ❌ 下载失败: {result.stderr}")
        return None, None
    audio_file = None
    for f in os.listdir(output_path):
        if f.endswith(('.mp3', '.m4a', '.webm', '.opus')):
            audio_file = os.path.join(output_path, f)
            break
    if not audio_file:
        print("  ❌ 未找到下载的音频文件")
        return None, None
    print(f"  ✅ 音频下载完成: {os.path.basename(audio_file)}")
    return None, audio_file


def extract_text_from_subtitle(subtitle_path):
    text = ""
    try:
        if subtitle_path.endswith('.json3'):
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for event in data.get('events', []):
                    if 'segs' in event:
                        for seg in event['segs']:
                            if 'utf8' in seg:
                                text += seg['utf8']
        elif subtitle_path.endswith('.srt'):
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.isdigit() and '-->' not in line:
                        text += line
        elif subtitle_path.endswith('.vtt'):
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('WEBVTT') and '-->' not in line and not line.isdigit():
                        text += line
        elif subtitle_path.endswith('.txt'):
            with open(subtitle_path, 'r', encoding='utf-8') as f:
                text = f.read()
    except Exception as e:
        print(f"  ⚠️ 字幕解析失败: {e}")
        return ""
    return text.strip()


def transcribe_audio(audio_path, model_name=WHISPER_MODEL):
    print(f"\n🎙️ 语音识别中（模型: {model_name}）...")
    print("  首次运行会下载模型，请耐心等待...")
    import whisper
    model = whisper.load_model(model_name)
    result = model.transcribe(audio_path, language="zh", verbose=False)
    text = result.get("text", "").strip()
    print(f"  ✅ 识别完成，共 {len(text)} 字")
    return text


def save_transcript(text, url, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"transcript_{timestamp}.txt"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"来源链接: {url}\n")
        f.write(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*50}\n\n")
        f.write(text)
    print(f"\n💾 逐字稿已保存: {filepath}")
    return filepath


def cleanup(temp_dir):
    try:
        for f in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, f)
            if os.path.isfile(filepath):
                os.remove(filepath)
        print("  🧹 临时文件已清理")
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        print("用法: python3 transcribe.py <抖音链接>")
        print("示例: python3 transcribe.py 'https://v.douyin.com/xxxxx'")
        sys.exit(1)
    url = sys.argv[1]
    print("=" * 50)
    print("🎬 抖音视频逐字稿生成工具")
    print("=" * 50)
    if not check_dependencies():
        sys.exit(1)
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    subtitle_text, audio_path = download_audio(url, TEMP_DIR)
    if subtitle_text:
        transcript = subtitle_text
    elif audio_path:
        transcript = transcribe_audio(audio_path)
    else:
        print("\n❌ 无法获取视频内容，请检查链接是否有效")
        sys.exit(1)
    if not transcript:
        print("\n❌ 逐字稿生成失败")
        sys.exit(1)
    filepath = save_transcript(transcript, url, OUTPUT_DIR)
    print("\n" + "=" * 50)
    print("📝 逐字稿内容:")
    print("=" * 50)
    print(transcript)
    print("=" * 50)
    cleanup(TEMP_DIR)
    print(f"\n✅ 完成！逐字稿文件: {filepath}")


if __name__ == "__main__":
    main()
