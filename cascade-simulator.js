/**
 * Cascade Simulation Engine v4
 * Forward propagation simulator with time delays, resilience dampening,
 * systemic stress amplification.
 *
 * Algorithm: iterative BFS through domain graph, applying
 * propagation weights and decay per time step.
 */

'use strict';

IE.CascadeSimulator = {

  // Propagation time delays (days) between domain pairs
  DELAYS: {
    'geopolitics→economy':    1,
    'geopolitics→social':     3,
    'geopolitics→technology': 5,
    'climate→social':         7,
    'climate→economy':        14,
    'economy→social':         3,
    'economy→geopolitics':    7,
    'technology→economy':     2,
    'social→geopolitics':     5,
  },

  // Resilience dampening per domain (higher = faster absorption)
  RESILIENCE: {
    geopolitics: 0.12,
    climate:     0.06,   // climate cascades decay slowly
    economy:     0.14,
    technology:  0.16,
    social:      0.10,
  },

  // Stress amplification: if domain already stressed, cascade hits harder
  STRESS_AMP: {
    geopolitics: 1.25,
    climate:     1.15,
    economy:     1.30,
    technology:  1.20,
    social:      1.20,
  },

  /**
   * Simulate forward propagation from seed events.
   * Returns timeline: [{day, domain, esc_delta, source_domain, mechanism}]
   */
  simulate(seedEvents, horizonDays = 30) {
    const domainBaseline = this._domainBaseline(seedEvents);
    const timeline = [];
    const domainStress = { ...domainBaseline };

    // Seed: add direct cascade from high-severity events
    const seeds = seedEvents.filter(e =>
      (e.escalation_score || 0) >= 30 && (e.cascade || []).length > 0
    );

    // BFS queue: {src_domain, dst_domain, esc_pressure, day}
    const queue = [];
    seeds.forEach(e => {
      (e.cascade || []).forEach(dst => {
        const delay = this.DELAYS[`${e.domain}→${dst}`] || 3;
        queue.push({
          src: e.domain, dst, day: delay,
          pressure: (e.escalation_score || 0) * (IE.AMP_MATRIX[e.domain]?.[dst] || 0.3),
          depth: 0,
        });
      });
    });

    // Process queue chronologically
    queue.sort((a, b) => a.day - b.day);

    while (queue.length && queue[0].day <= horizonDays) {
      const item = queue.shift();
      if (item.depth > 3) continue; // max chain depth

      const amp     = this.STRESS_AMP[item.dst] || 1.0;
      const damp    = this.RESILIENCE[item.dst]  || 0.12;
      const stress  = domainStress[item.dst] || 0;

      // Effective pressure: amplified by existing stress, dampened by resilience
      const effective = item.pressure * amp * (1 + stress / 100) * (1 - damp * item.depth);
      if (effective < 2) continue;

      const delta = Math.round(Math.min(25, effective));
      domainStress[item.dst] = Math.min(100, (domainStress[item.dst] || 0) + delta);

      timeline.push({
        day:           item.day,
        domain:        item.dst,
        esc_delta:     delta,
        cumulative:    domainStress[item.dst],
        source_domain: item.src,
        mechanism:     IE._causalMechanism ? IE._causalMechanism(item.src, item.dst, []) : `${item.src}→${item.dst}`,
        depth:         item.depth,
      });

      // Secondary cascades
      const secondaryCascades = Object.entries(IE.AMP_MATRIX[item.dst] || {});
      secondaryCascades.forEach(([dst2, w]) => {
        const delay2 = this.DELAYS[`${item.dst}→${dst2}`] || 4;
        queue.push({
          src: item.dst, dst: dst2,
          day: item.day + delay2,
          pressure: delta * w * 0.6,
          depth: item.depth + 1,
        });
      });

      queue.sort((a, b) => a.day - b.day);
    }

    return {
      timeline:       timeline.sort((a, b) => a.day - b.day),
      final_stress:   domainStress,
      peak_domains:   this._peakDomains(timeline, domainStress),
      chain_depth:    Math.max(...timeline.map(t => t.depth), 0),
      total_events:   timeline.length,
    };
  },

  /**
   * Quick scenario comparison: baseline vs stress scenario.
   */
  compareScenarios(seedEvents) {
    const baseline = this.simulate(seedEvents.filter(e => (e.escalation_score||0) < 50), 30);
    const stressed = this.simulate(seedEvents, 30);

    const delta = {};
    IE.DOMAINS.forEach(d => {
      delta[d] = (stressed.final_stress[d] || 0) - (baseline.final_stress[d] || 0);
    });

    return {
      baseline_stress:    baseline.final_stress,
      stressed_stress:    stressed.final_stress,
      delta_stress:       delta,
      highest_delta:      Object.entries(delta).sort((a,b)=>b[1]-a[1])[0],
      amplification_factor: stressed.total_events / Math.max(1, baseline.total_events),
    };
  },

  _domainBaseline(events) {
    const db = {};
    IE.DOMAINS.forEach(d => {
      const sub = events.filter(e => e.domain === d);
      db[d] = sub.length ? Math.round(sub.reduce((s,e)=>s+(e.escalation_score||0),0)/sub.length) : 0;
    });
    return db;
  },

  _peakDomains(timeline, finalStress) {
    return Object.entries(finalStress)
      .filter(([,v]) => v >= 30)
      .sort((a,b)=>b[1]-a[1])
      .slice(0,3)
      .map(([d,v]) => ({ domain:d, stress:v, label: IE.DL[d]||d }));
  },
};

// Expose DOMAINS reference if not already set
if (!IE.DOMAINS) IE.DOMAINS = ['geopolitics','climate','economy','technology','social'];

console.log('[IE] Cascade Simulator loaded');
