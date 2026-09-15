# Stage 1: build React/Vite assets.
FROM node:20-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: serve static assets and reverse proxy API requests.
FROM nginx:1.27-alpine

COPY infra/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html

EXPOSE 80
