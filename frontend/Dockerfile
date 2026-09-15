# EngramNote 前端 Dockerfile
# 多阶段构建：Node.js 构建 → Nginx 运行

# ---- 阶段1: 构建前端 ----
# ⚠️ 22 不是"顺手升级"：锁文件里的 `jsdom@30` 要求 `^22.22.2 || ^24.15.0 || >=26`，
# 它依赖的 `undici@8` 要求 `node >= 22.19`。`npm ci` 对 engines 不匹配**只警告**
# （EBADENGINE，不失败），所以 Node 18 的镜像能装完依赖却在跑测试时炸 ——
# 2026-09-15 CI 上就是这么红的（Node 20 + jsdom 30 → vitest 报 "no tests"）。
# 镜像这一层只跑 `npm run build`（不需要 jsdom），但让**构建环境与实际要求**
# 差着三个大版本只会把同一个坑留给下一次。
FROM node:22-alpine AS builder

# 使用淘宝 npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /app

# 先复制依赖清单**与锁文件**，利用 Docker 缓存层
#
# ⚠️ 锁文件必须一起复制（这一行此前只 COPY 了 package.json）：
# `npm ci` **要求** `package-lock.json` 在场，缺了它直接失败；而
# `npm install` 会**忽略锁文件**去解析版本 —— 于是镜像里的依赖树可能与
# CI 与本机都不同（"构建出来的东西和被测过的东西不是同一份"）。
# 现在这里与 `.github/workflows/ci.yml` 的 frontend job 用**同一条命令 +
# 同一份锁文件**。
COPY package.json package-lock.json ./

# 安装依赖（可复现：严格按锁文件还原）
RUN npm ci

# 复制源代码
COPY . .

# 构建生产版本
RUN npm run build

# ---- 阶段2: Nginx 运行 ----
FROM nginx:alpine

# 使用阿里云镜像源（已在 Docker daemon 配置中加速）
# 复制自定义 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 从 builder 阶段复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 暴露端口
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
