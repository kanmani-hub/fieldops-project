const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const Redis = require('ioredis');

const app = express();
const server = http.createServer(app);

const io = new Server(server, {
  cors: {
    origin: '*',
  },
  pingInterval: 30000,
  pingTimeout: 5000,
  connectionStateRecovery: {
    maxDisconnectionDuration: 2 * 60 * 1000, // 2 minutes auto-reconnect backoff
    skipMiddlewares: true,
  }
});

const redisSubscriber = new Redis({
  host: process.env.REDIS_HOST || 'localhost',
  port: process.env.REDIS_PORT || 6379,
  connectTimeout: 5000,
  maxRetriesPerRequest: null,
  retryStrategy(times) {
    const delay = Math.min(times * 200, 3000);
    console.warn(`[Redis Subscriber] Reconnecting in ${delay}ms (attempt ${times})...`);
    return delay;
  },
});

redisSubscriber.on('connect', () => {
  console.log('[Redis Subscriber] Connected to Redis successfully.');
});

redisSubscriber.on('error', (err) => {
  console.error('[Redis Subscriber] Connection error (safe degraded mode):', err.message);
});

redisSubscriber.on('reconnecting', () => {
  console.log('[Redis Subscriber] Reconnecting to Redis...');
});

const PORT = process.env.PORT || 4000;
const MAX_CONNECTIONS_PER_IP = 100;

// Metrics
let totalConnections = 0;
let messagesSent = 0;
const ipConnectionCounts = new Map();

const dispatchNamespace = io.of('/dispatch-dashboard');

dispatchNamespace.use((socket, next) => {
  const ip = socket.handshake.address;
  const currentCount = ipConnectionCounts.get(ip) || 0;
  
  if (currentCount >= MAX_CONNECTIONS_PER_IP) {
    return next(new Error('Connection limit exceeded for IP'));
  }
  
  ipConnectionCounts.set(ip, currentCount + 1);
  
  // Handshake should contain tenant_id in query or auth
  const tenantId = socket.handshake.query.tenant_id || socket.handshake.auth.tenant_id;
  if (!tenantId) {
    return next(new Error('Authentication error: tenant_id required'));
  }
  
  socket.tenantId = tenantId;
  next();
});

dispatchNamespace.on('connection', (socket) => {
  totalConnections++;
  const tenantId = socket.tenantId;
  const ip = socket.handshake.address;
  
  console.log(`[+] Client connected: ${socket.id} to tenant: ${tenantId}`);
  socket.join(tenantId);
  
  socket.on('disconnect', () => {
    totalConnections--;
    const currentCount = ipConnectionCounts.get(ip) || 0;
    if (currentCount > 1) {
      ipConnectionCounts.set(ip, currentCount - 1);
    } else {
      ipConnectionCounts.delete(ip);
    }
    console.log(`[-] Client disconnected: ${socket.id}`);
  });
});

redisSubscriber.subscribe('dispatch_events', (err, count) => {
  if (err) {
    console.error('Failed to subscribe to Redis dispatch_events channel:', err);
  } else {
    console.log(`Subscribed to Redis dispatch_events. Currently subscribed to ${count} channels.`);
  }
});

redisSubscriber.on('message', (channel, message) => {
  if (channel === 'dispatch_events') {
    try {
      const payload = JSON.parse(message);
      
      const { event, tenant_id } = payload;
      
      if (!event || !tenant_id) {
        console.warn('Received malformed dispatch event', payload);
        return;
      }
      
      // Broadcast to all clients in the specific tenant room
      dispatchNamespace.to(tenant_id).emit(event, payload);
      messagesSent++;
      
      console.log(`Broadcasted [${event}] to tenant [${tenant_id}]`);
      
    } catch (e) {
      console.error('Error parsing dispatch event from Redis:', e);
    }
  }
});

app.get('/metrics', (req, res) => {
  res.json({
    connections: totalConnections,
    messages_broadcasted: messagesSent,
    unique_ips: ipConnectionCounts.size,
    uptime_seconds: process.uptime()
  });
});

server.listen(PORT, () => {
  console.log(`Dispatch WebSocket Server running on port ${PORT}`);
});
