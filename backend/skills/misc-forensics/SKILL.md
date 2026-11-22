---
name: misc-forensics
description: 面向文件取证、隐写分析、流量包与多层编码还原的系统排查流程。
---

# Misc 杂项与取证排查清单

## 1. 文件头与附加数据
- 用 `file`、`exiftool` 检查真实格式与元数据字段。
- 用 `binwalk` 排查隐藏拼接的压缩包或媒体片段。

## 2. 编码与流量排查
- 识别 Base64 / Base32 / Hex / URL / Morse 等多层编码特征。
- 流量包优先统计协议分布并追踪 HTTP、DNS 与可疑 TCP 流。
