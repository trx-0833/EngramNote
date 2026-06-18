# EngramNote 前端 Dockerfile
# 多阶段构建：Node.js 构建 → Nginx 运行

# ---- 阶段1: 构建前端 ----
FROM node:18-alpine AS builder

# 使用淘宝 npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY package.json ./

# 安装依赖
RUN npm install

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
