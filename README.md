# EggyTrendStudio

蛋仔派对轻量化小游戏内容生产台的可交互 MVP。

## 当前可演示流程

- 选题审核：候选选题按行展示，可直接“通过 / 淘汰”
- 选题库：通过的选题进入选题库
- 题目审核：展开某个选题，在该选题下审核完整“题干 + A/B/C/D 选项”
- Badcase：淘汰的选题和题目保留为 Badcase / 负样本
- 数据飞轮：展示选题与题目审核数据如何反哺后续推荐与生成

## 本地打开

直接双击 `index.html` 即可。

## 发布到 GitHub Pages

1. 在 GitHub 新建仓库，例如 `EggyTrendStudio`
2. 把本仓库全部文件上传到 `main` 分支根目录
3. 打开仓库 `Settings`
4. 进入 `Pages`
5. 在 `Build and deployment` 中选择 `Deploy from a branch`
6. Branch 选择 `main`，目录选择 `/ (root)`，保存
7. 等待 GitHub Pages 部署完成后，仓库 Pages 页面会显示公网地址

## 说明

当前版本是前端交互原型，数据存储在浏览器 `localStorage` 中。
下一阶段需要把真实的 `EggyTrendAgent` 输出接到后端数据库，再由网页读取并写回审核结果。
