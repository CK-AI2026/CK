#!/usr/bin/env python3
"""
批量热榜采集器 v3
抓取抖音热榜 + 微博热搜 → DeepSeek AI筛选(含爆点预判) → 写入Notion选题库
用法: python3 hot-collector.py

依赖:
  必需: 无额外依赖（标准库即可运行基础采集）
  可选: pip install f2 browser-cookie3 httpx （视频互动数据模块已废弃，见 video-stats-filler.py）
"""

import json
import os
import sys
import time
import re
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ============ 可选依赖：视频互动数据抓取（已废弃，保留用于兼容）============
try:
    from f2.apps.douyin.utils import TokenManager as DyTokenManager, ABogusManager
    from f2.apps.douyin.api import DouyinAPIEndpoints
    import browser_cookie3
    import httpx
    VIDEO_STATS_ENABLED = True
except ImportError:
    VIDEO_STATS_ENABLED = False

# ============ 配置区 ============
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
TOPIC_DB_ID = "3cfb48a1-123f-81a3-bd3e-ef018991ed28"
KNOWLEDGE_DB_ID = "3cfb48a1-123f-8114-9b36-c67bbe85e950"

# DeepSeek API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 采集数量配置
DOUYIN_TOP_N = 50
WEIBO_TOP_N = 50
MAX_SELECTED = 15
SEARCH_TOP_VIDEOS = 5
# ================================

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def make_search_url(platform, keyword):
    encoded = urllib.parse.quote(keyword)
    if platform == "抖音":
        return f"https://www.douyin.com/search/{encoded}"
    elif platform == "微博":
        return f"https://s.weibo.com/weibo?q={encoded}"
    return ""


def calc_priority(composite):
    if composite >= 80:
        return "🔥 紧急制作"
    elif composite >= 70:
        return "⭐ 高优先"
    elif composite >= 60:
        return "📌 中优先"
    else:
        return "📝 低优先"


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


def fetch_douyin_hot():
    print("  📱 抓取抖音热榜...")
    headers = {"User-Agent": UA}
    try:
        req = urllib.request.Request(
            "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/",
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            items = []
            for item in data.get('word_list', [])[:DOUYIN_TOP_N]:
                word = item.get('word', '')
                items.append({
                    "platform": "抖音",
                    "title": word,
                    "hot": item.get('hot_value', 0),
                    "source_url": make_search_url("抖音", word)
                })
            print(f"  ✅ 抖音热榜: {len(items)} 条")
            return items
    except Exception as e:
        print(f"  ❌ 抖音热榜失败: {e}")
        return []


def fetch_weibo_hot():
    print("  📱 抓取微博热搜...")
    headers = {"User-Agent": UA, "Referer": "https://weibo.com/"}
    try:
        req = urllib.request.Request(
            "https://weibo.com/ajax/statuses/hot_band",
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            items = []
            for item in data.get('data', {}).get('band_list', [])[:WEIBO_TOP_N]:
                if item.get('is_ad') or item.get('ad_type'):
                    continue
                word = item.get('note', '') or item.get('word', '')
                items.append({
                    "platform": "微博",
                    "title": word,
                    "hot": item.get('num', 0),
                    "category": item.get('category', ''),
                    "label": item.get('label_name', '') or item.get('icon_desc', ''),
                    "source_url": make_search_url("微博", word)
                })
            print(f"  ✅ 微博热搜: {len(items)} 条")
            return items
    except Exception as e:
        print(f"  ❌ 微博热搜失败: {e}")
        return []


def ai_filter(hot_items):
    if not DEEPSEEK_API_KEY:
        print("  ⚠️ 未设置 DEEPSEEK_API_KEY，跳过AI筛选")
        return hot_items[:MAX_SELECTED]

    print(f"  🤖 DeepSeek 筛选中（共{len(hot_items)}条）...")

    items_text = ""
    for i, item in enumerate(hot_items):
        cat = item.get('category', '')
        label = item.get('label', '')
        extra = f" [分类:{cat}]" if cat else ""
        extra += f" [标签:{label}]" if label and isinstance(label, str) else ""
        items_text += f"{i+1}. [{item['platform']}] {item['title']} (热度:{item.get('hot',0)}){extra}\n"

    prompt = f"""你是短视频选题分析专家。以下是从抖音和微博采集的{len(hot_items)}条热点：

{items_text}

请筛选出最多{MAX_SELECTED}条最适合做短视频的选题。

筛选标准（按优先级）：
1. 话题本身有情绪共鸣点（能让人"对对对就是说我"）
2. 和以下领域相关：人文社科、心理学、社会学、情感关系、星座、职场
3. 能引发讨论和争议（不是纯新闻）
4. 避开：纯政治新闻、灾难、负面社会事件

两个账号：
- 账号A-人文社科：爆款公式=现象→深度知识→案例→共鸣→方案
- 账号B-星座情感：爆款公式=星座→情绪认同→性格解析→心理建议

【标题改写要求——非常重要】
每条选题的标题必须改写成适合做短视频的标题，不要直接用热搜原话。
必须使用多样化的句式，严禁多条选题使用相同或相似的句式开头。
请混合使用以下标题公式（每条选题只选一种，不要重复）：

标题公式库（轮流使用，确保多样）：
- 反问式：「XX真的靠谱吗？」「XX到底意味着什么？」
- 数字式：「90%的人都不知道的XX」「3个信号说明XX」
- 冲突式：「越XX的人越容易XX」「表面XX，实则XX」
- 共鸣式：「原来不止我一个人XX」「终于有人说清楚了XX」
- 悬念式：「XX背后的真相」「XX之后会发生什么」
- 对比式：「XX和XX的区别到底在哪」「别人XX，你却XX」
- 结论式：「XX才是普通人最大的XX」「XX决定了你的XX」
- 故事式：「那个XX的人后来怎样了」「从XX到XX需要几步」
- 星座式（账号B专用）：「这个星座最容易XX」「XX座的人都有个通病」
- 情绪式：「别再XX了」「承认吧，你就是XX」

每条标题必须用不同的公式，15条标题至少覆盖8种以上不同公式。

【爆点预判要求】
对每条选题，给出爆点预判：预测这条选题的爆款潜力和最佳切入角度。
格式：一句话说明爆点在哪 + 建议的切入角度。

【审核备注要求】
对每条选题，给出审核建议：告诉审核人这条选题的注意事项。

请严格按以下JSON格式返回，不要有任何其他文字：
{{
  "topics": [
    {{
      "title": "改写后的短视频标题",
      "original_hot": "原始热搜词条",
      "platform": "来源平台",
      "heat_score": 0,
      "pain_score": 0,
      "knowledge_score": 0,
      "account": "账号A-人文社科",
      "reason": "一句话说明为什么选这条",
      "viral_prediction": "爆点预判",
      "review_note": "审核备注",
      "tags": ["标签1", "标签2"]
    }}
  ]
}}"""

    data = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是短视频选题专家。你擅长把热搜改写成有网感、有悬念、句式多样的短视频标题，并给出精准的爆点预判。只返回JSON格式数据。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.5,
        "response_format": {"type": "json_object"}
    }).encode('utf-8')

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        req = urllib.request.Request(DEEPSEEK_API, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']

            try:
                selected = json.loads(content)
                if isinstance(selected, dict):
                    for key in ['topics', 'data', 'results', 'items']:
                        if key in selected:
                            selected = selected[key]
                            break
                    if isinstance(selected, dict):
                        selected = [selected]
                if not isinstance(selected, list):
                    selected = list(selected.values()) if isinstance(selected, dict) else []
            except:
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    selected = json.loads(match.group())
                else:
                    print("  ⚠️ AI返回格式异常，使用原始数据")
                    return hot_items[:MAX_SELECTED]

            for item in selected:
                original = item.get('original_hot', '')
                platform = item.get('platform', '')
                if original and platform:
                    item['source_url'] = make_search_url(platform, original)

            print(f"  ✅ AI筛选出 {len(selected)} 条选题")
            return selected
    except Exception as e:
        print(f"  ❌ DeepSeek API失败: {e}")
        return hot_items[:MAX_SELECTED]


VALID_ACCOUNTS = {"账号A-人文社科", "账号B-星座情感"}
VALID_TAGS = {
    "职场", "情感", "心理", "人际关系", "自我成长", "焦虑", "孤独", "熬夜", "拖延", "社交恐惧",
    "原生家庭", "认知升级", "哲学", "社会学", "心理学", "恋爱", "婚姻", "分手", "星座",
    "白羊座", "金牛座", "双子座", "巨蟹座", "狮子座", "处女座", "天秤座", "天蝎座",
    "射手座", "摩羯座", "水瓶座", "双鱼座", "上升星座", "月亮星座", "水逆", "复合",
    "脱单", "人文",
}


def clamp_score(value):
    """将 LLM 返回的评分安全规范为 0-100 的数值。"""
    try:
        return max(0, min(100, float(value)))
    except (TypeError, ValueError):
        return 0


def validate_ai_topics(topics):
    """校验并规范化 AI 结果；异常条目跳过，不影响整批入库。"""
    valid_topics = []
    for index, item in enumerate(topics, 1):
        if not isinstance(item, dict):
            print(f"  ⚠️ 跳过第 {index} 条：AI 返回的条目不是对象")
            continue

        title = str(item.get("title", "")).strip()
        if not title:
            print(f"  ⚠️ 跳过第 {index} 条：选题标题为空")
            continue
        item["title"] = title[:2000]

        for field in ("heat_score", "pain_score", "knowledge_score"):
            item[field] = clamp_score(item.get(field, 0))

        if item.get("account") not in VALID_ACCOUNTS:
            print(f"  ⚠️ 第 {index} 条账号无效，已改为默认账号A：{item.get('account', '')}")
            item["account"] = "账号A-人文社科"

        raw_tags = item.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        if not isinstance(raw_tags, list):
            raw_tags = []
        item["tags"] = list(dict.fromkeys(
            tag for tag in raw_tags if isinstance(tag, str) and tag in VALID_TAGS
        ))

        item["original_hot"] = str(item.get("original_hot", "")).strip()
        item["platform"] = str(item.get("platform", "")).strip()
        valid_topics.append(item)
    return valid_topics


# ============ 视频互动数据抓取（旧版/已废弃）============
# 新方案见 video-stats-filler.py（Safari/AppleScript 方案）
# 以下代码保留仅用于兼容，云端环境无浏览器cookies时会跳过

def get_safari_cookies(domain):
    try:
        cookies = browser_cookie3.safari(domain_name=domain)
        return '; '.join([f'{c.name}={c.value}' for c in cookies])
    except Exception as e:
        return None


def fetch_douyin_video_stats(keyword):
    """旧版API方案，已废弃。请使用 video-stats-filler.py"""
    if not VIDEO_STATS_ENABLED:
        return None
    cookie_str = get_safari_cookies('douyin.com')
    if not cookie_str:
        return None
    # ... 旧实现省略，因已废弃
    return None


def fetch_video_stats(item):
    keyword = item.get('original_hot', '') or item.get('title', '')
    platform = item.get('platform', '')
    if not keyword:
        return None
    if platform == "抖音":
        return fetch_douyin_video_stats(keyword)
    return None


# ============ 知识库关联 ============

def fetch_knowledge_base():
    try:
        req = urllib.request.Request(
            f"{NOTION_API}/databases/{KNOWLEDGE_DB_ID}/query",
            data=json.dumps({"page_size": 100}).encode('utf-8'),
            headers=NOTION_HEADERS,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            entries = []
            for page in data.get('results', []):
                props = page.get('properties', {})
                title_parts = props.get('知识标题', {}).get('title', [])
                title = ''.join([t['plain_text'] for t in title_parts])
                cat = props.get('知识分类', {}).get('select', {})
                category = cat.get('name', '') if cat else ''
                tags = [t['name'] for t in props.get('标签', {}).get('multi_select', [])]
                summary_parts = props.get('内容摘要', {}).get('rich_text', [])
                summary = summary_parts[0]['plain_text'] if summary_parts else ''
                entries.append({
                    'page_id': page['id'],
                    'title': title,
                    'category': category,
                    'tags': tags,
                    'summary': summary[:200],
                })
            return entries
    except Exception as e:
        print(f"  ❌ 知识库查询失败: {e}")
        return []


def ai_match_knowledge(topics, knowledge_entries):
    if not DEEPSEEK_API_KEY or not knowledge_entries:
        return {}

    kb_text = ""
    for i, entry in enumerate(knowledge_entries):
        kb_text += f"K{i+1}. [id:{entry['page_id']}] {entry['title']}"
        if entry['category']:
            kb_text += f" (分类:{entry['category']})"
        if entry['tags']:
            kb_text += f" (标签:{','.join(entry['tags'][:3])})"
        if entry['summary']:
            kb_text += f"\n   摘要: {entry['summary']}"
        kb_text += "\n"

    topics_text = ""
    for i, topic in enumerate(topics):
        title = topic.get('title', '')
        tags = topic.get('tags', [])
        reason = topic.get('reason', '')
        topics_text += f"T{i+1}. {title} (标签:{','.join(tags[:3])})\n   理由:{reason}\n"

    prompt = f"""你是知识关联专家。以下是选题库和知识库的内容：

【选题列表】
{topics_text}

【知识库列表】
{kb_text}

请判断每个选题最适合关联哪些知识库条目（最多关联2条）。
关联标准：
1. 选题内容能用该知识库条目的理论/方法论来解读
2. 选题的切入角度和知识库条目的知识分类匹配
3. 只有关联度较高才关联，没有合适的可以不关联

请严格按以下JSON格式返回，不要有任何其他文字：
{{
  "matches": [
    {{
      "topic_index": 1,
      "knowledge_ids": ["完整page_id1", "完整page_id2"]
    }}
  ]
}}

topic_index 从1开始，对应选题列表的序号。
knowledge_ids 使用完整的page_id。"""

    data = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你是知识管理专家，擅长发现选题与知识库之间的关联。只返回JSON格式数据。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }).encode('utf-8')

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        req = urllib.request.Request(DEEPSEEK_API, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            try:
                parsed = json.loads(content)
                matches = parsed.get('matches', parsed.get('data', []))
                if isinstance(matches, dict):
                    matches = [matches]
            except:
                return {}

            result_map = {}
            for m in matches:
                idx = m.get('topic_index', 0)
                kids = m.get('knowledge_ids', [])
                if idx and kids:
                    valid_ids = []
                    for kid in kids:
                        for entry in knowledge_entries:
                            if kid == entry['page_id'] or entry['page_id'].startswith(kid):
                                valid_ids.append(entry['page_id'])
                                break
                    if valid_ids:
                        result_map[idx] = valid_ids[:2]
            return result_map
    except Exception as e:
        print(f"  ❌ 知识关联AI匹配失败: {e}")
        return {}


# ============ Notion 写入 ============

def write_to_notion(item, video_stats=None, top_comments="", knowledge_ids=None):
    title = item.get('title', item.get('original_hot', '未知选题'))
    platform = item.get('platform', '')
    heat = item.get('heat_score', 0)
    pain = item.get('pain_score', 0)
    knowledge = item.get('knowledge_score', 0)
    account = item.get('account', '账号A-人文社科')
    reason = item.get('reason', '')
    tags = item.get('tags', [])
    original_hot = item.get('original_hot', '')
    viral_prediction = item.get('viral_prediction', '')
    review_note = item.get('review_note', '')

    source_url = item.get('source_url', '')
    video_url = ''
    if video_stats and video_stats.get('video_url'):
        video_url = video_stats['video_url']
        source_url = video_url

    composite = round(heat * 0.4 + knowledge * 0.3 + pain * 0.3)
    priority = calc_priority(composite)

    summary_parts = []
    if original_hot:
        summary_parts.append(f"【原始热搜】{original_hot}")
    if reason:
        summary_parts.append(f"【选题理由】{reason}")
    summary = "\n".join(summary_parts) if summary_parts else ""

    review_parts = []
    if review_note:
        review_parts.append(review_note)
    if video_url:
        review_parts.append(f"【参考视频】{video_url}")
        if video_stats and video_stats.get('uploader'):
            review_parts.append(f"【视频作者】{video_stats['uploader']}")
    review_text = "\n".join(review_parts) if review_parts else ""

    tag_options = [{"name": tag} for tag in tags[:5]]

    valid_platforms = ["抖音", "小红书", "微博", "今日头条", "知乎", "手动录入", "其他"]
    platform_name = platform if platform in valid_platforms else "抖音"
    valid_accounts = ["账号A-人文社科", "账号B-星座情感", "双账号通用"]
    account_name = account if account in valid_accounts else "账号A-人文社科"

    props = {
        "选题标题": {"title": [{"text": {"content": title[:2000]}}]},
        "来源平台": {"select": {"name": platform_name}},
        "热度评分": {"number": heat},
        "痛点精准度": {"number": pain},
        "知识关联度": {"number": knowledge},
        "优先级": {"select": {"name": priority}},
        "所属账号": {"select": {"name": account_name}},
        "选题状态": {"select": {"name": "待审核"}},
        "选题标签": {"multi_select": tag_options},
        "生成逐字稿": {"checkbox": False},
    }

    if source_url:
        props["来源链接"] = {"url": source_url}
    if summary:
        props["选题概括"] = {"rich_text": [{"text": {"content": summary[:2000]}}]}
    if viral_prediction:
        props["爆点预判"] = {"rich_text": [{"text": {"content": viral_prediction[:2000]}}]}
    if review_text:
        props["审核备注"] = {"rich_text": [{"text": {"content": review_text[:2000]}}]}

    if video_stats:
        if video_stats.get('like_count'):
            props["点赞数"] = {"number": video_stats['like_count']}
        if video_stats.get('comment_count'):
            props["评论数"] = {"number": video_stats['comment_count']}
        if video_stats.get('collect_count'):
            props["收藏数"] = {"number": video_stats['collect_count']}
        if video_stats.get('share_count'):
            props["分享数"] = {"number": video_stats['share_count']}
        if video_stats.get('collect_like_ratio'):
            props["收藏点赞比"] = {"number": video_stats['collect_like_ratio']}
        if video_stats.get('zan_zan_bi'):
            props["赞赞比"] = {"number": video_stats['zan_zan_bi']}
        if video_stats.get('zan_level'):
            props["赞赞比等级"] = {"select": {"name": video_stats['zan_level']}}
        if video_stats.get('uploader'):
            props["原始作者"] = {"rich_text": [{"text": {"content": video_stats['uploader'][:2000]}}]}

    if top_comments:
        props["高赞评论"] = {"rich_text": [{"text": {"content": top_comments[:2000]}}]}

    if knowledge_ids:
        props["关联知识"] = {"relation": [{"id": kid} for kid in knowledge_ids]}

    data = {
        "parent": {"database_id": TOPIC_DB_ID},
        "properties": props
    }

    try:
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(
            f"{NOTION_API}/pages",
            data=body,
            headers=NOTION_HEADERS,
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                return True
    except Exception as e:
        print(f"    ❌ 写入失败: {e}")
    return False


def check_existing(item):
    """按平台 + 原始热搜词 + 当天入库日期去重；标题匹配仅作历史兜底。"""
    title = str(item.get("title", "")).strip()
    platform = str(item.get("platform", "")).strip()
    original_hot = str(item.get("original_hot", "")).strip()

    if platform and original_hot:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today = now.date().isoformat()
        tomorrow = (now.date() + timedelta(days=1)).isoformat()
        try:
            data = json.dumps({
                "page_size": 100,
                "filter": {"and": [
                    {"property": "来源平台", "select": {"equals": platform}},
                    {"property": "采集时间", "created_time": {
                        "on_or_after": today,
                        "before": tomorrow,
                    }},
                ]},
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{NOTION_API}/databases/{TOPIC_DB_ID}/query",
                data=data, headers=NOTION_HEADERS, method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                for page in json.loads(resp.read().decode("utf-8")).get("results", []):
                    summary = "".join(
                        part.get("plain_text", "")
                        for part in page.get("properties", {}).get("选题概括", {}).get("rich_text", [])
                    )
                    match = re.search(r"【原始热搜】([^\\n]+)", summary)
                    if match and match.group(1).strip() == original_hot:
                        return True
        except Exception as e:
            print(f"  ⚠️ 原始热搜去重查询失败，将尝试标题兜底: {e}")

    if not title:
        return False
    try:
        data = json.dumps({
            "filter": {
                "property": "选题标题",
                "title": {"contains": title[:50]}
            }
        }).encode('utf-8')
        req = urllib.request.Request(
            f"{NOTION_API}/databases/{TOPIC_DB_ID}/query",
            data=data,
            headers=NOTION_HEADERS,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return len(result.get("results", [])) > 0
    except:
        return False


# ============ 主流程 ============

def main():
    if not NOTION_TOKEN:
        print("❌ 未设置 NOTION_TOKEN，停止运行以避免未授权写入")
        return
    print("=" * 60)
    print("🔥 批量热榜采集器 v3")
    print(f"   时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   DeepSeek: {'✅ 已配置' if DEEPSEEK_API_KEY else '❌ 未配置'}")
    print("=" * 60)

    print("\n📥 第1步：抓取热榜")
    douyin_items = fetch_douyin_hot()
    weibo_items = fetch_weibo_hot()
    all_items = douyin_items + weibo_items
    print(f"  总计: {len(all_items)} 条热点")
    if not all_items:
        print("\n❌ 没有抓到任何热点，退出")
        return

    print("\n🤖 第2步：AI筛选（含爆点预判 + 审核备注）")
    selected = validate_ai_topics(ai_filter(all_items))
    print(f"  筛选出 {len(selected)} 条选题")

    # 视频互动数据：云端无浏览器cookies，跳过
    # Mac本地请使用 video-stats-filler.py 补全
    print("\nℹ️ 第3步：视频互动数据（云端跳过，请在Mac本地用 video-stats-filler.py 补全）")

    print("\n📚 第4步：知识库关联匹配")
    knowledge_entries = fetch_knowledge_base()
    print(f"  知识库共 {len(knowledge_entries)} 条记录")
    if knowledge_entries and DEEPSEEK_API_KEY:
        knowledge_matches = ai_match_knowledge(selected, knowledge_entries)
        match_count = sum(1 for v in knowledge_matches.values() if v)
        print(f"  AI匹配完成: {match_count} 条选题关联了知识库")
    else:
        knowledge_matches = {}
        print("  跳过知识关联（无知识库或未配置API）")

    print("\n📤 第5步：写入Notion选题库")
    success = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(selected, 1):
        title = item.get('title', item.get('original_hot', f'选题{i}'))
        composite = round(
            item.get('heat_score', 0) * 0.4 +
            item.get('knowledge_score', 0) * 0.3 +
            item.get('pain_score', 0) * 0.3
        )
        priority = calc_priority(composite)
        knowledge_ids = knowledge_matches.get(i, [])

        if check_existing(item):
            print(f"  [{i}/{len(selected)}] ⏭️ 已存在，跳过: {title[:30]}")
            skipped += 1
            continue

        kb_info = f" 知识:{len(knowledge_ids)}条" if knowledge_ids else ""
        print(f"  [{i}/{len(selected)}] 写入: {title[:40]}... ({priority} 评分:{composite}{kb_info})")

        if write_to_notion(item, None, "", knowledge_ids):
            success += 1
            print(f"         ✅ 成功")
        else:
            failed += 1

        time.sleep(0.5)

    print("\n" + "=" * 60)
    print("📊 采集结果汇总")
    print(f"  抓取热点: {len(all_items)} 条")
    print(f"  AI筛选: {len(selected)} 条")
    kb_match_count = sum(1 for v in knowledge_matches.values() if v)
    print(f"  知识关联: {kb_match_count}/{len(selected)} 条")
    print(f"  成功写入: {success} 条")
    print(f"  重复跳过: {skipped} 条")
    print(f"  写入失败: {failed} 条")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
