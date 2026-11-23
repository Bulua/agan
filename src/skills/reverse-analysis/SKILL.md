---
name: reverse-analysis
description: 适用于 ELF/PE/脚本字节码逆向的静态定位、控制流还原与约束求解方法。
---

# Reverse 逆向工程分析清单

## 1. 文件识别与定位
- 使用 `file` / `readelf` / `objdump` 识别格式并确认是否加壳或混淆。
- 搜索关键提示字符串（如 `Correct` / `Wrong` / `Flag`）定位核心校验逻辑。

## 2. 逻辑还原与求解
- 梳理长度校验、字符范围约束及逐字节变换流程。
- 对多条件方程组优先采用 `z3` 符号约束建模并验证唯一解。
