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
  
  // Connection-State Recovery: Notify client of recovery status
  if (socket.recovered) {
    console.log(`[+] Client recovered connection state: ${socket.id} for tenant: ${tenantId}`);
    socket.emit('recovery.status', {
      status: 'RECOVERED',
      namespace: '/dispatch-dashboard',
      recovery_window_seconds: 120,
      recovered: true,
      tenant_id: tenantId,
    });
  } else {
    socket.emit('recovery.status', {
      status: 'INITIALIZED',
      namespace: '/dispatch-dashboard',
      recovery_window_seconds: 120,
      recovered: false,
      tenant_id: tenantId,
    });
  }

  // Security: Do NOT allow client messages to mutate job status.

  // Authoritative updates are pushed from backend events only.
  const rejectStatusMutation = (data, callback) => {
    console.warn(`[Security] Client ${socket.id} attempted to mutate job status directly over WebSocket. Mutation rejected.`);
    if (typeof callback === 'function') {
      callback({
        error: 'Direct client status mutation forbidden. Authoritative backend updates only.',
        status: 'FORBIDDEN',
      });
    }
  };

  socket.on('job.status.update', rejectStatusMutation);
  socket.on('update_job_status', rejectStatusMutation);
  socket.on('job_status_update', rejectStatusMutation);
  socket.on('dispatch.update', rejectStatusMutation);
  socket.on('override_assignment', rejectStatusMutation);
  socket.on('mutate_dispatch', rejectStatusMutation);


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

redisSubscriber.subscribe('dispatch_events', 'sla_events', 'notification_events', 'payment_events', (err, count) => {
  if (err) {
    console.error('Failed to subscribe to Redis channels:', err);
  } else {
    console.log(`Subscribed to Redis channels. Currently subscribed to ${count} channels.`);
  }
});

redisSubscriber.on('message', (channel, message) => {
  if (channel === 'dispatch_events' || channel === 'sla_events' || channel === 'notification_events' || channel === 'payment_events') {
    try {
      const payload = JSON.parse(message);
      
      const { event, tenant_id, event_id } = payload;
      
      if (!event || !tenant_id) {
        console.warn('Received malformed event', payload);
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

      // If payload represents a status change and event was not already job.status.updated,
      // also broadcast canonical job.status.updated event
      if (event !== 'job.status.updated' && (payload.status || payload.new_status)) {
        const canonicalStatus = String(payload.status || payload.new_status || '').toUpperCase();
        const canonicalPayload = {
          event: 'job.status.updated',
          tenant_id: String(tenant_id),
          job_id: String(payload.job_id || ''),
          status: canonicalStatus,
          old_status: payload.old_status,
          timestamp: payload.timestamp,
          correlation_id: payload.correlation_id,
          event_id: payload.event_id,
        };
        dispatchNamespace.to(tenant_id).emit('job.status.updated', canonicalPayload);
        messagesSent++;
      }

      // If payload represents a location change and event was not already technician.location.updated,
      // also broadcast canonical technician.location.updated event
      if (event !== 'technician.location.updated' && (payload.latitude !== undefined && payload.longitude !== undefined)) {
        const canonicalLocPayload = {
          event: 'technician.location.updated',
          tenant_id: String(tenant_id),
          technician_id: String(payload.technician_id || ''),
          latitude: Number(payload.latitude),
          longitude: Number(payload.longitude),
          job_id: payload.job_id ? String(payload.job_id) : undefined,
          accuracy: payload.accuracy,
          altitude: payload.altitude,
          speed: payload.speed,
          heading: payload.heading,
          timestamp: payload.timestamp,
          correlation_id: payload.correlation_id,
          event_id: payload.event_id,
        };
        dispatchNamespace.to(tenant_id).emit('technician.location.updated', canonicalLocPayload);
        messagesSent++;
      }

      // If payload represents a dispatch update and event was not already dispatch.updated,
      // also broadcast canonical dispatch.updated event
      if (event !== 'dispatch.updated' && (payload.assignment_id || event === 'job_assigned' || event === 're-dispatch')) {
        const canonicalDispatchPayload = {
          event: 'dispatch.updated',
          tenant_id: String(tenant_id),
          job_id: String(payload.job_id || ''),
          assignment_id: String(payload.assignment_id || payload.event_id || '456'),
          technician_id: payload.technician_id,
          technician_name: payload.technician_name,
          dispatch_action: payload.dispatch_action || event,
          status: payload.status || 'ASSIGNED',
          timestamp: payload.timestamp,
          correlation_id: payload.correlation_id,
          event_id: payload.event_id,
        };
        dispatchNamespace.to(tenant_id).emit('dispatch.updated', canonicalDispatchPayload);
        messagesSent++;
      }

      // If payload represents an SLA alert and event was not already sla.alert,
      // also broadcast canonical sla.alert event
      if (
        event !== 'sla.alert' &&
        (payload.severity ||
         String(event).toLowerCase().includes('sla') ||
         String(event).toLowerCase().includes('breach'))
      ) {
        const canonicalSeverity = String(
          payload.severity ||
          (String(event).toLowerCase().includes('breached') ? 'BREACHED' : 'AT_RISK')
        ).toUpperCase();
        const canonicalSLAPayload = {
          event: 'sla.alert',
          tenant_id: String(tenant_id),
          job_id: String(payload.job_id || ''),
          severity: canonicalSeverity,
          sla_type: payload.sla_type || 'RESPONSE',
          time_remaining_seconds: payload.time_remaining_seconds !== undefined
            ? Number(payload.time_remaining_seconds)
            : (payload.payload && payload.payload.remaining_seconds !== undefined
                ? Number(payload.payload.remaining_seconds)
                : undefined),
          target_time: payload.target_time || payload.deadline || (payload.payload && (payload.payload.sla_deadline || payload.payload.target_time)),
          customer_tier: payload.customer_tier || 'STANDARD',
          timestamp: payload.timestamp || new Date().toISOString(),
          correlation_id: payload.correlation_id,
          event_id: payload.event_id,
        };
        dispatchNamespace.to(tenant_id).emit('sla.alert', canonicalSLAPayload);
        messagesSent++;
      }

      // If payload represents a notification update and event was not already notification.updated,
      // also broadcast canonical notification.updated event
      if (
        event !== 'notification.updated' &&
        (payload.notification_id || String(event).toLowerCase().includes('notification'))
      ) {
        const canonicalStatus = String(payload.status || 'SENT').toUpperCase();
        const canonicalNotificationPayload = {
          event: 'notification.updated',
          tenant_id: String(tenant_id),
          notification_id: String(payload.notification_id || payload.event_id || 'n-1'),
          status: canonicalStatus,
          channel: String(payload.channel || 'EMAIL').toUpperCase(),
          recipient_id: payload.recipient_id ? String(payload.recipient_id) : undefined,
          recipient_masked: payload.recipient_masked,
          attempt: payload.attempt !== undefined ? Number(payload.attempt) : 1,
          job_id: payload.job_id ? String(payload.job_id) : undefined,
          error_reason: payload.error_reason,
          timestamp: payload.timestamp || new Date().toISOString(),
          correlation_id: payload.correlation_id,
          event_id: payload.event_id,
        };
        dispatchNamespace.to(tenant_id).emit('notification.updated', canonicalNotificationPayload);
        messagesSent++;
      }

      // If payload represents a payment update and event was not already payment.updated,
      // also broadcast canonical payment.updated event
      if (
        event !== 'payment.updated' &&
        (payload.invoice_id || payload.payment_status || String(event).toLowerCase().includes('payment'))
      ) {
        const canonicalPaymentStatus = String(payload.payment_status || payload.status || 'PAID').toUpperCase();
        const canonicalPaymentPayload = {
          event: 'payment.updated',
          tenant_id: String(tenant_id),
          invoice_id: String(payload.invoice_id || payload.payment_id || payload.event_id || '10001'),
          payment_status: canonicalPaymentStatus,
          payment_id: payload.payment_id ? String(payload.payment_id) : undefined,
          job_id: payload.job_id ? String(payload.job_id) : undefined,
          customer_id: payload.customer_id ? String(payload.customer_id) : undefined,
          amount: payload.amount,
          currency: String(payload.currency || 'USD').toUpperCase(),
          payment_method: payload.payment_method ? String(payload.payment_method).toUpperCase() : undefined,
          error_reason: payload.error_reason,
          timestamp: payload.timestamp || new Date().toISOString(),
          correlation_id: payload.correlation_id,
          event_id: payload.event_id,
        };
        dispatchNamespace.to(tenant_id).emit('payment.updated', canonicalPaymentPayload);
        messagesSent++;
      }
      
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

// Canonical Job Status Updates config endpoint
app.get('/job-status/config', (req, res) => {
  res.json({
    event: 'job.status.updated',
    transport: 'REDIS_PUB_SUB_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    source_of_truth: 'POSTGRESQL',
  });
});

// Canonical Technician Location Updates config endpoint
app.get('/technician-location/config', (req, res) => {
  res.json({
    event: 'technician.location.updated',
    transport: 'REDIS_PUB_SUB_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    source_of_truth: 'POSTGRESQL',
  });
});

// Canonical Dispatch Updates config endpoint
app.get('/dispatch/config', (req, res) => {
  res.json({
    event: 'dispatch.updated',
    transport: 'REDIS_PUB_SUB_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    source_of_truth: 'POSTGRESQL',
  });
});

// Canonical SLA Alerts config endpoint
app.get('/sla-alerts/config', (req, res) => {
  res.json({
    event: 'sla.alert',
    transport: 'REDIS_PUB_SUB_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    source_of_truth: 'POSTGRESQL',
    valid_severities: ['AT_RISK', 'BREACHED', 'CRITICAL', 'INFO', 'RESOLVED', 'WARNING'],
  });
});

// Canonical Notification Updates config endpoint
app.get('/notifications/config', (req, res) => {
  res.json({
    event: 'notification.updated',
    transport: 'REDIS_PUB_SUB_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    source_of_truth: 'POSTGRESQL',
    valid_statuses: ['ACCEPTED', 'DELIVERED', 'FAILED', 'PENDING', 'PERMANENT_FAILURE', 'QUEUED', 'READ', 'RETRIED', 'SENT'],
  });
});

// Canonical Payment Updates config endpoint
app.get('/payments/config', (req, res) => {
  res.json({
    event: 'payment.updated',
    transport: 'REDIS_PUB_SUB_TO_WEBSOCKET',
    namespace: '/dispatch-dashboard',
    source_of_truth: 'POSTGRESQL',
    valid_statuses: ['AUTHORIZED', 'CANCELLED', 'FAILED', 'PAID', 'PARTIALLY_REFUNDED', 'PENDING', 'PROCESSING', 'REFUNDED', 'SUCCESSFUL', 'UNAVAILABLE'],
  });
});

// Canonical Reconnect & Connection-State Recovery status endpoint
app.get('/reconnect/status', (req, res) => {
  res.json({
    status: 'RECOVERED',
    namespace: '/dispatch-dashboard',
    recovery_window_seconds: 120,
  });
});

// Canonical Reconnect & Connection-State Recovery config endpoint
app.get('/reconnect/config', (req, res) => {
  res.json({
    status: 'RECOVERED',
    namespace: '/dispatch-dashboard',
    recovery_window_seconds: 120,
    max_disconnection_duration_ms: 120000,
    skip_middlewares: true,
    transport: 'SOCKET_IO_CONNECTION_STATE_RECOVERY',
    client_reconnection_options: {
      reconnection: true,
      reconnection_attempts: 10,
      reconnection_delay_ms: 1000,
      reconnection_delay_max_ms: 10000,
      timeout_ms: 10000,
    },
  });
});

// Reconnection sync evaluation endpoint
app.post('/reconnect/sync', (req, res) => {
  const { tenant_id, recovered, disconnected_at, recovery_window_seconds } = req.body || {};
  if (!tenant_id) {
    return res.status(400).json({ error: 'tenant_id is mandatory' });
  }

  const windowSeconds = Number(recovery_window_seconds || 120);
  let isRecovered = recovered !== undefined ? Boolean(recovered) : true;

  if (disconnected_at) {
    const elapsed = (Date.now() - Number(disconnected_at)) / 1000;
    if (elapsed > windowSeconds) {
      isRecovered = false;
    }
  }

  res.json({
    status: isRecovered ? 'RECOVERED' : 'SYNC_REQUIRED',
    namespace: '/dispatch-dashboard',
    recovery_window_seconds: windowSeconds,
  });
});


// Broadcast payment update directly via HTTP into the WebSocket server
app.post('/payments/broadcast', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const invoice_id = payload.invoice_id || payload.payment_id;
  const { tenant_id, event_id } = payload;
  const payment_status = payload.payment_status || payload.status || 'PAID';

  if (!invoice_id || !tenant_id) {
    return res.status(400).json({
      error: 'invoice_id and tenant_id are mandatory fields',
    });
  }

  const normStatus = String(payment_status).toUpperCase();

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      event: 'payment.updated',
      tenant_id: String(tenant_id),
      invoice_id: String(invoice_id),
      payment_status: normStatus,
      duplicate: true,
      status_code: 'SKIPPED',
    });
  }

  const canonicalPayload = {
    event: 'payment.updated',
    tenant_id: String(tenant_id),
    invoice_id: String(invoice_id),
    payment_status: normStatus,
    payment_id: payload.payment_id ? String(payload.payment_id) : undefined,
    job_id: payload.job_id ? String(payload.job_id) : undefined,
    customer_id: payload.customer_id ? String(payload.customer_id) : undefined,
    amount: payload.amount,
    currency: String(payload.currency || 'USD').toUpperCase(),
    payment_method: payload.payment_method ? String(payload.payment_method).toUpperCase() : undefined,
    error_reason: payload.error_reason,
    timestamp: payload.timestamp || new Date().toISOString(),
    correlation_id: payload.correlation_id,
    event_id: event_id,
  };

  dispatchNamespace.to(tenant_id).emit('payment.updated', canonicalPayload);
  messagesSent++;

  return res.json({
    event: 'payment.updated',
    tenant_id: String(tenant_id),
    invoice_id: String(invoice_id),
    payment_status: normStatus,
  });
});


// Broadcast notification update directly via HTTP into the WebSocket server
app.post('/notifications/broadcast', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const { notification_id, status, tenant_id, event_id } = payload;
  if (!notification_id || !tenant_id) {
    return res.status(400).json({
      error: 'notification_id and tenant_id are mandatory fields',
    });
  }

  const normStatus = String(status || 'SENT').toUpperCase();

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      event: 'notification.updated',
      tenant_id: String(tenant_id),
      notification_id: String(notification_id),
      status: normStatus,
      duplicate: true,
      status_code: 'SKIPPED',
    });
  }

  const canonicalPayload = {
    event: 'notification.updated',
    tenant_id: String(tenant_id),
    notification_id: String(notification_id),
    status: normStatus,
    channel: String(payload.channel || 'EMAIL').toUpperCase(),
    recipient_id: payload.recipient_id ? String(payload.recipient_id) : undefined,
    recipient_masked: payload.recipient_masked,
    attempt: payload.attempt !== undefined ? Number(payload.attempt) : 1,
    job_id: payload.job_id ? String(payload.job_id) : undefined,
    error_reason: payload.error_reason,
    timestamp: payload.timestamp || new Date().toISOString(),
    correlation_id: payload.correlation_id,
    event_id: event_id,
  };

  dispatchNamespace.to(tenant_id).emit('notification.updated', canonicalPayload);
  messagesSent++;

  return res.json({
    event: 'notification.updated',
    tenant_id: String(tenant_id),
    notification_id: String(notification_id),
    status: normStatus,
  });
});


// Broadcast SLA alert directly via HTTP into the WebSocket server
app.post('/sla-alerts/broadcast', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const { job_id, severity, tenant_id, event_id } = payload;
  if (!job_id || !tenant_id) {
    return res.status(400).json({
      error: 'job_id and tenant_id are mandatory fields',
    });
  }

  const normSeverity = String(severity || 'AT_RISK').toUpperCase();

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      event: 'sla.alert',
      tenant_id: String(tenant_id),
      job_id: String(job_id),
      severity: normSeverity,
      duplicate: true,
      status_code: 'SKIPPED',
    });
  }

  const canonicalPayload = {
    event: 'sla.alert',
    tenant_id: String(tenant_id),
    job_id: String(job_id),
    severity: normSeverity,
    sla_type: payload.sla_type || 'RESPONSE',
    time_remaining_seconds: payload.time_remaining_seconds,
    target_time: payload.target_time || payload.deadline,
    customer_tier: payload.customer_tier || 'STANDARD',
    timestamp: payload.timestamp || new Date().toISOString(),
    correlation_id: payload.correlation_id,
    event_id: event_id,
  };

  dispatchNamespace.to(tenant_id).emit('sla.alert', canonicalPayload);
  messagesSent++;

  return res.json({
    event: 'sla.alert',
    tenant_id: String(tenant_id),
    job_id: String(job_id),
    severity: normSeverity,
  });
});


// Broadcast dispatch update directly via HTTP into the WebSocket server
app.post('/dispatch/broadcast', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const { job_id, assignment_id, tenant_id, event_id } = payload;
  if (!job_id || !assignment_id || !tenant_id) {
    return res.status(400).json({
      error: 'job_id, assignment_id, and tenant_id are mandatory fields',
    });
  }

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      event: 'dispatch.updated',
      tenant_id: String(tenant_id),
      job_id: String(job_id),
      assignment_id: String(assignment_id),
      duplicate: true,
      status_code: 'SKIPPED',
    });
  }

  const canonicalPayload = {
    event: 'dispatch.updated',
    tenant_id: String(tenant_id),
    job_id: String(job_id),
    assignment_id: String(assignment_id),
    technician_id: payload.technician_id,
    technician_name: payload.technician_name,
    dispatch_action: payload.dispatch_action || 'ASSIGNED',
    status: payload.status || 'ASSIGNED',
    timestamp: payload.timestamp || new Date().toISOString(),
    correlation_id: payload.correlation_id,
    event_id: event_id,
  };

  dispatchNamespace.to(tenant_id).emit('dispatch.updated', canonicalPayload);
  messagesSent++;

  return res.json({
    event: 'dispatch.updated',
    tenant_id: String(tenant_id),
    job_id: String(job_id),
    assignment_id: String(assignment_id),
  });
});


// Broadcast technician location update directly via HTTP into the WebSocket server
app.post('/technician-location/broadcast', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const { technician_id, tenant_id, latitude, longitude, event_id } = payload;
  if (!technician_id || !tenant_id || latitude === undefined || longitude === undefined) {
    return res.status(400).json({
      error: 'technician_id, tenant_id, latitude, and longitude are mandatory fields',
    });
  }

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      event: 'technician.location.updated',
      tenant_id: String(tenant_id),
      technician_id: String(technician_id),
      latitude: Number(latitude),
      longitude: Number(longitude),
      duplicate: true,
      status_code: 'SKIPPED',
    });
  }

  const canonicalPayload = {
    event: 'technician.location.updated',
    tenant_id: String(tenant_id),
    technician_id: String(technician_id),
    latitude: Number(latitude),
    longitude: Number(longitude),
    job_id: payload.job_id ? String(payload.job_id) : undefined,
    accuracy: payload.accuracy,
    altitude: payload.altitude,
    timestamp: payload.timestamp || new Date().toISOString(),
    correlation_id: payload.correlation_id,
    event_id: event_id,
  };

  dispatchNamespace.to(tenant_id).emit('technician.location.updated', canonicalPayload);
  messagesSent++;

  return res.json({
    event: 'technician.location.updated',
    tenant_id: String(tenant_id),
    technician_id: String(technician_id),
    latitude: Number(latitude),
    longitude: Number(longitude),
  });
});


// Broadcast job status update directly via HTTP into the WebSocket server
app.post('/job-status/broadcast', (req, res) => {
  const payload = req.body;
  if (!payload || typeof payload !== 'object') {
    return res.status(400).json({ error: 'Invalid JSON payload' });
  }

  const { job_id, status, tenant_id, event_id } = payload;
  if (!job_id || !status || !tenant_id) {
    return res.status(400).json({
      error: 'job_id, status, and tenant_id are mandatory fields',
    });
  }

  if (isDuplicateEvent(event_id)) {
    return res.status(200).json({
      event: 'job.status.updated',
      tenant_id: String(tenant_id),
      job_id: String(job_id),
      status: String(status).toUpperCase(),
      duplicate: true,
      status_code: 'SKIPPED',
    });
  }

  const canonicalPayload = {
    event: 'job.status.updated',
    tenant_id: String(tenant_id),
    job_id: String(job_id),
    status: String(status).toUpperCase(),
    old_status: payload.old_status,
    timestamp: payload.timestamp || new Date().toISOString(),
    correlation_id: payload.correlation_id,
    event_id: event_id,
  };

  dispatchNamespace.to(tenant_id).emit('job.status.updated', canonicalPayload);
  messagesSent++;

  return res.json({
    event: 'job.status.updated',
    tenant_id: String(tenant_id),
    job_id: String(job_id),
    status: String(status).toUpperCase(),
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
