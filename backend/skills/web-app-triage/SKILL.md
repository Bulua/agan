---
name: web-app-triage
description: 针对 CTF Web 题目的标准化信息收集、源码审计、鉴权与响应特征排查流程。
---

# Web 题目系统化排查清单

## 1. 信息收集与响应特征
- 检查 HTTP 响应头（`Server`、`X-Powered-By`、`Set-Cookie`、`Content-Type`），详细核对项见 `references/http-headers-checklist.md`。
- 排查 `/robots.txt`、`/.well-known/`、前端 JS 中的隐藏接口与调试端点。
- 梳理所有可控输入点：Query、POST Body、Cookie、Header 与上传字段。

## 2. 鉴权与会话机制审计
- 解码 Cookie / JWT 观察结构、算法与字段，核实服务端校验逻辑是否完备。
- 检查敏感接口权限校验是否一致。

## 3. 最小化复现验证
- 将每一步验证写成独立可复现脚本，并将确认的线索记录到工作笔记。
