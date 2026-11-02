# 设计文档检查记录

日期：2026-10-29。仅验证静态规范；未进行产品 UI 或可访问性整体验收。

## 颜色对比计算

按 sRGB 相对亮度公式计算，范围为下表显式组合，不包含未来组件的实际叠层和透明效果。

| 前景 | 背景 | 对比度 | 目标 |
|---|---|---:|---:|
| text | surface | 17.85:1 | 4.5:1 |
| textSecondary | surfaceSubtle | 6.92:1 | 4.5:1 |
| textMuted | bg | 4.55:1 | 4.5:1 |
| primary | primarySubtle | 6.16:1 | 4.5:1 |
| success | successSubtle | 4.79:1 | 4.5:1 |
| warning | warningSubtle | 6.84:1 | 4.5:1 |
| danger | dangerSubtle | 5.91:1 | 4.5:1 |
| neutral | neutralSubtle | 6.92:1 | 4.5:1 |
| onPrimary | primary | 6.70:1 | 4.5:1 |
| onPrimary | primaryHover | 8.72:1 | 4.5:1 |
| onPrimary | primaryActive | 10.36:1 | 4.5:1 |
| onPrimary | success | 5.02:1 | 4.5:1 |
| onPrimary | warning | 7.09:1 | 4.5:1 |
| onPrimary | danger | 6.47:1 | 4.5:1 |
| onPrimary | dangerHover | 8.31:1 | 4.5:1 |
| onPrimary | dangerActive | 10.02:1 | 4.5:1 |
| logText | logBg | 14.48:1 | 4.5:1 |
| controlBorder | surface | 4.76:1 | 3:1 |
| focus | surface | 5.17:1 | 3:1 |

## 检查范围

- design-tokens.json 可解析，基本颜色组合通过上述阈值。
- Markdown 本地文件链接检查见最终打包验证。
- 静态样张不连接 API，不代表任何开发阶段已完成。
- 真实组件尺寸、键盘、屏幕阅读器、三视口与缩放验证由阶段 01 起执行。

本次本地 Markdown 文件链接检查通过；外部链接未作离线可用性保证。
