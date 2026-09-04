# Coze 扣子工作流设计 · 选题猎手 Topic Hunter

## 工作流概览
**工作流名称**：选题猎手 · Topic Hunter
**触发方式**：定时触发（每天10:00/22:00）+ 手动触发 + 手机端分享触发
**输出**：结构化选题卡片，自动写入Notion选题库

## 工作流结构
开始 → 1.热点采集 → 2.内容筛选 → 3.赞赞比分析 → 4.选题打分 → 5.生成选题卡片 → 6.写入Notion → 结束

## 三个触发入口
- 入口A：定时批量采集（每天 10:00 / 22:00）
- 入口B：手动"找选题"（手机端Coze Bot）
- 入口C：单条分享入库（手机端分享链接到Coze Bot）

## 节点详解

### 节点0：账号Profile配置（起始变量）
输入：account_id, platforms, zan_zan_threshold, max_topics
数据来源：accounts.json

### 节点1：热点采集（插件节点）
数据源：抖音热点、小红书热点、微博热搜、今日头条热榜、知乎热榜

### 节点2：内容筛选（LLM节点）
Prompt：见 prompts/content-filter.md
输出：筛选后的内容列表（按相关度排序）

### 节点3：赞赞比分析（插件 + LLM节点）
3.1 评论抓取 → Top 20 评论
3.2 赞赞比计算（代码节点）
3.3 痛点精准度评估（LLM节点，Prompt: prompts/pain-point-analyzer.md）

### 节点4：选题打分（LLM + 代码节点）
4.1 热度评分（代码节点）
4.2 知识关联度评估（LLM节点，Prompt: prompts/knowledge-relevance.md）
4.3 综合评分计算（代码节点）：热度×0.4 + 知识×0.3 + 痛点×0.3

### 节点5：生成选题卡片（LLM节点）
Prompt：见 prompts/topic-card-generator.md
输出格式：JSON

### 节点6：写入Notion（插件节点）
数据库：选题库 · Topic Library
操作：创建新页面

## 注意事项
- 知识关联度评估需要先从 Notion 知识库读取所有条目，注入到 Prompt 中
- 注入方式：先用代码节点查询知识库 → 将知识库摘要拼入 LLM 节点的 user message
- 避免幻觉：只让 AI 从提供的知识库条目中选择，不要自创
