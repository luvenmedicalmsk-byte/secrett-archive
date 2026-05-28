/**
 * Autonomous Detector Registry v4
 *
 * Each detector: independent module with threshold, confidence,
 * cooldown, anomaly_memory, baseline.
 *
 * DetectorRegistry.run(events) → { alerts, active_detectors, alert_level }
 */

'use strict';

IE.DetectorRegistry = {

  // Alert state machine
  ALERT_STATES: ['monitor','elevated','critical','cascading','systemic_break'],

  // Each detector: { id, label, threshold, cooldown_hrs, fn(events)→score 0-100 }
  DETECTORS: [
    {
      id: 'food_supply',
      label: 'Продовольственный стресс',
      threshold: 35,
      cooldown_hrs: 6,
      fn(events) {
        const KW = ['food','grain','wheat','harvest','drought','famine','hunger',
                    'продовольств','зерно','урожай','засуха','голод'];
        const matched = events.filter(e => KW.some(k => ((e.title||'')+(e.summary||'')).toLowerCase().includes(k)));
        if (!matched.length) return 0;
        const c2s = matched.filter(e => e.domain==='climate' && (e.cascade||[]).includes('social')).length;
        return Math.min(100, matched.length * 8 + c2s * 15 +
          matched.reduce((s,e)=>s+(e.escalation_score||0),0)/matched.length*0.3);
      }
    },
    {
      id: 'energy_instability',
      label: 'Энергетическая нестабильность',
      threshold: 30,
      cooldown_hrs: 4,
      fn(events) {
        const KW = ['energy','power outage','blackout','pipeline','oil','gas','grid',
                    'энергет','отключен','нефть','газ','электро'];
        const matched = events.filter(e => KW.some(k => ((e.title||'')+(e.summary||'')).toLowerCase().includes(k)));
        const multi = new Set(matched.map(e=>e.domain)).size;
        return Math.min(100, matched.length*7 + multi*12 +
          (matched[0]?(matched[0].severity_delta||0)*4:0));
      }
    },
    {
      id: 'civil_unrest',
      label: 'Гражданские волнения',
      threshold: 40,
      cooldown_hrs: 3,
      fn(events) {
        const KW = ['protest','unrest','civil','uprising','riot','coup','strike',
                    'протест','беспорядки','переворот','забастовка','восстание'];
        const matched = events.filter(e => KW.some(k => ((e.title||'')+(e.summary||'')).toLowerCase().includes(k)));
        const rising = matched.filter(e=>e.trend_direction==='rising').length;
        return Math.min(100, matched.length*9 + rising*8);
      }
    },
    {
      id: 'climate_migration',
      label: 'Климатическая миграция',
      threshold: 25,
      cooldown_hrs: 12,
      fn(events) {
        const climate = events.filter(e=>e.domain==='climate' && (e.escalation_score||0)>=25);
        const migration = events.filter(e => ['refugee','migrant','displacement','перемещ','беженц']
          .some(k=>((e.title||'')+(e.summary||'')).toLowerCase().includes(k)));
        const cascade = climate.filter(e=>(e.cascade||[]).includes('social')).length;
        return Math.min(100, cascade*18 + migration.length*10);
      }
    },
    {
      id: 'infrastructure_fragility',
      label: 'Уязвимость инфраструктуры',
      threshold: 35,
      cooldown_hrs: 6,
      fn(events) {
        const infra = events.filter(e => (e.vectors||[]).includes('infrastructure'));
        const cyber  = events.filter(e => (e.vectors||[]).includes('cyber'));
        const high   = [...infra,...cyber].filter(e=>(e.escalation_score||0)>=40);
        return Math.min(100, high.length*14 + infra.length*5 + cyber.length*7);
      }
    },
    {
      id: 'geopolitical_escalation',
      label: 'Геополитическая эскалация',
      threshold: 45,
      cooldown_hrs: 2,
      fn(events) {
        const geo = events.filter(e=>e.domain==='geopolitics');
        const kinetic = geo.filter(e=>(e.vectors||[]).includes('kinetic'));
        const high    = geo.filter(e=>e.escalation_level==='high'||e.escalation_level==='critical');
        const rising  = geo.filter(e=>e.trend_direction==='rising');
        return Math.min(100, kinetic.length*12 + high.length*10 + rising.length*6);
      }
    },
    {
      id: 'cyber_contagion',
      label: 'Кибер-распространение',
      threshold: 30,
      cooldown_hrs: 3,
      fn(events) {
        const cyber = events.filter(e=>(e.vectors||[]).includes('cyber'));
        const multi  = new Set(cyber.map(e=>e.domain)).size;
        const rising = cyber.filter(e=>e.trend_direction==='rising'||(e.severity_delta||0)>=3).length;
        return Math.min(100, cyber.length*8 + multi*12 + rising*9);
      }
    },
    {
      id: 'logistics_disruption',
      label: 'Логистические сбои',
      threshold: 25,
      cooldown_hrs: 6,
      fn(events) {
        const KW = ['logistics','shipping','supply chain','transport','port','freight',
                    'логистик','поставки','транспорт','порт','перевозк'];
        const matched = events.filter(e => KW.some(k => ((e.title||'')+(e.summary||'')).toLowerCase().includes(k)));
        const econ = events.filter(e=>e.domain==='economy'&&(e.cascade||[]).includes('social')).length;
        return Math.min(100, matched.length*9 + econ*8);
      }
    },
  ],

  // Cooldown tracking: {detector_id → last_fired_ts}
  _lastFired: {},

  // Anomaly memory: {detector_id → [score_history]}
  _memory: {},

  /**
   * Run all detectors against current events.
   * Returns: { detections, active_count, alert_level, escalation_delta }
   */
  run(events) {
    const now  = Date.now();
    const detections = [];

    this.DETECTORS.forEach(det => {
      // Cooldown check
      const lastFired = this._lastFired[det.id] || 0;
      const cooldownMs = det.cooldown_hrs * 3600000;
      const onCooldown = (now - lastFired) < cooldownMs;

      // Normalize score: divide raw by sqrt(event_count/20) to prevent inflation
      const rawScore = det.fn(events);
      const normFactor = Math.max(0.4, Math.min(1.0, Math.sqrt(20 / Math.max(1, events.length))));
      const score = Math.min(100, Math.round(rawScore * normFactor));

      // Update memory
      if (!this._memory[det.id]) this._memory[det.id] = [];
      this._memory[det.id].push(score);
      if (this._memory[det.id].length > 24) this._memory[det.id] = this._memory[det.id].slice(-24);

      const avg_hist = this._memory[det.id].length > 1
        ? this._memory[det.id].slice(0,-1).reduce((a,b)=>a+b,0) / (this._memory[det.id].length-1)
        : 0;

      // Baseline deviation
      const deviation = score - avg_hist;
      const firing    = score >= det.threshold && !onCooldown;
      const elevated  = score >= det.threshold * 0.7;

      if (firing) this._lastFired[det.id] = now;

      if (score >= det.threshold * 0.5) {
        detections.push({
          id:          det.id,
          label:       det.label,
          score,
          threshold:   det.threshold,
          firing:      firing && !onCooldown,
          on_cooldown: onCooldown && score >= det.threshold,
          elevated,
          baseline_avg: Math.round(avg_hist),
          deviation:   Math.round(deviation),
          confidence:  Math.min(1, score / 100),
        });
      }
    });

    const active = detections.filter(d => d.firing);
    const alertLevel = this._computeAlertLevel(active, detections);

    return {
      detections: detections.sort((a,b)=>b.score-a.score),
      active_count:   active.length,
      total_elevated: detections.filter(d=>d.elevated).length,
      alert_level:    alertLevel,
      top_detector:   active[0] || null,
    };
  },

  _computeAlertLevel(active, all) {
    const n = active.length;
    const maxScore = active.length ? Math.max(...active.map(d=>d.score)) : 0;
    if (n >= 5 || maxScore >= 90) return 'systemic_break';
    if (n >= 4 || maxScore >= 75) return 'cascading';
    if (n >= 3 || maxScore >= 60) return 'critical';
    if (n >= 2 || maxScore >= 40) return 'elevated';
    return 'monitor';
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// ADAPTIVE PRIORITY ENGINE v2
// Replaces static SignalPriority with dynamic formula:
// P = severity × synchronization × velocity × convergence × criticality × rarity
// ═══════════════════════════════════════════════════════════════════════════

IE.AdaptivePriority = {

  /**
   * Compute adaptive_priority for a single event.
   * All factors [0,1] → product × 100.
   */
  score(ev, context = {}) {
    const { convergence = {}, accelerationState = 'latent', domainPressure = {} } = context;

    const severity    = (ev.severity || 0) / 100;

    // Synchronization: domain is in convergence rising_domains?
    const rising_d    = convergence.rising_domains || [];
    const sync        = rising_d.includes(ev.domain) ? 1.0 : 0.4;

    // Velocity: severity_delta × phase multiplier (recency)
    // Fallback when trend_direction='new' (schema 2.1): use severity as proxy
    const phase_m     = {emerging:1.3,active:1.1,chronic:0.8,'de-escalating':0.5}[ev.phase] || 1.0;
    const hasTrend    = ev.trend_direction && ev.trend_direction !== 'new';
    const velocityRaw = hasTrend
      ? Math.max(0.15, ((ev.severity_delta||0)+3) / 18 * phase_m)
      : Math.max(0.20, (ev.severity||0) / 120 * phase_m);  // severity proxy
    const velocity    = Math.min(1, velocityRaw);

    // Convergence factor: convergence_index / 100, boosted for multi-cascade
    const ci          = (convergence.convergence_index || 0) / 100;
    const casc_bonus  = Math.min(0.3, (ev.cascade||[]).length * 0.1);
    const conv_factor = Math.min(1, ci * 0.7 + casc_bonus + 0.15);

    // Infrastructure criticality: vectors that affect critical systems
    const CRIT_VECS   = new Set(['cyber','infrastructure','kinetic','economic']);
    const crit_count  = (ev.vectors||[]).filter(v => CRIT_VECS.has(v)).length;
    const criticality = Math.min(1, 0.3 + crit_count * 0.25);

    // Rarity: signal_type anomaly/structural → rarer → higher score
    const RARITY_W    = {anomaly:1.0, structural:0.9, escalation:0.7, baseline:0.3};
    const rarity      = RARITY_W[ev.signal_type] || 0.5;

    // Acceleration multiplier
    const ACC_MULT    = {latent:1.0,accelerating:1.2,synchronized:1.35,cascading:1.5,nonlinear_break:1.7};
    const acc_mult    = ACC_MULT[accelerationState] || 1.0;

    const raw = severity * sync * velocity * conv_factor * criticality * rarity * acc_mult * 200;
    return Math.min(100, Math.round(raw));
  },

  classify(score, ev) {
    if (score >= 70) return 'systemic_critical';
    if (score >= 50 && (ev.cascade||[]).length >= 2) return 'cascade_trigger';
    if (score >= 50) return 'high_priority';
    if (score >= 30 && (ev.severity_delta||0) >= 3) return 'accelerating_precursor';
    if (score >= 20) return 'monitor';
    return 'low';
  },

  rank(events, context = {}, topN = 20) {
    return events
      .map(ev => {
        const s = this.score(ev, context);
        return { ...ev, adaptive_priority: s, adaptive_class: this.classify(s, ev) };
      })
      .sort((a, b) => b.adaptive_priority - a.adaptive_priority)
      .slice(0, topN);
  },
};

console.log('[IE] Detector Registry + Adaptive Priority loaded');
