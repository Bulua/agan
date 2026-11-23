# HTTP 响应头与指纹速查

- `Server` / `X-Powered-By`：判断 Nginx、Werkzeug、Express、PHP、Gunicorn 等技术栈。
- `Set-Cookie`：检查 `HttpOnly`、`Secure`、`SameSite` 以及 Session 编码格式（如 Flask signed cookie、JWT）。
- `Access-Control-Allow-Origin`：核查 CORS 配置。
- `Content-Security-Policy`：分析脚本与资源加载限制。
- `Location`：追踪 301/302 重定向链与参数透传。
