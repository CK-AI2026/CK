# 创作者 AI 工作台

Phase 1（选题猎人）包含：云端热榜采集、AI 选题筛选、Notion 入库、Mac Safari 视频数据补全，以及逐字稿提取。

## 云端热榜采集

GitHub Actions 在北京时间每天 07:00、22:00 运行 `hot-collector.py`。在仓库 **Settings → Secrets and variables → Actions** 中配置以下 repository secrets：

- `NOTION_TOKEN`
- `DEEPSEEK_API_KEY`

可在 Actions 页面通过 **Collect hot topics → Run workflow** 手动验证。公开仓库不得提交 `.env` 或任何真实密钥。

## Mac 本地工具

`video-stats-filler.py` 与逐字稿脚本依赖本机 Safari 登录态、ffmpeg、yt-dlp 和 Whisper，不在 GitHub Actions 中运行。
