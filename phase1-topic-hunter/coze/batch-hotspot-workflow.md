# 批量采集热榜 · Coze 工作流搭建手册

## 概述
创建一个定时运行的工作流，自动抓取抖音热榜 + 微博热搜，AI 初筛出有爆款潜力的选题，批量写入 Notion 选题库。

**运行频率：** 每天 2 次（10:00 / 22:00，北京时间）
**处理量：** 每次约 100 条 → AI 筛选出 10-15 条 → 写入 Notion

## 工作流节点总览

[1] 定时触发
    ↓
[2] HTTP请求 - 抓抖音热榜 ─┐
                             ├→ [4] 代码节点 - 合并数据
[3] HTTP请求 - 抓微博热搜 ─┘
    ↓
[5] LLM节点 - AI筛选与评分（DeepSeek）
    ↓
[6] 代码节点 - 格式化输出
    ↓
[7] Notion节点 - 批量写入选题库
    ↓
[结束]

## 详细步骤

### 第1步：创建工作流
1. Coze 左侧边栏 → 工作流 → + 创建工作流
2. 名称：批量热榜采集
3. 描述：每天自动抓取抖音+微博热榜，AI筛选后写入Notion选题库
4. 选择空白工作流开始

### 第2步：定时触发节点（节点1）
- 触发方式：Cron 表达式
- Cron：0 10,22 * * *
- 时区：Asia/Shanghai

### 第3步：HTTP 请求 - 抖音热榜（节点2）
- 方法：GET
- URL：https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/
- Headers：
  User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15
  Referer: https://www.douyin.com/
- 超时：15秒
- 输出变量：douyin_data

### 第4步：HTTP 请求 - 微博热搜（节点3）
- 方法：GET
- URL：https://weibo.com/ajax/statuses/hot_band
- Headers：
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0
  Referer: https://weibo.com/
- 超时：15秒
- 输出变量：weibo_data

返回结构以已运行的 `hot-collector.py` 为准：热搜列表在
`weibo_data.data.band_list`。每项使用 `note`（为空时回退 `word`）作为热搜词，
`num` 作为热度；跳过 `is_ad` 或 `ad_type` 标记的广告项。

注意：节点2和节点3是并行的。

### 第5步：代码节点 - 合并数据（节点4）
输入变量：douyin_data, weibo_data
Python 代码：

import json

def main(douyin_data, weibo_data):
    items = []
    try:
        dy_list = douyin_data.get('word_list', [])
        for item in dy_list:
            items.append({
                'platform': '抖音',
                'title': item.get('word', ''),
                'hot_value': item.get('hot_value', 0),
                'rank': item.get('position', 0),
                'url': f"https://www.douyin.com/search/{item.get('word', '')}"
            })
    except Exception as e:
        print(f"抖音数据解析失败: {e}")
    try:
        wb_list = weibo_data.get('data', {}).get('band_list', [])
        for item in wb_list:
            if item.get('is_ad') or item.get('ad_type'):
                continue
            word = item.get('note', '') or item.get('word', '')
            if not word:
                continue
            items.append({
                'platform': '微博',
                'title': word,
                'hot_value': item.get('num', 0),
                'rank': item.get('rank', item.get('position', 0)),
                'url': f"https://s.weibo.com/weibo?q=%23{word}%23"
            })
    except Exception as e:
        print(f"微博数据解析失败: {e}")
    items.sort(key=lambda x: x['hot_value'], reverse=True)
    top_items = items[:60]
    return {
        'total_count': len(items),
        'filtered_count': len(top_items),
        'hot_items': top_items
    }

输出变量：merged_hotlist

### 第6步：LLM 节点 - AI筛选与评分（节点5）
- 模型：DeepSeek
- 输入变量：merged_hotlist
- System Prompt：见下方完整 Prompt
- User Message：以下是今天的热榜数据（共{{merged_hotlist.filtered_count}}条）...
- 输出变量：ai_filtered_topics

### 第7步：代码节点 - 格式化输出（节点6）
输入变量：ai_filtered_topics
功能：解析 LLM 输出为 JSON，按综合分排序，添加批次时间
输出变量：formatted_topics

### 第8步：Notion 写入节点（节点7）
推荐用 HTTP 请求 + 循环方式：
- 循环遍历 formatted_topics.topics
- POST https://api.notion.com/v1/pages
- Headers: Authorization, Content-Type, Notion-Version
- Body: parent + properties（字段映射见下方）

字段映射：
- 选题标题 ← loop_item.title
- 所属账号 ← loop_item.account
- 来源平台 ← loop_item.platform
- 热度评分 ← loop_item.heat_score
- 痛点精准度 ← loop_item.pain_score
- 知识关联度 ← loop_item.knowledge_score
- 综合评分 ← 不传。该字段为 Notion Formula，会根据热度评分、知识关联度、痛点精准度自动计算。
- 赞赞比等级 ← loop_item.zan_zan_ratio_level
- 选题状态 ← "待审核"
- 采集时间 ← 不传。该字段使用 Notion `created_time`，由创建页面时自动生成；如未来确需记录外部批次时间，另建普通 `date` 字段（例如“采集批次时间”），不要改写 `created_time`。

## 测试与发布
1. 测试：点击运行，检查每个节点输出
2. 验证：Notion 选题库是否有新条目
3. 发布：保存并启用定时触发
