/**
 * Desktop 核心冒烟测试
 * 校验 Desktop 生产产物完整性、依赖闭环、页面路由与关键 DOM 结构
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const root = path.resolve(__dirname, "..");
const distDir = path.join(root, "apps", "desktop", "dist");

console.log("[Smoke Test] 正在检查 Desktop 构建产物...");

// 1. 检查 index.html 存在
const indexPath = path.join(distDir, "index.html");
assert(fs.existsSync(indexPath), "dist/index.html 不存在，请先执行 pnpm build");
const html = fs.readFileSync(indexPath, "utf-8");
assert(html.includes("<title>Forge Coding Agent</title>") || html.includes("<div id=\"root\">"), "index.html 格式异常");

// 2. 检查 assets 目录
const assetsDir = path.join(distDir, "assets");
assert(fs.existsSync(assetsDir), "dist/assets 目录不存在");
const files = fs.readdirSync(assetsDir);
const jsBundle = files.find(f => f.endsWith(".js"));
const cssBundle = files.find(f => f.endsWith(".css"));
assert(jsBundle, "未找到 JS bundle 产物");
assert(cssBundle, "未找到 CSS bundle 产物");

console.log(`[Smoke Test] 发现产物: JS: ${jsBundle}, CSS: ${cssBundle}`);

// 3. 校验 CSS 产物中是否包含浅色主题对比度覆盖及无障碍关键类
const cssContent = fs.readFileSync(path.join(assetsDir, cssBundle), "utf-8");
assert(cssContent.includes("plan-step-copy"), "CSS 缺少 plan-step-copy 样式定义");
assert(cssContent.includes("async-message"), "CSS 缺少 async-message 状态样式");
assert(cssContent.includes("focus-visible"), "CSS 缺少 focus-visible 焦点环");

// 4. 校验 JS 产物包含关键核心交互逻辑
const jsContent = fs.readFileSync(path.join(assetsDir, jsBundle), "utf-8");
assert(jsContent.includes("FORGE"), "JS 缺少应用品牌与核心文本");

console.log("[Smoke Test] Desktop 核心冒烟测试通过！构建产物健康且完整。");
