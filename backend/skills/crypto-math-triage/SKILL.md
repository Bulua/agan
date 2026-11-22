---
name: crypto-math-triage
description: 用于分析 RSA、对称分组密码、伪随机数与经典编码题目的结构化数学分析指南。
---

# Crypto 密码学题目数学分析指南

## 1. 参数提取与快速预检
- 完整提取已知量与未知量（`n`、`e`、`c`、IV/Nonce、轮函数等）。
- 可参考 `scripts/rsa_quick_check.py` 对常见 RSA 参数做基础结构预检（小指数、公共因子、位长等）。

## 2. 分组模式与随机性
- 对称密码核查工作模式（ECB / CBC / CTR / GCM）与 IV/Nonce 复用情况。
- 伪随机数重点分析状态转移方程（LCG / LFSR / MT19937）与输出截断位数。

## 3. 数值求解与回代校验
- 使用 `sympy` / `gmpy2` / `z3` 求解后务必回代校验明文是否符合预期格式。
