#!/usr/bin/env python3
"""
视频互动数据提取脚本 v8.1 — Safari/AppleScript 方案
通过 Safari 浏览器直接提取抖音视频互动数据，无需 API、无需 cookies

原理:
  - 用 AppleScript 控制 Safari 打开抖音搜索页
  - 从搜索结果 DOM 提取视频链接和播放量
  - 逐个打开视频详情页，提取点赞/评论/收藏/分享
  - 更新 Notion 选题库

前置条件:
  1. Safari → 开发菜单 → 勾选「允许通过 AppleEvents 进行 JavaScript 脚本编写」
  2. 终端有「完全磁盘访问」权限（系统设置 → 隐私与安全）

用法:
  python3 video-stats-filler.py --keyword "搜索关键词"
  python3 video-stats-filler.py --limit 20 --videos 3
  python3 video-stats-filler.py --diagnose
"""

import subprocess
import tempfile
import os
import time
import json
import re
import argparse
import urllib.request
import urllib.parse
import urllib.error

# ============ 配置区 ============
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
TOPIC_DB_ID = "3cfb48a1-123f-81a3-bd3e-ef018991ed28"

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

LOAD_TIMEOUT = 30
CONTENT_DELAY = 4
DELAY_BETWEEN_VIDEOS = 2
DELAY_BETWEEN_TOPICS = 2
DEFAULT_VIDEOS_PER_TOPIC = 5
# ================================


# ============ Safari 控制 ============

def run_safari_js(js_code):
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.js', delete=False, dir='/tmp', prefix='safari_'
    ) as f:
        f.write(js_code)
        js_path = f.name

    applescript = (
        f'set jsCode to read POSIX file "{js_path}"\n'
        f'tell application "Safari"\n'
        f'    return do JavaScript jsCode in front document\n'
        f'end tell'
    )
    try:
        result = subprocess.run(
            ['osascript', '-e', applescript],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "osascript 超时"
    finally:
        try:
            os.unlink(js_path)
        except OSError:
            pass


def open_safari_url(url):
    safe_url = url.replace('"', '%22')
    applescript = (
        f'tell application "Safari"\n'
        f'    set URL of front document to "{safe_url}"\n'
        f'end tell'
    )
    result = subprocess.run(
        ['osascript', '-e', applescript],
        capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0


def wait_for_page_load(timeout=30):
    js = "document.readyState"
    start = time.time()
    while time.time() - start < timeout:
        ok, output, _ = run_safari_js(js)
        if ok and "complete" in output:
            return True
        time.sleep(1)
    return False


def get_safari_url():
    applescript = 'tell application "Safari" to get URL of front document'
    result = subprocess.run(
        ['osascript', '-e', applescript],
        capture_output=True, text=True, timeout=5
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def scroll_safari_down():
    run_safari_js("window.scrollTo(0, document.body.scrollHeight); ''")


def check_safari_ready():
    ok, _, err = run_safari_js("'Safari_JS_OK'")
    if not ok:
        print("❌ 无法在 Safari 中执行 JavaScript")
        print("   请检查：")
        print("   1. Safari 已打开且有窗口")
        print("   2. Safari 菜单栏 → 开发 → 勾选「允许通过 AppleEvents 进行 JavaScript 脚本编写」")
        print(f"   错误: {err}")
        return False
    return True


# ============ 数据提取 JS ============

JS_SEARCH_EXTRACT = r"""
var result = [];
var cards = document.querySelectorAll('.search-result-card');
if (cards.length === 0) {
    var links = document.querySelectorAll('a[href*="/video/"]');
    var seen = {};
    for (var i = 0; i < links.length; i++) {
        var href = links[i].getAttribute('href');
        if (href && href.indexOf('/video/') > -1 && !seen[href]) {
            seen[href] = true;
            if (href.startsWith('//')) href = 'https:' + href;
            if (!href.startsWith('http')) href = 'https://www.douyin.com' + href;
            result.push({url: href, play_count: '', title: '', author: '', duration: ''});
        }
    }
} else {
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        var a = c.querySelector('a[href*="/video/"]');
        if (!a) continue;
        var href = a.getAttribute('href');
        if (!href) continue;
        if (href.startsWith('//')) href = 'https:' + href;
        if (!href.startsWith('http')) href = 'https://www.douyin.com' + href;

        var playEl = c.querySelector('[class*="cIiU4Muu"]');
        var titleEl = c.querySelector('[class*="VDYK8Xd7"]');
        var authorEl = c.querySelector('[class*="MZNczJmS"]');

        if (!playEl) {
            var spans = c.querySelectorAll('span');
            for (var j = 0; j < spans.length; j++) {
                if (spans[j].children.length === 0) {
                    var t = spans[j].innerText.trim();
                    if (/^\d+(\.\d+)?(万|亿)?$/.test(t) && t.length < 12) {
                        playEl = spans[j];
                        break;
                    }
                }
            }
        }

        result.push({
            url: href,
            play_count: playEl ? playEl.innerText.trim() : '',
            title: titleEl ? titleEl.innerText.trim() : '',
            author: authorEl ? authorEl.innerText.trim() : '',
            duration: ''
        });
    }
}
JSON.stringify(result);
"""

JS_DETAIL_EXTRACT = r"""
var result = {};

// === 策略1: 用诊断到的 class 名 ===
var likeEl = document.querySelector('[class*="Lr3l3ZEc"]');
var cmtEl  = document.querySelector('[class*="x6d7guxH"]');
var colEl  = document.querySelector('[class*="urITFwDq"]');
var shEl   = document.querySelector('[class*="mvwEat0w"]');

if (likeEl) result.likes = likeEl.innerText.trim();
if (cmtEl)  result.comments = cmtEl.innerText.trim();
if (colEl)  result.collects = colEl.innerText.trim();
if (shEl)   result.shares = shEl.innerText.trim();

var playEls = document.querySelectorAll('[class*="K46WC3Bh"]');
if (playEls.length > 0) {
    var nums = [];
    for (var i = 0; i < playEls.length; i++) {
        var t = playEls[i].innerText.trim();
        if (t) nums.push(t);
    }
    result.play_counts = nums;
}

var timeEl = document.querySelector('[class*="AxYDNgtW"]');
if (timeEl) result.publish_time = timeEl.innerText.trim().replace(/发布时间[：:]\s*/g, '');

var titleEl = document.querySelector('title');
if (titleEl) result.title = titleEl.innerText.replace(' - 抖音', '').trim();

// === 策略2: 如果策略1失败，用通用位置法 ===
if (!result.likes) {
    var counts = [];
    var allEls = document.querySelectorAll('div, span');
    for (var i = 0; i < allEls.length; i++) {
        var el = allEls[i];
        if (el.children.length === 0) {
            var t = (el.innerText || '').trim();
            if (t.length > 0 && t.length < 20 && /^\d+(\.\d+)?(万|亿)?$/.test(t)) {
                var rect = el.getBoundingClientRect();
                if (rect.y > 50 && rect.y < 900 && rect.x < 1200) {
                    counts.push({
                        text: t,
                        y: Math.round(rect.y),
                        x: Math.round(rect.x),
                        cls: (el.className || '').toString().substring(0, 30)
                    });
                }
            }
        }
    }
    var filtered = counts.filter(function(c) {
        return !/^\d{2}:\d{2}$/.test(c.text)
            && !/^\d+(\.\d+)?x$/.test(c.text)
            && c.text !== '50'
            && !/P$/.test(c.text);
    });
    filtered.sort(function(a, b) { return a.y - b.y || a.x - b.x; });
    if (filtered.length >= 4) {
        result.likes = filtered[0].text;
        result.comments = filtered[1].text;
        result.collects = filtered[2].text;
        result.shares = filtered[3].text;
        result._fallback = true;
    }
}

JSON.stringify(result);
"""

JS_DIAGNOSE = r"""
var r = [];
document.querySelectorAll('span,div,button,p,a,li').forEach(function(el) {
    if (el.children.length === 0) {
        var t = (el.innerText || el.textContent || '').trim();
        if (t.length > 0 && t.length < 80 && /[\d]/.test(t)) {
            r.push(el.tagName + '[' + (el.className || '').toString().substring(0, 40) + ']=>' + t);
        }
    }
});
r.join('\n');
"""


# ============ 数据解析 ============

def parse_count(text):
    if not text:
        return 0
    text = str(text).strip()
    try:
        if '亿' in text:
            return int(float(text.replace('亿', '')) * 100_000_000)
        elif '万' in text:
            return int(float(text.replace('万', '')) * 10_000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def determine_zan_level(ratio):
    """赞赞比 = 播放量/点赞数，越低越好"""
    if ratio <= 0:
        return None
    if ratio <= 10:
        return "S级-≤10"
    elif ratio <= 20:
        return "A级-≤20"
    elif ratio <= 50:
        return "B级-≤50"
    elif ratio <= 100:
        return "C级-≤100"
    else:
        return "D级->100"


# ============ 搜索与提取 ============

def extract_search_results(keyword, max_results=10):
    encoded = urllib.parse.quote(keyword)
    search_url = f"https://www.douyin.com/search/{encoded}?type=video"

    print(f"  🔍 搜索: {keyword}")
    print(f"  📎 URL: {search_url}")

    if not open_safari_url(search_url):
        print("  ❌ 无法在 Safari 中打开搜索页")
        return []

    print("  ⏳ 等待页面加载...")
    wait_for_page_load(LOAD_TIMEOUT)
    time.sleep(CONTENT_DELAY)

    scroll_safari_down()
    time.sleep(3)
    scroll_safari_down()
    time.sleep(2)

    ok, output, err = run_safari_js(JS_SEARCH_EXTRACT)
    if not ok:
        print(f"  ❌ 提取搜索结果失败: {err}")
        return []

    results = []
    try:
        data = json.loads(output)
        for item in data:
            results.append(item)
            if len(results) >= max_results:
                break
    except json.JSONDecodeError:
        print(f"  ❌ 解析搜索结果失败: {output[:200]}")
        return []

    print(f"  ✅ 找到 {len(results)} 个视频")
    return results


def extract_video_details():
    ok, output, err = run_safari_js(JS_DETAIL_EXTRACT)
    if not ok:
        print(f"    ❌ 提取详情失败: {err}")
        return None

    try:
        data = json.loads(output)
        return data
    except json.JSONDecodeError:
        print(f"    ❌ 解析详情JSON失败: {output[:200]}")
        return None


def process_keyword(keyword, max_videos=5, notion_page_id=None):
    print(f"\n{'='*60}")
    print(f"📋 关键词: {keyword}")
    print(f"{'='*60}")

    videos = extract_search_results(keyword, max_videos)
    if not videos:
        print("  ⚠️ 未找到视频")
        return []

    all_stats = []
    for i, v in enumerate(videos):
        title_short = v.get('title', '')[:40] or v['url']
        print(f"\n  📹 视频 {i+1}/{len(videos)}: {title_short}")
        print(f"     URL: {v['url']}")
        print(f"     播放量(搜索页): {v.get('play_count', 'N/A')}")

        if not open_safari_url(v['url']):
            print("     ⚠️ 无法打开详情页")
            continue

        wait_for_page_load(LOAD_TIMEOUT)
        time.sleep(CONTENT_DELAY)

        details = extract_video_details()
        if not details:
            print("     ⚠️ 无法提取详情数据")
            continue

        play_count = 0
        if 'play_counts' in details and details['play_counts']:
            for pc in details['play_counts']:
                val = parse_count(pc)
                if val > play_count:
                    play_count = val
        if play_count == 0 and v.get('play_count'):
            play_count = parse_count(v['play_count'])

        likes = parse_count(details.get('likes', '0'))
        comments = parse_count(details.get('comments', '0'))
        collects = parse_count(details.get('collects', '0'))
        shares = parse_count(details.get('shares', '0'))

        zan_ratio = round(play_count / likes, 1) if likes > 0 else 0
        zan_level = determine_zan_level(zan_ratio)
        collect_like_ratio = round(collects / likes * 100, 1) if likes > 0 else 0

        stats = {
            'video_url': v['url'],
            'play_count': play_count,
            'like_count': likes,
            'comment_count': comments,
            'collect_count': collects,
            'share_count': shares,
            'collect_like_ratio': collect_like_ratio,
            'zan_zan_bi': zan_ratio,
            'zan_level': zan_level,
            'uploader': v.get('author', ''),
            'video_desc': v.get('title', '')[:100],
            'publish_time': details.get('publish_time', ''),
        }
        if details.get('_fallback'):
            stats['_fallback'] = True

        all_stats.append(stats)

        fb = " [备选定位]" if details.get('_fallback') else ""
        print(f"     ✅ 播放:{play_count} 赞:{likes} 评:{comments} 收藏:{collects} 分享:{shares}{fb}")
        print(f"     📊 赞赞比:{zan_ratio} 等级:{zan_level or 'N/A'}")
        if details.get('publish_time'):
            print(f"     📅 发布:{details['publish_time']}")

        time.sleep(DELAY_BETWEEN_VIDEOS)

    if notion_page_id and all_stats:
        best = max(all_stats, key=lambda x: x['play_count'])
        print(f"\n  📝 更新 Notion (播放量最高: {best['play_count']})")
        if update_notion_video_stats(notion_page_id, best):
            print("  ✅ Notion 更新成功")
        else:
            print("  ❌ Notion 更新失败")

    return all_stats


# ============ Notion 操作 ============

def notion_request(method, path, data=None):
    url = f"{NOTION_API}{path}"
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=NOTION_HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8')[:300]
        print(f"  ❌ Notion API {e.code}: {err_body}")
        return None
    except Exception as e:
        print(f"  ❌ Notion 请求失败: {e}")
        return None


def fetch_topics_without_video_stats(limit=50, platform=None):
    conditions = [
        {"property": "点赞数", "number": {"is_empty": True}},
        {"property": "选题状态", "select": {"equals": "待审核"}},
    ]

    if platform:
        conditions.append({"property": "来源平台", "select": {"equals": platform}})

    query = {
        "page_size": min(limit, 100),
        "filter": {"and": conditions},
        "sorts": [{"property": "采集时间", "direction": "descending"}]
    }

    result = notion_request("POST", f"/databases/{TOPIC_DB_ID}/query", query)
    if not result:
        return []

    topics = []
    for page in result.get('results', []):
        props = page.get('properties', {})

        title_parts = props.get('选题标题', {}).get('title', [])
        title = ''.join([t['plain_text'] for t in title_parts])

        summary_parts = props.get('选题概括', {}).get('rich_text', [])
        summary_text = summary_parts[0]['plain_text'] if summary_parts else ''
        original_hot = ''
        if '【原始热搜】' in summary_text:
            original_hot = summary_text.split('【原始热搜】')[1].split('\n')[0].strip()

        platform_prop = props.get('来源平台', {}).get('select', {})
        platform_name = platform_prop.get('name', '') if platform_prop else ''

        source_url = props.get('来源链接', {}).get('url') or ''

        if title and original_hot and platform_name:
            topics.append({
                'page_id': page['id'],
                'title': title,
                'original_hot': original_hot,
                'platform': platform_name,
                'source_url': source_url,
            })

    return topics


def update_notion_video_stats(page_id, stats):
    props = {}

    if stats.get('like_count') is not None and stats.get('like_count') > 0:
        props["点赞数"] = {"number": stats['like_count']}
    if stats.get('comment_count') is not None and stats.get('comment_count') > 0:
        props["评论数"] = {"number": stats['comment_count']}
    if stats.get('collect_count') is not None and stats.get('collect_count') > 0:
        props["收藏数"] = {"number": stats['collect_count']}
    if stats.get('share_count') is not None and stats.get('share_count') > 0:
        props["分享数"] = {"number": stats['share_count']}
    if stats.get('collect_like_ratio') is not None and stats.get('collect_like_ratio') > 0:
        props["收藏点赞比"] = {"number": stats['collect_like_ratio']}
    if stats.get('zan_zan_bi') is not None and stats.get('zan_zan_bi') > 0:
        props["赞赞比"] = {"number": stats['zan_zan_bi']}
    if stats.get('zan_level'):
        props["赞赞比等级"] = {"select": {"name": stats['zan_level']}}
    if stats.get('uploader'):
        props["原始作者"] = {"rich_text": [{"text": {"content": stats['uploader'][:2000]}}]}
    if stats.get('video_url'):
        props["来源链接"] = {"url": stats['video_url']}

    review_parts = []
    if stats.get('video_url'):
        review_parts.append(f"【参考视频】{stats['video_url']}")
    if stats.get('uploader'):
        review_parts.append(f"【视频作者】{stats['uploader']}")
    if stats.get('video_desc'):
        review_parts.append(f"【视频描述】{stats['video_desc']}")
    if stats.get('play_count') and stats.get('play_count') > 0:
        review_parts.append(f"【播放量】{stats['play_count']}")
    if stats.get('publish_time'):
        review_parts.append(f"【发布时间】{stats['publish_time']}")
    if stats.get('_fallback'):
        review_parts.append("【注意】互动数据通过备选定位法提取，可能不准确")
    if review_parts:
        props["审核备注"] = {"rich_text": [{"text": {"content": "\n".join(review_parts)[:2000]}}]}

    if not props:
        return False

    result = notion_request("PATCH", f"/pages/{page_id}", {"properties": props})
    return result is not None


# ============ 诊断模式 ============

def run_diagnose():
    print("🔬 DOM 诊断模式")
    print(f"   当前 URL: {get_safari_url()}")
    print(f"   时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    ok, output, err = run_safari_js(JS_DIAGNOSE)
    if not ok:
        print(f"❌ 执行失败: {err}")
        return

    print("含数字的叶子节点：")
    print("-" * 60)
    print(output)
    print("-" * 60)
    print(f"\n共 {len(output.strip().split(chr(10)))} 个节点")


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(
        description="抖音视频互动数据提取 v8.1 (Safari/AppleScript)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --keyword "火象星座"                    # 搜索关键词（默认5个视频）
  %(prog)s --keyword "火象星座" --videos 3        # 只看前3个视频
  %(prog)s --limit 10                              # 补全10条选题
  %(prog)s --limit 20 --videos 3                   # 补全20条选题，每条只看3个视频
  %(prog)s --diagnose                              # 诊断当前页面 DOM
        """
    )
    parser.add_argument('--keyword', '-k', help='直接搜索关键词')
    parser.add_argument('--limit', '-n', type=int, default=5, help='处理多少条选题 (默认5)')
    parser.add_argument('--videos', '-v', type=int, default=DEFAULT_VIDEOS_PER_TOPIC,
                        help=f'每个选题提取多少个视频 (默认{DEFAULT_VIDEOS_PER_TOPIC})')
    parser.add_argument('--platform', '-p', choices=['douyin', 'weibo'], help='只处理指定平台')
    parser.add_argument('--no-notion', action='store_true', help='只搜索不更新 Notion')
    parser.add_argument('--diagnose', action='store_true', help='诊断当前页面 DOM 结构')
    args = parser.parse_args()

    print("=" * 60)
    print("🎬 视频互动数据提取 v8.1 (Safari/AppleScript)")
    print(f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if args.diagnose:
        run_diagnose()
        return

    if not check_safari_ready():
        return

    if args.keyword:
        print(f"\n🔍 关键词模式: {args.keyword} (提取 {args.videos} 个视频)")
        stats = process_keyword(args.keyword, args.videos)

        print(f"\n{'='*60}")
        print(f"📊 汇总: 共提取 {len(stats)} 个视频")
        print("-" * 60)
        for s in stats:
            fb = " [备选]" if s.get('_fallback') else ""
            print(f"  {s['video_desc'][:30]}... | 播放:{s['play_count']} 赞:{s['like_count']} 评:{s['comment_count']}{fb}")
        print(f"{'='*60}")
        return

    if not NOTION_TOKEN:
        print("❌ 未设置 NOTION_TOKEN，无法读取或更新 Notion 选题库")
        return

    platform_map = {"douyin": "抖音", "weibo": "微博"}
    platform_filter = platform_map.get(args.platform)

    print(f"\n📥 从 Notion 读取待处理选题...")
    topics = fetch_topics_without_video_stats(args.limit if not args.no_notion else 100, platform_filter)

    if not topics:
        print("✅ 没有需要补全的选题！")
        return

    dy_count = sum(1 for t in topics if t['platform'] == '抖音')
    wb_count = sum(1 for t in topics if t['platform'] == '微博')
    print(f"  找到 {len(topics)} 条（抖音 {dy_count} 条, 微博 {wb_count} 条）")
    print(f"  每条选题提取 {args.videos} 个视频")

    est_per_topic = 15 + args.videos * 10 + 2
    est_total = dy_count * est_per_topic
    print(f"  ⏱️  预计耗时: 约 {est_total//60} 分钟 ({dy_count} 条抖音选题)")

    if args.no_notion:
        print("  ⚠️ --no-notion 模式：只搜索不更新 Notion")

    success = 0
    no_result = 0
    fail = 0

    for i, topic in enumerate(topics, 1):
        keyword = topic['original_hot']
        platform = topic['platform']
        print(f"\n[{i}/{len(topics)}] {platform}「{keyword[:25]}」")

        try:
            if platform == '抖音':
                stats = process_keyword(
                    keyword, args.videos,
                    notion_page_id=None if args.no_notion else topic['page_id']
                )
                if stats:
                    success += 1
                else:
                    no_result += 1
            else:
                print(f"  ⏭️ 微博暂不支持 Safari 提取，跳过")
                no_result += 1
                continue

        except Exception as e:
            print(f"  ❌ 异常: {str(e)[:80]}")
            fail += 1

        time.sleep(DELAY_BETWEEN_TOPICS)

    print(f"\n{'='*60}")
    print(f"📊 补全结果汇总")
    print(f"  总计: {len(topics)} 条")
    print(f"  成功: {success} 条")
    print(f"  无结果: {no_result} 条")
    print(f"  失败: {fail} 条")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
