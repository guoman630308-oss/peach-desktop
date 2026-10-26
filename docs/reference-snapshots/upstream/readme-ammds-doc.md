<div align="center">

# AMMDS DOC

[![Version](https://img.shields.io/badge/Version-1.6.47-blue.svg)](https://github.com/QYG2297248353/AMMDS-Doc)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Node.js](https://img.shields.io/badge/Node.js-%3E%3D20.0-yellow.svg)](https://nodejs.org/)
[![Docusaurus](https://img.shields.io/badge/Docusaurus-3.9.2-green.svg)](https://docusaurus.io/)

</div>

---

## 📋 目录

<details>
<summary>📑 展开/收起目录</summary>

- [项目简介](#项目简介)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [命令说明](#命令说明)
  - [开发相关](#开发相关)
  - [文档相关](#文档相关)
  - [图片处理](#图片处理)
  - [部署相关](#部署相关)
- [安装部署](#安装部署)
  - [环境要求](#环境要求)
  - [安装依赖](#安装依赖)
  - [本地开发](#本地开发)
  - [构建生产版本](#构建生产版本)
- [开发指南](#开发指南)
  - [添加新文档](#添加新文档)
  - [添加新博客](#添加新博客)
  - [添加新翻译](#添加新翻译)
  - [添加新版本](#添加新版本)
- [贡献指南](#贡献指南)
  - [代码规范](#代码规范)
- [许可证](#许可证)

</details>

---

## 📝 项目简介

AMMDS-DOC 是使用 Docusaurus 构建的文档网站，为 AMMDS（个人视频数据管理平台）提供完整的技术文档支持。该网站面向开发人员和用户，提供详细的使用教程、部署指南、插件开发文档和常见问题解答，支持多语言（英文、简体中文、日文、韩文）和多版本文档管理。

---

## 🛠️ 技术栈

| 技术 | 版本 | 说明 |
|------|------|------|
| Docusaurus | 3.9.2 | 静态网站生成器 |
| React | 19.0.0 | UI 框架 |
| TypeScript | 5.6.2 | 类型系统 |
| Mermaid | - | 图表支持 |
| Algolia DocSearch | 4.5.3 | 文档搜索 |
| PWA Plugin | 3.9.2 | 渐进式 Web 应用支持 |
| Image Zoom | 3.0.1 | 图片缩放功能 |
| 多语言 | - | 英文、简体中文、日文、韩文 |
| 多版本 | - | 支持历史版本文档管理 |

---

## 📁 目录结构

```tree
AMMDS-DOC/
├── .github/              # GitHub CI/CD 配置
│   └── workflows/
│       └── deploy.yml    # 自动部署工作流
├── blog/                 # 博客内容
│   ├── 2023/             # 博客文章按年份组织
│   ├── authors.yml       # 作者配置
│   └── tags.yml          # 标签配置
├── docs/                 # 文档内容（当前版本）
│   ├── deploy/           # 部署相关文档
│   ├── plugin/           # 插件相关文档
│   ├── tutorial/         # 教程相关文档
│   ├── usage/            # 使用相关文档
│   ├── intro.md          # 文档首页
│   └── qa.md             # 常见问题
├── i18n/                 # 国际化配置
│   ├── en/               # 英文翻译
│   ├── zh-Hans/          # 简体中文翻译
│   ├── ja/               # 日文翻译
│   └── ko/               # 韩文翻译
├── scripts/              # 自定义脚本
│   └── gen-webp.ts       # 生成 WebP 图片脚本
├── src/                  # 源代码
│   ├── components/       # React 组件
│   ├── css/              # 样式文件
│   ├── pages/            # 自定义页面
│   └── plugin/           # 自定义插件
├── static/               # 静态资源
│   └── img/              # 图片资源
├── versioned_docs/       # 历史版本文档
├── versioned_sidebars/   # 历史版本侧边栏配置
├── .eslintrc             # ESLint 配置
├── .gitignore            # Git 忽略配置
├── docusaurus.config.ts  # Docusaurus 主配置
├── package.json          # 项目依赖和脚本
├── sidebars.ts           # 侧边栏配置
├── tsconfig.json         # TypeScript 配置
└── versions.json         # 版本管理配置
```

---

## 🧰 命令说明

### 开发相关

| 命令 | 说明 | 示例 |
|------|------|------|
| `npm run start` | 启动本地开发服务器 | `npm run start` (默认端口 3000) |
| `npm run build` | 构建生产版本 | `npm run build` |
| `npm run serve` | 本地预览构建结果 | `npm run serve` (默认端口 3000) |
| `npm run typecheck` | TypeScript 类型检查 | `npm run typecheck` |
| `npm run clear` | 清理构建缓存 | `npm run clear` |

### 文档相关

| 命令 | 说明 | 示例 |
|------|------|------|
| `npm run write-translations` | 生成翻译文件 | `npm run write-translations -- --locale zh-Hans` |
| `npm run write-heading-ids` | 生成文档标题 ID | `npm run write-heading-ids` |
| `npm run swizzle` | 自定义 Docusaurus 组件 | `npm run swizzle @docusaurus/theme-classic DocSidebar` |
| `npm run gen:zhhans` | 生成简体中文翻译 | `npm run gen:zhhans` |
| `npm run gen:ja` | 生成日文翻译 | `npm run gen:ja` |
| `npm run gen:ko` | 生成韩文翻译 | `npm run gen:ko` |
| `npm run gen:lang` | 生成所有语言翻译 | `npm run gen:lang` |

### 图片处理

| 命令 | 说明 | 示例 |
|------|------|------|
| `npm run gen:webp` | 生成 WebP 格式图片 | `npm run gen:webp` |
| `npm run prebuild` | 构建前准备（包括生成 WebP 图片） | `npm run prebuild` |

### 部署相关

| 命令 | 说明 | 示例 |
|------|------|------|
| `npm run deploy` | 部署到 GitHub Pages | `npm run deploy` |

---

## 🚀 安装部署

### 环境要求

- Node.js >= 20.0
- npm >= 9.0

### 安装依赖

```bash
# 克隆仓库
git clone https://github.com/QYG2297248353/AMMDS-Doc.git
cd AMMDS-Doc

# 安装依赖
npm install
```

### 本地开发

```bash
# 启动开发服务器
npm run start

# 启动开发服务器 指定语言
npm run start -- --locale zh-Hans

# 访问 http://localhost:3000 查看文档
```

### 构建生产版本

```bash
# 构建生产版本
npm run build

# 预览构建结果
npm run serve

# 访问 http://localhost:3000 查看构建结果
```

---

## 📚 开发指南

### 添加新文档

1. 在 `docs/` 目录下创建新的 Markdown 文件
2. 文档会自动添加到侧边栏（基于目录结构）
3. 使用 `_category_.json` 文件自定义目录配置，例如：

```json
{
  "label": "部署指南",
  "position": 1,
  "link": {
    "type": "generated-index",
    "description": "学习如何部署 AMMDS"
  }
}
```

### 添加新博客

1. 在 `blog/` 目录下创建新的 Markdown 文件
2. 文件名格式：`YYYY/MM/DD/title.md`
3. 在文件头部添加 Front Matter 配置：

```markdown
---
title: "博客标题"
author: "作者名称"
date: "2024-01-01"
tags: ["更新", "功能"]
description: "博客描述"
---
```

### 添加新翻译

1. 运行翻译生成命令：

```bash
# 生成所有语言翻译
npm run gen:lang

# 或生成特定语言翻译
npm run gen:zhhans  # 简体中文
npm run gen:ja      # 日文
npm run gen:ko      # 韩文
```

2. 在 `i18n/[locale]/` 目录下编辑翻译文件
3. 支持的语言：`en`（英文）、`zh-Hans`（简体中文）、`ja`（日文）、`ko`（韩文）

### 添加新版本

1. 编辑 `versions.json` 文件，添加新版本号
2. 运行命令创建新版本文档：

```bash
npm run docusaurus docs:version v1.6.48
```

3. 编辑 `docusaurus.config.ts` 中的版本配置

---

## 🤝 贡献指南

### 代码规范

1. **TypeScript**：使用 TypeScript 编写所有代码，确保类型安全
2. **ESLint**：遵循项目的 ESLint 配置
3. **代码风格**：保持一致的代码风格，使用 2 个空格缩进
4. **提交信息**：使用清晰的提交信息，遵循语义化提交规范
5. **PR 流程**：
   - Fork 本仓库
   - 创建新的分支（feature/xxx 或 fix/xxx）
   - 提交代码
   - 创建 Pull Request，描述更改内容和目的

---

## 📄 许可证

MIT License
