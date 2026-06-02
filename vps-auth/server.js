/**
 * MIA GEKDI — Auth Server v1
 * Ubuntu VPS: 62.238.37.129
 * Node.js + Express + JWT + bcrypt
 *
 * Endpoints:
 *   POST /api/auth/login    — authenticate, return JWT
 *   GET  /api/auth/verify   — validate Bearer token
 *   POST /api/auth/logout   — revoke session
 *   GET  /health            — liveness check
 *
 * Config (.env):
 *   JWT_SECRET      — random 48+ char string (REQUIRED)
 *   AUTH_HASH       — bcrypt hash of password (REQUIRED)
 *   PORT            — default 3000
 *   ALLOWED_ORIGINS — comma-separated CORS origins
 */

'use strict';

const express   = require('express');
const jwt       = require('jsonwebtoken');
const bcrypt    = require('bcryptjs');
const rateLimit = require('express-rate-limit');
const crypto    = require('crypto');
const fs        = require('fs');
const path      = require('path');

// ── Config ──────────────────────────────────────────────────────────
const PORT    = parseInt(process.env.PORT || '3000', 10);
const JWT_SECRET = process.env.JWT_SECRET;
const AUTH_HASH  = process.env.AUTH_HASH;
const ORIGINS    = (process.env.ALLOWED_ORIGINS || 'https://secrett-archive.com').split(',').map(s=>s.trim());
const TOKEN_TTL  = 86400; // 24 hours

if (!JWT_SECRET || !AUTH_HASH) {
  console.error('[FATAL] JWT_SECRET and AUTH_HASH must be set in .env');
  console.error('  Generate hash: node -e "require(\'bcryptjs\').hash(\'YOUR_PASSWORD\',12).then(console.log)"');
  process.exit(1);
}

// ── Session store (in-memory + file persistence) ────────────────────
const SESSIONS = new Map(); // jti → { exp, ip, created_at }
const SESS_FILE = path.join(__dirname, 'sessions.json');

function saveSessions() {
  const now = Math.floor(Date.now() / 1000);
  const valid = {};
  SESSIONS.forEach((v, k) => { if (v.exp > now) valid[k] = v; });
  try { fs.writeFileSync(SESS_FILE, JSON.stringify(valid)); } catch(_) {}
}
function loadSessions() {
  try {
    if (!fs.existsSync(SESS_FILE)) return;
    const obj = JSON.parse(fs.readFileSync(SESS_FILE, 'utf8'));
    const now = Math.floor(Date.now() / 1000);
    let n = 0;
    Object.entries(obj).forEach(([k, v]) => { if (v.exp > now) { SESSIONS.set(k, v); n++; } });
    if (n > 0) console.log(`[AUTH] Restored ${n} active sessions`);
  } catch(_) {}
}
loadSessions();
setInterval(saveSessions, 60_000);
['SIGINT','SIGTERM'].forEach(sig => process.on(sig, () => { saveSessions(); process.exit(0); }));

// ── Audit log ───────────────────────────────────────────────────────
const LOG_FILE = path.join(__dirname, 'auth.log');
function authLog(ip, success, note) {
  const line = JSON.stringify({ ts: new Date().toISOString(), ip, success, note }) + '\n';
  fs.appendFile(LOG_FILE, line, () => {});
}

// ── App ─────────────────────────────────────────────────────────────
const app = express();
app.set('trust proxy', 1); // trust nginx X-Forwarded-For
app.use(express.json({ limit: '16kb' }));

// Security headers
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  next();
});

// CORS
app.use((req, res, next) => {
  const origin = req.headers.origin;
  if (origin && ORIGINS.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  } else if (ORIGINS.includes('*')) {
    res.setHeader('Access-Control-Allow-Origin', '*');
  }
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');
  res.setHeader('Access-Control-Max-Age', '86400');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// Rate limiter: 5 attempts per 15 minutes per IP
const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 5,
  standardHeaders: true,
  legacyHeaders: false,
  keyGenerator: (req) => req.headers['x-forwarded-for']?.split(',')[0] || req.ip,
  handler: (req, res) => {
    const ip = req.headers['x-forwarded-for']?.split(',')[0] || req.ip;
    authLog(ip, false, 'rate_limited');
    res.status(429).json({ success: false, error: 'Too many attempts. Try again in 15 minutes.' });
  }
});

// ── GET /health ──────────────────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'mia-auth-v1', sessions: SESSIONS.size, ts: new Date().toISOString() });
});

// ── POST /api/auth/login ─────────────────────────────────────────────
app.post('/api/auth/login', loginLimiter, async (req, res) => {
  const ip = req.headers['x-forwarded-for']?.split(',')[0] || req.ip;
  const { password } = req.body || {};

  if (!password || typeof password !== 'string' || password.length > 128) {
    return res.status(400).json({ success: false, error: 'Invalid request.' });
  }

  try {
    const valid = await bcrypt.compare(password, AUTH_HASH);
    if (!valid) {
      authLog(ip, false, 'wrong_password');
      return res.status(401).json({ success: false, error: 'Invalid password.' });
    }

    const jti = crypto.randomUUID();
    const iat = Math.floor(Date.now() / 1000);
    const exp = iat + TOKEN_TTL;

    const token = jwt.sign({ sub: 'mia-gekdi', jti }, JWT_SECRET, {
      algorithm: 'HS256',
      expiresIn: TOKEN_TTL,
      issuer:    'mia-auth',
    });

    SESSIONS.set(jti, { exp, ip, created_at: iat });
    authLog(ip, true, 'login_ok');
    return res.json({ success: true, token, expires_at: exp });

  } catch (e) {
    console.error('[AUTH] Login error:', e.message);
    return res.status(500).json({ success: false, error: 'Server error.' });
  }
});

// ── GET /api/auth/verify ──────────────────────────────────────────────
app.get('/api/auth/verify', (req, res) => {
  const h = req.headers['authorization'] || '';
  const token = h.startsWith('Bearer ') ? h.slice(7).trim() : null;
  if (!token) return res.status(401).json({ valid: false, reason: 'no_token' });

  try {
    const payload = jwt.verify(token, JWT_SECRET, {
      algorithms: ['HS256'],
      issuer:     'mia-auth',
    });
    if (!SESSIONS.has(payload.jti)) {
      return res.status(401).json({ valid: false, reason: 'revoked' });
    }
    return res.json({ valid: true, expires_at: payload.exp });
  } catch (e) {
    const reason = e.name === 'TokenExpiredError' ? 'expired' : 'invalid';
    return res.status(401).json({ valid: false, reason });
  }
});

// ── POST /api/auth/logout ─────────────────────────────────────────────
app.post('/api/auth/logout', (req, res) => {
  const h = req.headers['authorization'] || '';
  const token = h.startsWith('Bearer ') ? h.slice(7).trim() : null;
  if (token) {
    try {
      const payload = jwt.decode(token);
      if (payload?.jti) SESSIONS.delete(payload.jti);
    } catch (_) {}
  }
  authLog(req.headers['x-forwarded-for']?.split(',')[0] || req.ip, true, 'logout');
  return res.json({ success: true });
});

// ── 404 ──────────────────────────────────────────────────────────────
app.use((req, res) => {
  res.status(404).json({
    error: 'Not found',
    available: [
      'POST /api/auth/login',
      'GET  /api/auth/verify',
      'POST /api/auth/logout',
      'GET  /health',
    ]
  });
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[MIA AUTH] Listening on 127.0.0.1:${PORT}`);
  console.log(`[MIA AUTH] CORS: ${ORIGINS.join(', ')}`);
});
