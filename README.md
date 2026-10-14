# GitHub Skill Catalog API

该仓库通过 GitHub Pages 提供公开 JSON 数据，并每天自动刷新一次。

## API

最新数据地址：

```text
https://huadu123.github.io/github-skill-catalog-api/latest.json
```

响应包含仓库名称、地址、简介、主题、stars、推送时间等公开元数据。

## 首次发布

1. 在仓库的 Settings > Pages 中，将 Source 设为 GitHub Actions。
2. 在 Actions 页面手动运行 `Publish skill catalog API`。
3. 首次发布完成后，通过上方地址获取 JSON。

工作流每天 UTC 01:00 运行，即北京时间 09:00。它使用工作流临时令牌读取公开搜索结果，不需要保存个人访问令牌。

## 数据范围

数据来自公开仓库搜索 `topic:agent-skills`，筛选最近 24 小时内有 `pushed_at` 活动的仓库。此字段只表示仓库有推送活动，不代表 `SKILL.md` 正文在该时段有变化。API 返回仓库元数据，不下载仓库文件。
