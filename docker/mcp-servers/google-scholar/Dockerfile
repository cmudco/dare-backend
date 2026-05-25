FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY server.js ./

ENV PORT=3015
EXPOSE 3015

CMD ["node", "server.js"]
