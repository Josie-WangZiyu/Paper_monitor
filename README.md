# Paper Monitor | 声学文献追踪器

一个自动追踪声学、音频、振动领域最新学术文献的静态网站，支持部署到 GitHub Pages。

## 追踪的期刊与来源

| 来源 | 简称 | 领域 |
|------|------|------|
| Journal of the Acoustical Society of America | JASA | 声学 |
| Journal of Sound and Vibration | JSV | 振动与声 |
| Applied Acoustics | App. Acoustics | 应用声学 |
| Journal of the Audio Engineering Society | AES | 音频工程 |
| arXiv (cs.SD / eess.AS) | arXiv | 音频与声学预印本 |

## 功能特性

- **自动抓取**: 每日通过 GitHub Actions 自动从各期刊 RSS 和 arXiv API 抓取最新论文
- **前端展示**: 暗色主题响应式网页，支持搜索、期刊筛选、摘要展开
- **一键访问**: 直接跳转至原文 DOI 或 arXiv 页面
- **去重合并**: 智能去重，保留历史数据

## 快速开始

### 本地运行

```bash
# 安装依赖
pip install -r scripts/requirements.txt

# 手动抓取数据
python scripts/fetch_papers.py

# 本地预览（可用任意静态服务器）
python -m http.server 8080
# 然后打开 http://localhost:8080
```

### GitHub Pages 部署

1. **Fork / 上传** 本仓库到你的 GitHub 账号
2. **开启 GitHub Pages**: Settings → Pages → Source 选择 `Deploy from a branch` → Branch 选 `main`，目录选 `/ (root)`
3. **配置自动更新**: GitHub Actions 已配置，每天 UTC 06:00 自动抓取更新

访问 `https://你的用户名.github.io/Paper_monitor/` 即可查看。

## 项目结构

```
Paper_monitor/
├── data/
│   └── papers.json          # 文献数据（自动更新）
├── scripts/
│   ├── fetch_papers.py      # 抓取脚本
│   └── requirements.txt     # Python 依赖
├── .github/workflows/
│   └── update-papers.yml    # GitHub Actions 自动更新
├── index.html               # 前端页面
└── README.md
```

## 手动添加特定期刊

编辑 `scripts/fetch_papers.py` 中的 `JOURNALS` 字典，添加 RSS 源即可：

```python
JOURNALS = {
    "your_journal": {
        "name": "Full Journal Name",
        "short": "Short",
        "rss": "https://example.com/rss",
        "website": "https://example.com",
    },
    # ...
}
```

## 许可证

MIT
