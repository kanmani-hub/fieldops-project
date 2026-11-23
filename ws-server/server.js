const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const Redis = require('ioredis');

const app = express();
app.use(express.json());
const server = http.createServer(app);

// In-memory bounded deduplication for Kafka and Redis events
const SEEN_EVENTS_MAX = 10000;
const seenEventIds = new Set();
const seenEventQueue = [];

function isDuplicateEvent(eventId) {
  if (!eventId) return false;
  const strId = String(eventId);
  if (seenEventIds.has(strId)) {
    return true;
  }
  if (seenEventQueue.length >= SEEN_EVENTS_MAX) {
    const oldest = seenEventQueue.shift();
    seenEventIds.delete(oldest);
  }
  seenEventIds.add(strId);
  seenEventQueue.push(strId);
  return false;
}


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

let redisErrorsCount = 0;
let redisReconnectCount = 0;

redisSubscriber.on('connect', () => {
  console.log('[Redis Subscriber] Connected to Redis successfully.');
});

redisSubscriber.on('error', (err) => {
  redisErrorsCount++;
  console.error('[Redis Subscriber] Connection error (safe degraded mode):', err.message);
});

redisSubscriber.on('reconnecting', () => {
  redisReconnectCount++;
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
      
      const { event, tenant_id, event_id } = payload;
      
      if (!event || !tenant_id) {
        console.warn('Received malformed dispatch event', payload);
        return;
      }
      
      if (isDuplicateEvent(event_id)) {
        console.log(`[Deduplication] Skipping duplicate broadcast for event_id: ${event_id}`);
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

// Canonical Kafka-to-WebSocket bridge status endpoint
app.get('/bridge/status', (req, res) => {
  res.json({
    status: 'CONNECTED',
    transport: 'KAFKA_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
  });
});

// Bridge Kafka event directly via HTTP into the WebSocket server
app.post('/bridge/kafka-event', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const { event, event_type, tenant_id, event_id } = payload;
  const eventName = event || event_type;

  if (!eventName || !tenant_id) {
    return res.status(400).json({
      error: 'Missing required fields: event/event_type and tenant_id are mandatory',
    });
  }

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      status: 'SKIPPED',
      idempotent_duplicate: true,
      event_id: event_id,
      tenant_id: tenant_id,
      namespace: '/dispatch-dashboard',
    });
  }

  dispatchNamespace.to(tenant_id).emit(eventName, payload);
  messagesSent++;

  return res.json({
    status: 'CONNECTED',
    transport: 'KAFKA_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    delivered: true,
    event_id: event_id,
    tenant_id: tenant_id,
  });
});

app.get('/metrics', (req, res) => {
  res.json({
    connections: totalConnections,
    messages_broadcasted: messagesSent,
    unique_ips: ipConnectionCounts.size,
    uptime_seconds: process.uptime(),
    redis_subscriber: {
      status: redisSubscriber.status,
      connected: redisSubscriber.status === 'ready',
      connection_errors: redisErrorsCount,
      reconnect_attempts: redisReconnectCount,
    }
  });
});

server.listen(PORT, () => {
  console.log(`Dispatch WebSocket Server running on port ${PORT}`);
});
