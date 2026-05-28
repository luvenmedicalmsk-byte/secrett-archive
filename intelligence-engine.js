/**
 * Sovereign Intelligence Engine v3.0
 * Autonomous analytical layer — deterministic, no LLM inference.
 *
 * Modules:
 *   1. CausalEngine      — causal chains, amplifiers, propagation
 *   2. AccelerationEngine— systemic acceleration, nonlinear detection
 *   3. SynthesisEngine   — machine-generated intelligence assessments
 *   4. TemporalMemory    — snapshot diffing, regime drift, recurrence
 *   5. SignalPriority    — priority_score, TOP SYSTEMIC SIGNALS
 *   6. GraphAnalytics    — PageRank, betweenness, contagion centrality
 *   7. HorizonForecaster — 7/30/90/180d multi-horizon scenarios
 *   8. TrajectoryEngine  — country trajectory states + fragility scoring
 */

'use strict';

// ═══════════════════════════════════════════════════════════════════════════
// CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

const IE = window.IE = {};  // global namespace

IE.DOMAINS   = ['geopolitics','climate','economy','technology','social'];
IE.VECTORS   = ['kinetic','cyber','economic','environmental','political','infrastructure','social','informational'];
IE.DC = {climate:'#1E8449',economy:'#2471A3',geopolitics:'#C0622B',technology:'#7D3C98',social:'#B03A2E'};
IE.DL = {climate:'Климат',economy:'Экономика',geopolitics:'Геополитика',technology:'Технологии',social:'Социум'};

// Domain structural amplification weights (how much domain A amplifies domain B)
IE.AMP_MATRIX = {
  geopolitics: {economy:.70, social:.50, technology:.30, climate:.15},
  climate:     {social:.60, economy:.45, geopolitics:.25},
  economy:     {social:.65, geopolitics:.40, technology:.25},
  technology:  {economy:.55, infrastructure:.50, geopolitics:.25},
  social:      {geopolitics:.40, economy:.30},
};

// Contagion velocity weights by vector type
IE.VECTOR_VELOCITY = {
  cyber:.90, kinetic:.85, economic:.75, political:.65,
  infrastructure:.70, environmental:.50, social:.55, informational:.45,
};

// _causalMechanism defined here for reference by CausalEngine
IE._causalMechanism = function(src, dst, vectors) {
  const MECHS = {
    'geopolitics→economy': 'санкции/торговые ограничения',
    'geopolitics→social':  'принудительное перемещение',
    'geopolitics→technology': 'технологические ограничения',
    'climate→social':      'климатическое вытеснение',
    'climate→economy':     'продовольственный стресс',
    'economy→social':      'социально-экономический стресс',
    'economy→geopolitics': 'финансовые рычаги давления',
    'technology→economy':  'инфраструктурный сбой',
    'social→geopolitics':  'политическая дестабилизация',
  };
  return MECHS[`${src}→${dst}`] || `${IE.DL[src]||src} → ${IE.DL[dst]||dst}`;
};

IE._groupBy = (arr, key) => arr.reduce((acc, x) => {
  (acc[x[key]] = acc[x[key]] || []).push(x); return acc;
}, {});

IE._mode = arr => {
  const counts = arr.reduce((a,v)=>{a[v]=(a[v]||0)+1;return a;},{});
  return Object.entries(counts).sort((a,b)=>b[1]-a[1])[0]?.[0];
};

IE._domainAvg = events => {
  const r = {};
  IE.DOMAINS.forEach(d => {
    const sub = events.filter(e=>e.domain===d);
    r[d] = sub.length ? Math.round(sub.reduce((s,e)=>s+(e.escalation_score||0),0)/sub.length) : 0;
  });
  return r;
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 1 — CAUSAL ENGINE
// ═══════════════════════════════════════════════════════════════════════════

IE.CausalEngine = {

  /**
   * Derive causal chains from events.
   * Returns array of {src, dst, mechanism, weight, evidence}
   */
  deriveCausalChains(events) {
    const chains = [];
    const byDomain = IE._groupBy(events, 'domain');

    events.forEach(ev => {
      const src = ev.domain;
      if (!src) return;
      const esc   = ev.escalation_score || 0;
      const vecs  = ev.vectors || [];

      // Explicit cascade links
      (ev.cascade || []).forEach(dst => {
        const ampW = (IE.AMP_MATRIX[src] || {})[dst] || 0.2;
        const vel  = Math.max(...vecs.map(v => IE.VECTOR_VELOCITY[v] || 0.4), 0.4);
        chains.push({
          src, dst, type: 'cascade',
          mechanism: IE._causalMechanism(src, dst, vecs),
          weight: +(esc / 100 * ampW * vel).toFixed(3),
          evidence: (ev.title || '').slice(0, 60),
          fingerprint: ev.fingerprint || '',
        });
      });

      // Inferred structural links (high-severity events imply amplification)
      if (esc >= 55) {
        const structural = IE.AMP_MATRIX[src] || {};
        Object.entries(structural).forEach(([dst, baseW]) => {
          if (!(ev.cascade || []).includes(dst)) {
            const vel = Math.max(...vecs.map(v => IE.VECTOR_VELOCITY[v] || 0.4), 0.4);
            chains.push({
              src, dst, type: 'inferred',
              mechanism: IE._causalMechanism(src, dst, vecs),
              weight: +(esc / 100 * baseW * vel * 0.5).toFixed(3),
              evidence: `Structural: ${(ev.title || '').slice(0, 40)}`,
              fingerprint: ev.fingerprint || '',
            });
          }
        });
      }
    });

    // Aggregate duplicate pairs → sum weight, count
    const agg = {};
    chains.forEach(c => {
      const k = `${c.src}→${c.dst}:${c.type}`;
      if (!agg[k]) agg[k] = { ...c, count: 0, total_weight: 0, evidence_list: [] };
      agg[k].count++;
      agg[k].total_weight += c.weight;
      if (agg[k].evidence_list.length < 3) agg[k].evidence_list.push(c.evidence);
    });

    return Object.values(agg)
      .map(c => ({ ...c, weight: +(c.total_weight / c.count).toFixed(3) }))
      .sort((a, b) => b.weight - a.weight);
  },

  /**
   * Detect amplification vectors — vectors that appear in
   * multiple high-severity cross-domain events.
   */
  detectAmplifiers(events) {
    const vecStats = {};
    events.forEach(ev => {
      if ((ev.escalation_score || 0) < 25) return;
      (ev.vectors || []).forEach(v => {
        if (!vecStats[v]) vecStats[v] = { count: 0, total_esc: 0, domains: new Set(), cascade_count: 0 };
        vecStats[v].count++;
        vecStats[v].total_esc += ev.escalation_score || 0;
        vecStats[v].domains.add(ev.domain);
        vecStats[v].cascade_count += (ev.cascade || []).length;
      });
    });

    return Object.entries(vecStats)
      .map(([v, s]) => ({
        vector:         v,
        count:          s.count,
        avg_esc:        +(s.total_esc / s.count).toFixed(1),
        domain_spread:  s.domains.size,
        cascade_count:  s.cascade_count,
        amplification_score: +(
          (s.count * 0.3 + s.total_esc / s.count / 100 * 0.4 + s.domains.size / 5 * 0.3) * 100
        ).toFixed(1),
      }))
      .sort((a, b) => b.amplification_score - a.amplification_score);
  },

  /**
   * Infer propagation pathways — which domains will feel pressure next.
   * Returns {domain → pressure_score, mechanism, source_events}
   */
  inferPropagation(events) {
    const pressure = {};
    IE.DOMAINS.forEach(d => { pressure[d] = { score: 0, mechanisms: [], source_count: 0 }; });

    events.forEach(ev => {
      const src = ev.domain;
      const esc = ev.escalation_score || 0;
      if (esc < 20) return;
      const structural = IE.AMP_MATRIX[src] || {};
      Object.entries(structural).forEach(([dst, w]) => {
        if (!pressure[dst]) return;  // skip non-domain targets (e.g. 'infrastructure')
        const vel = Math.max(...(ev.vectors || []).map(v => IE.VECTOR_VELOCITY[v] || 0.4), 0.4);
        pressure[dst].score += esc * w * vel;
        pressure[dst].source_count++;
        const mech = IE._causalMechanism(src, dst, ev.vectors || []);
        if (!pressure[dst].mechanisms.includes(mech)) pressure[dst].mechanisms.push(mech);
      });
    });

    return Object.fromEntries(
      Object.entries(pressure).map(([d, p]) => [d, {
        domain: d,
        pressure_score: Math.min(100, Math.round(p.score / Math.max(1, p.source_count) * 0.8)),
        mechanisms:     p.mechanisms.slice(0, 3),
        source_count:   p.source_count,
      }])
    );
  },

  /**
   * Detect hidden dependencies — domain pairs with unexpectedly
   * high co-occurrence of escalating events.
   */
  detectHiddenDependencies(events) {
    const co = {};
    const esc_events = events.filter(e => (e.escalation_score || 0) >= 30);
    // Count co-occurrence via shared cascade targets
    esc_events.forEach(a => {
      esc_events.forEach(b => {
        if (a.id === b.id || a.domain === b.domain) return;
        const shared = (a.cascade || []).filter(c => (b.cascade || []).includes(c));
        if (shared.length) {
          const k = [a.domain, b.domain].sort().join('↔');
          if (!co[k]) co[k] = { pair: [a.domain, b.domain], count: 0, shared_targets: new Set() };
          co[k].count++;
          shared.forEach(s => co[k].shared_targets.add(s));
        }
      });
    });
    return Object.values(co)
      .map(c => ({ ...c, shared_targets: [...c.shared_targets] }))
      .filter(c => c.count >= 2)
      .sort((a, b) => b.count - a.count)
      .slice(0, 6);
  },

};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 2 — SYSTEMIC ACCELERATION ENGINE
// ═══════════════════════════════════════════════════════════════════════════

IE.AccelerationEngine = {

  /**
   * Compute acceleration state for the system.
   * States: latent | accelerating | synchronized | cascading | nonlinear_break
   */
  computeState(events, convergence = {}) {
    const metrics = this._computeMetrics(events);
    const state   = this._classifyState(metrics, convergence);
    const clusters = this._detectClusters(events);
    return { state, metrics, clusters };
  },

  _computeMetrics(events) {
    const n = events.length || 1;

    // Slope acceleration: avg severity_delta
    const deltas = events.map(e => e.severity_delta || 0);
    const avg_delta = deltas.reduce((a, b) => a + b, 0) / n;

    // Volatility: std of escalation_scores
    const scores = events.map(e => e.escalation_score || e.severity || 0);
    const avg_sc = scores.reduce((a, b) => a + b, 0) / n;
    const variance = scores.map(x => (x - avg_sc) ** 2).reduce((a, b) => a + b, 0) / n;
    const volatility = Math.sqrt(variance);

    // Convergence density: fraction of domains with rising trend
    const rising_domains = new Set(
      events.filter(e => e.trend_direction === 'rising').map(e => e.domain)
    ).size;
    const convergence_density = rising_domains / IE.DOMAINS.length;

    // Contagion velocity: events with cascade / total events
    const contagion_frac = events.filter(e => (e.cascade || []).length > 0).length / n;
    const contagion_velocity = +(contagion_frac * 100).toFixed(1);

    // Systemic synchronization: domains escalating simultaneously
    const domain_esc = {};
    IE.DOMAINS.forEach(d => {
      const sub = events.filter(e => e.domain === d);
      domain_esc[d] = sub.length ?
        sub.reduce((s, e) => s + (e.escalation_score || 0), 0) / sub.length : 0;
    });
    const esc_vals = Object.values(domain_esc);
    const esc_mean = esc_vals.reduce((a, b) => a + b, 0) / esc_vals.length;
    const sync_score = esc_vals.filter(v => v >= esc_mean * 0.75).length / esc_vals.length;

    return {
      slope_acceleration:   +avg_delta.toFixed(3),
      volatility:           +volatility.toFixed(2),
      convergence_density:  +convergence_density.toFixed(3),
      contagion_velocity,
      sync_score:           +sync_score.toFixed(3),
      avg_escalation:       +avg_sc.toFixed(1),
      domain_escalation:    domain_esc,
    };
  },

  _classifyState(metrics, convergence) {
    const ci = convergence.convergence_index || 0;
    const { slope_acceleration: sa, volatility: v, convergence_density: cd,
            contagion_velocity: cv, sync_score: ss } = metrics;

    if (ss >= 0.8 && cd >= 0.6 && cv >= 25) return 'nonlinear_break';
    if (ss >= 0.6 && (ci >= 60 || cd >= 0.5)) return 'cascading';
    if (cd >= 0.4 && cv >= 20)               return 'synchronized';
    if (sa >= 1.5 || v >= 12)                return 'accelerating';
    return 'latent';
  },

  _detectClusters(events) {
    // Clusters = domains where >=2 events have severity_delta > 0 AND phase=emerging/active
    const clusters = {};
    events.filter(e => (e.severity_delta || 0) >= 0 && ['active','emerging'].includes(e.phase))
      .forEach(e => {
        const d = e.domain;
        if (!clusters[d]) clusters[d] = { domain: d, count: 0, avg_delta: 0, max_esc: 0 };
        clusters[d].count++;
        clusters[d].avg_delta += e.severity_delta || 0;
        clusters[d].max_esc = Math.max(clusters[d].max_esc, e.escalation_score || 0);
      });
    return Object.values(clusters)
      .map(c => ({ ...c, avg_delta: +(c.avg_delta / c.count).toFixed(2) }))
      .filter(c => c.count >= 3)
      .sort((a, b) => b.count - a.count);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 3 — SYNTHESIS ENGINE (deterministic intelligence assessment)
// ═══════════════════════════════════════════════════════════════════════════

IE.SynthesisEngine = {

  REGIME_NARRATIVES: {
    stable:       'Система находится в устойчивом состоянии. Наблюдаемые риски не превышают базовый уровень.',
    deteriorating:'Фиксируется нарастание давления по ключевым доменам. Признаков структурного разрыва нет, но тенденция требует мониторинга.',
    unstable:     'Система демонстрирует нестабильность с расширяющейся волатильностью. Вероятность ускорения умеренная.',
    transition:   'Система находится в переходном состоянии. Зафиксировано ускорение slope и рост convergence. Вероятность нелинейного перехода повышена.',
    nonlinear:    'ВНИМАНИЕ: Система вышла из линейного режима. Зафиксировано синхронное ускорение в множестве доменов. Структурный разрыв вероятен.',
  },

  ACC_NARRATIVES: {
    latent:          'Латентные процессы: нарастание не обнаружено.',
    accelerating:    'Обнаружено ускорение: slope acceleration превышает пороговое значение.',
    synchronized:    'Синхронизированная эскалация: несколько доменов нарастают параллельно.',
    cascading:       'Каскадное нарастание: активные contagion paths между доменами.',
    nonlinear_break: 'НЕЛИНЕЙНЫЙ РАЗРЫВ: Система перешла в нелинейный режим.',
  },

  /**
   * Generate full intelligence assessment.
   * Returns structured text blocks — no LLM, pure template synthesis.
   */
  generateAssessment(events, regime, convergence, acceleration, causalChains, weakSignals, patterns) {
    const topDomains     = this._topDomains(events);
    const topVectors     = this._topVectors(events);
    const amplifiers     = causalChains.slice(0, 3);
    const unstableRegions = this._unstableRegions(events);
    const regimeState    = (regime && regime.state) || 'stable';
    const accState       = (acceleration && acceleration.state) || 'latent';
    const ci             = (convergence && convergence.convergence_index) || 0;
    const wsCluster      = weakSignals && weakSignals.cluster && weakSignals.cluster.cluster_level;
    const analogIds      = patterns && patterns.pattern_matches || [];

    const blocks = {

      executive_summary: this._buildExecutiveSummary(
        regimeState, accState, ci, topDomains, topVectors, wsCluster
      ),

      escalation_assessment: this._buildEscalationAssessment(
        events, topDomains, amplifiers, causalChains
      ),

      regime_interpretation: this._buildRegimeInterpretation(
        regimeState, regime, accState
      ),

      dominant_risks: this._buildDominantRisks(events, topDomains),

      unstable_regions: this._buildUnstableRegions(unstableRegions),

      contagion_vectors: this._buildContagionVectors(topVectors, causalChains),

      hidden_signals: this._buildHiddenSignals(weakSignals, analogIds),

      generated_at: new Date().toISOString(),
    };

    return blocks;
  },

  _buildExecutiveSummary(regime, acc, ci, topDomains, topVectors, wsCluster) {
    const intensity = ci >= 70 ? 'критическая' : ci >= 45 ? 'умеренная' : 'низкая';
    const topD = topDomains.slice(0, 2).map(d => IE.DL[d] || d).join(' и ');
    const topV = topVectors.slice(0, 2).join(', ');

    let summary = `Текущий системный режим: ${IE.SynthesisEngine.REGIME_NARRATIVES[regime] || regime}`;

    if (acc !== 'latent') {
      summary += ` ${IE.SynthesisEngine.ACC_NARRATIVES[acc]}`;
    }
    if (ci > 30) {
      summary += ` Уровень конвергенции — ${intensity} (индекс ${ci}).`;
    }
    if (topD) {
      summary += ` Доминирующие домены риска: ${topD}.`;
    }
    if (topV) {
      summary += ` Активные векторы воздействия: ${topV}.`;
    }
    if (wsCluster && wsCluster !== 'none') {
      summary += ` Кластер слабых сигналов: уровень ${wsCluster.toUpperCase()}.`;
    }
    return summary.trim();
  },

  _buildEscalationAssessment(events, topDomains, amplifiers, chains) {
    const esc_events = events.filter(e => e.signal_type === 'escalation');
    const mod_plus   = events.filter(e => ['moderate','high','critical'].includes(e.escalation_level));
    const lines = [];

    lines.push(`Событий с типом 'escalation': ${esc_events.length} из ${events.length} (${Math.round(esc_events.length/Math.max(1,events.length)*100)}%).`);

    if (mod_plus.length) {
      lines.push(`Событий умеренного и выше уровня: ${mod_plus.length}.`);
    }

    if (amplifiers.length) {
      const amp = amplifiers[0];
      lines.push(`Ведущий каскадный путь: ${IE.DL[amp.src]||amp.src} → ${IE.DL[amp.dst]||amp.dst} (механизм: ${amp.mechanism}).`);
    }

    const structural = events.filter(e => e.signal_type === 'structural');
    if (structural.length) {
      lines.push(`Структурные риски: ${structural.length} долгосрочных уязвимостей зафиксировано.`);
    }

    return lines.join(' ');
  },

  _buildRegimeInterpretation(regimeState, regime, accState) {
    const base = IE.SynthesisEngine.REGIME_NARRATIVES[regimeState] || 'Состояние не определено.';
    const conf = regime && regime.confidence ? `Уверенность оценки: ${Math.round(regime.confidence*100)}%.` : '';
    const bp   = regime && regime.systemic_break_probability
      ? `Вероятность системного разрыва: ${Math.round(regime.systemic_break_probability*100)}%.`
      : '';
    const acc  = accState !== 'latent' ? IE.SynthesisEngine.ACC_NARRATIVES[accState] : '';
    return [base, conf, bp, acc].filter(Boolean).join(' ');
  },

  _buildDominantRisks(events, topDomains) {
    return topDomains.slice(0, 3).map(d => {
      const sub = events.filter(e => e.domain === d);
      const avg = sub.length ? Math.round(sub.reduce((s, e) => s + (e.escalation_score||0), 0) / sub.length) : 0;
      const top = sub.sort((a,b)=>(b.escalation_score||0)-(a.escalation_score||0))[0];
      return {
        domain:      d,
        label:       IE.DL[d] || d,
        event_count: sub.length,
        avg_score:   avg,
        top_signal:  top ? (top.title||'').slice(0,70) : '',
      };
    });
  },

  _buildUnstableRegions(regions) {
    return regions.slice(0, 5).map(r => ({
      region: r.region,
      event_count: r.count,
      max_severity: r.max_sev,
      domains: r.domains,
    }));
  },

  _buildContagionVectors(topVectors, chains) {
    return topVectors.slice(0, 4).map(v => {
      const related = chains.filter(c => c.mechanism && c.mechanism.includes(v));
      return { vector: v, chain_count: related.length,
        top_path: related[0] ? `${IE.DL[related[0].src]||related[0].src} → ${IE.DL[related[0].dst]||related[0].dst}` : '' };
    });
  },

  _buildHiddenSignals(weakSignals, analogIds) {
    const ws   = weakSignals && weakSignals.signals ? weakSignals.signals.filter(s=>s.probability>=.35) : [];
    const anl  = analogIds.slice(0, 2);
    const lines = [];
    if (ws.length)  lines.push(`Активных прекурсорных сигналов: ${ws.length} (${ws.map(s=>s.type).join(', ')}).`);
    if (anl.length) lines.push(`Исторические аналоги: ${anl.join(', ')}.`);
    return lines.join(' ') || 'Скрытые сигналы не обнаружены.';
  },

  _topDomains(events) {
    const d = {};
    events.forEach(e => { if (e.domain) d[e.domain] = (d[e.domain]||0) + (e.escalation_score||e.severity||0); });
    return Object.entries(d).sort((a,b)=>b[1]-a[1]).map(([k])=>k);
  },

  _topVectors(events) {
    const v = {};
    events.forEach(e => (e.vectors||[]).forEach(vec => v[vec] = (v[vec]||0)+1));
    return Object.entries(v).sort((a,b)=>b[1]-a[1]).map(([k])=>k);
  },

  _unstableRegions(events) {
    const r = {};
    events.filter(e => (e.escalation_score||0) >= 25).forEach(e => {
      const region = (e.region||'').split('·')[0].trim().slice(0, 24) || 'Глобально';
      if (!r[region]) r[region] = { region, count:0, max_sev:0, domains:new Set() };
      r[region].count++;
      r[region].max_sev = Math.max(r[region].max_sev, e.severity||0);
      r[region].domains.add(e.domain);
    });
    return Object.values(r)
      .map(x => ({ ...x, domains: [...x.domains] }))
      .sort((a,b) => b.count - a.count);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 4 — TEMPORAL MEMORY
// ═══════════════════════════════════════════════════════════════════════════

IE.TemporalMemory = {

  _snapshots: [],   // [{ts, events_summary, regime, gri, convergence_index}]
  MAX_SNAPSHOTS: 48,

  /** Store current snapshot in memory (session-only, KV backed externally) */
  record(events, regime, gri, convergence) {
    const summary = {
      ts:               new Date().toISOString(),
      event_count:      events.length,
      avg_esc:          Math.round(events.reduce((s,e)=>s+(e.escalation_score||0),0)/Math.max(1,events.length)),
      critical_count:   events.filter(e=>e.escalation_level==='critical').length,
      high_count:       events.filter(e=>e.escalation_level==='high').length,
      regime_state:     regime && regime.state,
      gri_index:        gri && gri.index,
      convergence_index: convergence && convergence.convergence_index,
      domain_avg:       IE._domainAvg(events),
    };
    this._snapshots.push(summary);
    if (this._snapshots.length > this.MAX_SNAPSHOTS)
      this._snapshots = this._snapshots.slice(-this.MAX_SNAPSHOTS);
    return summary;
  },

  /** Compare current with previous snapshot */
  diff(events, regime) {
    if (this._snapshots.length < 2) return null;
    const prev = this._snapshots[this._snapshots.length - 2];
    const curr = this._snapshots[this._snapshots.length - 1];
    const dGRI  = (curr.gri_index||0) - (prev.gri_index||0);
    const dConv = (curr.convergence_index||0) - (prev.convergence_index||0);
    const dEsc  = curr.avg_esc - prev.avg_esc;
    return {
      delta_gri:          dGRI,
      delta_convergence:  dConv,
      delta_avg_esc:      dEsc,
      regime_changed:     curr.regime_state !== prev.regime_state,
      prev_regime:        prev.regime_state,
      curr_regime:        curr.regime_state,
      deteriorating:      dGRI > 0 || dConv > 5,
    };
  },

  /** Detect regime drift over rolling window */
  regimeDrift() {
    if (this._snapshots.length < 4) return { drifting: false };
    const recent = this._snapshots.slice(-8);
    const states = recent.map(s => s.regime_state).filter(Boolean);
    const stable_count = states.filter(s => s === 'stable').length;
    const drifted = stable_count < states.length * 0.5;
    return {
      drifting:       drifted,
      dominant_state: IE._mode(states),
      state_history:  states,
    };
  },

  /** Get trend series for a metric */
  series(metric) {
    return this._snapshots.map(s => ({ ts: s.ts, value: s[metric] || 0 }));
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 5 — SIGNAL PRIORITY ENGINE
// ═══════════════════════════════════════════════════════════════════════════

IE.SignalPriority = {

  /**
   * Priority score formula:
   * P = severity×0.20 + convergence_weight×0.18 + acceleration_weight×0.18
   *   + cascade_weight×0.16 + recurrence_weight×0.14 + cross_domain_factor×0.14
   */
  score(ev, convergence = {}, domainPressure = {}) {
    const sev      = (ev.severity || 0) / 100;
    const esc      = (ev.escalation_score || 0) / 100;
    const delta    = Math.max(0, (ev.severity_delta || 0)) / 15;

    // Convergence weight: is domain in active/rising convergence?
    const rising_d = convergence.rising_domains || [];
    const conv_w   = rising_d.includes(ev.domain) ? 1 : 0.3;

    // Acceleration weight: severity_delta + phase
    const phase_mult = {emerging:1.2,active:1.0,chronic:0.8,'de-escalating':0.5}[ev.phase] || 1.0;
    const accel_w    = delta * phase_mult;

    // Cascade weight: number of cascade targets × avg weight
    const casc_w = Math.min(1, (ev.cascade||[]).length * 0.3);

    // Recurrence: count_7d / 20
    const rec_w = Math.min(1, (ev.count_7d || 0) / 20);

    // Cross-domain factor: signal_type + vectors spread
    const vspread = new Set((ev.vectors||[]).map(v => IE.VECTORS.indexOf(v) >= 0 ? 'known' : 'unk')).size;
    const cross_d = ((ev.signal_type === 'escalation' ? 1.0 : 0.6)
                   + (vspread > 1 ? 0.4 : 0)) / 1.4;

    // Pressure from domain
    const dom_p = (domainPressure[ev.domain] || {}).pressure_score || 0;
    const dom_w = dom_p / 100;

    const raw = (
      sev    * 0.20 +
      conv_w * 0.18 +
      accel_w * 0.18 +
      casc_w  * 0.16 +
      rec_w   * 0.14 +
      cross_d * 0.14
    ) * 100 + dom_w * 8;

    return Math.min(100, Math.round(raw));
  },

  /** Classify signal: critical_system | precursor | systemic_trigger | noise */
  classify(ev, score) {
    if (score >= 65 && ev.signal_type === 'escalation') return 'critical_system';
    if (score >= 40 && (ev.severity_delta||0) >= 3)    return 'precursor';
    if (score >= 45 && (ev.cascade||[]).length >= 2)    return 'systemic_trigger';
    if (score < 20)                                      return 'noise';
    return 'monitor';
  },

  /** Rank events, return top N with priority_score + classification */
  rank(events, convergence = {}, domainPressure = {}, topN = 20) {
    return events
      .map(ev => {
        const ps = this.score(ev, convergence, domainPressure);
        return { ...ev, priority_score: ps, signal_class: this.classify(ev, ps) };
      })
      .sort((a, b) => b.priority_score - a.priority_score)
      .slice(0, topN);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 6 — GRAPH ANALYTICS
// ═══════════════════════════════════════════════════════════════════════════

IE.GraphAnalytics = {

  /** Build adjacency list from causal chains */
  buildAdj(causalChains, events) {
    const adj = {};
    IE.DOMAINS.forEach(d => adj[`dom:${d}`] = []);

    causalChains.forEach(c => {
      const src = `dom:${c.src}`, dst = `dom:${c.dst}`;
      if (!adj[src]) adj[src] = [];
      adj[src].push({ id: dst, w: c.weight });
    });

    // Add vector nodes
    const vecCounts = {};
    events.forEach(ev => (ev.vectors||[]).forEach(v => {
      const vid = `vec:${v}`;
      if (!adj[vid]) adj[vid] = [];
      const did = `dom:${ev.domain}`;
      adj[vid].push({ id: did, w: (ev.escalation_score||30)/100 });
      vecCounts[vid] = (vecCounts[vid]||0)+1;
    }));

    return adj;
  },

  /** Iterative PageRank (20 iter, d=0.85) */
  pagerank(adj, iterations = 20, d = 0.85) {
    const nodes = Object.keys(adj);
    const n = nodes.length;
    if (!n) return {};
    let rank = Object.fromEntries(nodes.map(k => [k, 1/n]));
    for (let it = 0; it < iterations; it++) {
      const next = Object.fromEntries(nodes.map(k => [k, (1-d)/n]));
      nodes.forEach(src => {
        const out = adj[src] || [];
        const tw  = out.reduce((s, e) => s + (e.w||1), 0) || 1;
        out.forEach(e => {
          if (next[e.id] !== undefined)
            next[e.id] += d * rank[src] * (e.w||1) / tw;
        });
      });
      rank = next;
    }
    return Object.fromEntries(Object.entries(rank).map(([k,v])=>[k, +v.toFixed(5)]));
  },

  /** Approximate betweenness via BFS from each node (O(V*E)) */
  betweenness(adj) {
    const nodes = Object.keys(adj);
    const bc = Object.fromEntries(nodes.map(k=>[k,0]));
    nodes.forEach(src => {
      const dist = {[src]:0}, sigma = {[src]:1};
      const pred = {}, queue = [src], order = [];
      while (queue.length) {
        const v = queue.shift();
        order.push(v);
        (adj[v]||[]).forEach(({id:w}) => {
          if (dist[w] === undefined) { dist[w] = dist[v]+1; queue.push(w); }
          if (dist[w] === dist[v]+1) {
            sigma[w] = (sigma[w]||0) + (sigma[v]||0);
            if (!pred[w]) pred[w]=[];
            pred[w].push(v);
          }
        });
      }
      const delta = {};
      while (order.length) {
        const w = order.pop();
        (pred[w]||[]).forEach(v => {
          delta[v] = (delta[v]||0) + ((sigma[v]||0)/(sigma[w]||1)) * (1+(delta[w]||0));
        });
        if (w !== src) bc[w] = (bc[w]||0) + (delta[w]||0);
      }
    });
    const norm = Math.max(...Object.values(bc)) || 1;
    return Object.fromEntries(Object.entries(bc).map(([k,v])=>[k,+(v/norm).toFixed(4)]));
  },

  /** Contagion centrality = PageRank × cascade_weight */
  contagionCentrality(pr, causalChains) {
    const cc = { ...pr };
    causalChains.forEach(c => {
      const k = `dom:${c.dst}`;
      if (cc[k] !== undefined) cc[k] = +(cc[k] * (1 + c.weight)).toFixed(5);
    });
    return cc;
  },

  /** Identify chokepoints: high betweenness, moderate PR */
  chokepoints(pr, bt, n = 5) {
    return Object.keys(pr)
      .map(k => ({
        id: k,
        label: k.replace(/^(dom:|vec:)/,''),
        pagerank: pr[k]||0, betweenness: bt[k]||0,
        chokepoint_score: +((bt[k]||0)*0.6 + (pr[k]||0)*0.4).toFixed(4),
      }))
      .sort((a,b) => b.chokepoint_score - a.chokepoint_score)
      .slice(0, n);
  },

  /** Weak bridge detection: low PR, non-zero betweenness */
  weakBridges(pr, bt) {
    const prVals = Object.values(pr), prMean = prVals.reduce((a,b)=>a+b,0)/Math.max(1,prVals.length);
    return Object.keys(pr)
      .filter(k => (pr[k]||0) < prMean && (bt[k]||0) > 0.05)
      .map(k => ({ id: k, label: k.replace(/^(dom:|vec:)/,''), pagerank: pr[k], betweenness: bt[k] }))
      .sort((a,b)=>b.betweenness-a.betweenness).slice(0,4);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 7 — MULTI-HORIZON FORECASTER
// ═══════════════════════════════════════════════════════════════════════════

IE.HorizonForecaster = {

  HORIZONS: [7, 30, 90, 180],

  SCENARIOS: {
    stabilization:          { label: 'Стабилизация',         color: '#1E8449' },
    controlled_escalation:  { label: 'Управляемая эскалация', color: '#B8973A' },
    systemic_fragmentation: { label: 'Системная фрагментация', color: '#C0622B' },
    nonlinear_disruption:   { label: 'Нелинейный сбой',       color: '#7B241C' },
  },

  /**
   * Compute scenario probabilities for each horizon.
   * Uses: avg escalation, convergence_index, regime_state, acc_state, break_prob
   */
  forecast(events, regime, convergence, accEngine) {
    const avgEsc   = events.reduce((s,e)=>s+(e.escalation_score||0),0) / Math.max(1,events.length);
    const ci       = (convergence && convergence.convergence_index) || 0;
    const regSt    = (regime && regime.state) || 'stable';
    const breakP   = (regime && regime.systemic_break_probability) || 0.1;
    const accState = (accEngine && accEngine.state) || 'latent';

    const out = {};
    this.HORIZONS.forEach(h => {
      const decay = Math.pow(0.82, h/30);   // confidence decays with horizon
      const p = this._baseProbs(avgEsc, ci, regSt, breakP, accState);
      out[h] = {
        horizon_days: h,
        scenarios:    this._applyDecay(p, decay),
        confidence:   +(decay * 0.9).toFixed(2),
        uncertainty:  +(1 - decay * 0.9).toFixed(2),
        divergence:   this._divergence(p, decay),
      };
    });
    return out;
  },

  _baseProbs(avgEsc, ci, regSt, breakP, accState) {
    // Base priors
    let stable = 0.45, ctrl = 0.30, frag = 0.15, nonlin = 0.10;

    // Adjust by regime
    const RAdjust = {
      stable:       { stable:.3,  ctrl:-.1, frag:-.1,  nonlin:-.1 },
      deteriorating:{ stable:-.1, ctrl:.1,  frag:.0,   nonlin:.0  },
      unstable:     { stable:-.2, ctrl:.0,  frag:.15,  nonlin:.05 },
      transition:   { stable:-.3, ctrl:-.1, frag:.2,   nonlin:.2  },
      nonlinear:    { stable:-.35,ctrl:-.15,frag:.1,   nonlin:.4  },
    };
    const r = RAdjust[regSt] || RAdjust.stable;
    stable += r.stable; ctrl += r.ctrl; frag += r.frag; nonlin += r.nonlin;

    // Adjust by convergence
    if (ci >= 60) { stable -= .1; frag += .05; nonlin += .05; }
    if (ci >= 80) { stable -= .15; nonlin += .1; }

    // Adjust by acceleration
    if (accState === 'cascading')       { frag += .1; nonlin += .05; stable -= .1; }
    if (accState === 'nonlinear_break') { frag += .1; nonlin += .2; stable -= .2; }

    // Direct break probability
    nonlin = Math.max(nonlin, breakP * 0.8);

    // Normalize
    const tot = stable + ctrl + frag + nonlin;
    return {
      stabilization:         +(stable/tot).toFixed(3),
      controlled_escalation: +(ctrl/tot).toFixed(3),
      systemic_fragmentation:+(frag/tot).toFixed(3),
      nonlinear_disruption:  +(nonlin/tot).toFixed(3),
    };
  },

  _applyDecay(probs, decay) {
    // Uncertainty entropy: decay pushes distribution toward uniform
    const uniform = 0.25;
    return Object.fromEntries(
      Object.entries(probs).map(([k,v]) => [k, +(v*decay + uniform*(1-decay)).toFixed(3)])
    );
  },

  _divergence(probs, decay) {
    // KL divergence from uniform as proxy for forecast confidence
    const uniform = 0.25;
    let kl = 0;
    Object.values(probs).forEach(p => {
      if (p > 1e-9) kl += p * Math.log(p / uniform);
    });
    return +((kl * decay)).toFixed(4);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// MODULE 8 — COUNTRY TRAJECTORY ENGINE
// ═══════════════════════════════════════════════════════════════════════════

IE.TrajectoryEngine = {

  TRAJECTORIES: {
    stable:        { label: 'Стабильный',   color: '#1E8449', min: 0,  max: 20 },
    pressured:     { label: 'Под давлением',color: '#2471A3', min: 20, max: 35 },
    fragile:       { label: 'Хрупкий',      color: '#B8973A', min: 35, max: 50 },
    deteriorating: { label: 'Деградация',   color: '#C0622B', min: 50, max: 65 },
    unstable:      { label: 'Нестабильный', color: '#A93226', min: 65, max: 80 },
    systemic_risk: { label: 'Системный риск',color: '#7B241C', min: 80, max:100 },
  },

  /** Compute trajectory state and sub-scores for a country profile */
  computeTrajectory(profile) {
    if (!profile || !profile.found) return null;

    const risk     = profile.risk_score || 0;
    const signals  = profile.signal_count || 0;
    const hot      = (profile.escalation_hotspots || []).length;
    const structs  = (profile.structural_risks || []).length;
    const cascade  = (profile.cascade_exposure || []).length;
    const domBreak = profile.domain_breakdown || {};

    // Resilience: inverse of fragility indicators
    const resilience = Math.max(0, Math.round(
      100 - risk * 0.5 - hot * 8 - cascade * 6 - structs * 5
    ));

    // Fragility: structural + cascade exposure
    const fragility = Math.min(100, Math.round(
      structs * 15 + cascade * 10 + hot * 12 + risk * 0.3
    ));

    // Exposure: cascade incoming
    const exposure = Math.min(100, cascade * 20 + risk * 0.4);

    // Convergence exposure: domains in active escalation
    const conv_exp = Math.min(100, Object.values(domBreak).filter(d=>(d.score||d.avg||0)>=40).length * 20);

    // Trajectory state
    let traj = 'stable';
    for (const [k, v] of Object.entries(this.TRAJECTORIES)) {
      if (risk >= v.min && risk < v.max) { traj = k; break; }
    }
    if (risk >= 80) traj = 'systemic_risk';

    return {
      iso3:         profile.iso3,
      name_ru:      profile.name_ru || profile.name,
      risk_score:   risk,
      trajectory:   traj,
      resilience,
      fragility,
      exposure:     Math.round(exposure),
      convergence_exposure: conv_exp,
    };
  },

  /** Compute trajectories for all profiles, return sorted by risk */
  computeAll(profiles) {
    if (!profiles) return [];
    const items = Object.values(profiles).map(p => this.computeTrajectory(p)).filter(Boolean);
    return items.sort((a,b)=>b.risk_score-a.risk_score);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// PIPELINE ORCHESTRATOR
// ═══════════════════════════════════════════════════════════════════════════

IE.Pipeline = {

  _cache: null,
  _ts: 0,

  /** Run full analytics pipeline. Cached for 60s. */
  run(events, snapshotData = {}) {
    const now = Date.now();
    if (this._cache && now - this._ts < 60000) return this._cache;

    const { regime, convergence, weak_signals, patterns, country_profiles } = snapshotData;

    // Stage 1: Causal
    const causalChains    = IE.CausalEngine.deriveCausalChains(events);
    const amplifiers      = IE.CausalEngine.detectAmplifiers(events);
    const propagation     = IE.CausalEngine.inferPropagation(events);
    const hiddenDeps      = IE.CausalEngine.detectHiddenDependencies(events);

    // Stage 2: Acceleration
    const acceleration    = IE.AccelerationEngine.computeState(events, convergence || {});

    // Stage 3: Signal Priority
    const prioritized     = IE.SignalPriority.rank(events, convergence || {}, propagation);

    // Stage 4: Graph Analytics
    const adj  = IE.GraphAnalytics.buildAdj(causalChains, events);
    const pr   = IE.GraphAnalytics.pagerank(adj);
    const bt   = IE.GraphAnalytics.betweenness(adj);
    const cc   = IE.GraphAnalytics.contagionCentrality(pr, causalChains);
    const chokepoints  = IE.GraphAnalytics.chokepoints(pr, bt);
    const weakBridges  = IE.GraphAnalytics.weakBridges(pr, bt);

    // Stage 5: Horizon Forecast
    const horizonForecast = IE.HorizonForecaster.forecast(events, regime, convergence, acceleration);

    // Stage 6: Country Trajectories
    const trajectories    = IE.TrajectoryEngine.computeAll(country_profiles || {});

    // Stage 7: Synthesis
    const assessment      = IE.SynthesisEngine.generateAssessment(
      events, regime, convergence, acceleration, causalChains, weak_signals, patterns
    );

    // Stage 8: Temporal memory
    IE.TemporalMemory.record(events, regime, snapshotData.global_risk_index, convergence);
    const diff    = IE.TemporalMemory.diff(events, regime);
    const drift   = IE.TemporalMemory.regimeDrift();

    this._cache = {
      causalChains, amplifiers, propagation, hiddenDeps,
      acceleration, prioritized,
      graph: { pr, bt, cc, chokepoints, weakBridges },
      horizonForecast, trajectories, assessment,
      temporal: { diff, drift },
      ts: new Date().toISOString(),
    };
    this._ts = now;
    return this._cache;
  },
};



console.log('[IE] Sovereign Intelligence Engine v3.0 loaded');
