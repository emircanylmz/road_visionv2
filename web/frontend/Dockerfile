# Aşama 1: SPA üretim derlemesi. package-lock.json depodadır;
# npm ci sürüm çözümlemesini kilitten okur (deterministik imaj).
FROM node:22-alpine AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# Aşama 2: nginx ile servis.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 80
