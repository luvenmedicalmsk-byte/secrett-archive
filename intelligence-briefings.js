/**
 * Autonomous Intelligence Briefings v4
 * Deterministic structured synthesis — not LLM.
 *
 * Output formats:
 *   Daily Intelligence Brief
 *   Escalation Bulletin
 *   Critical Transition Alert
 *   Convergence Alert
 *   Regional Risk Outlook
 *   Infrastructure Stress Report
 *
 * Each briefing: { title, classification, confidence, drivers,
 *   affected_systems, propagation_vectors, escalation_probability,
 *   uncertainty_assessment, body, generated_at }
 */

'use strict';

IE.Briefings = {

  CLASSIFICATION: {
    monitor:        'ROUTINE INTELLIGENCE',
    elevated:       'ELEVATED WATCH',
    critical:       'PRIORITY INTELLIGENCE',
    cascading:      'CRITICAL ALERT',
    systemic_break: 'SYSTEMIC BREAK WARNING',
  },

  /**
   * Generate all briefings from pipeline result.
   * Returns array of briefing objects, sorted by urgency.
   */
  generateAll(events, pipelineResult, alertLevel) {
    const briefings = [];

    briefings.push(this.dailyBrief(events, pipelineResult, alertLevel));

    if ((pipelineResult.acceleration && pipelineResult.acceleration.state !== 'latent') ||
        (pipelineResult.causalChains && pipelineResult.causalChains.length > 3)) {
      briefings.push(this.escalationBulletin(events, pipelineResult));
    }

    const regime_state = pipelineResult.assessment?.regime_interpretation || '';
    if (['transition','nonlinear','unstable'].some(s =>
          (events[0]?.regime_state || regime_state || '').includes(s)) ||
        alertLevel === 'critical' || alertLevel === 'cascading') {
      briefings.push(this.transitionAlert(events, pipelineResult));
    }

    const ci = (pipelineResult.acceleration?.metrics?.convergence_density || 0);
    if (ci >= 0.4) {
      briefings.push(this.convergenceAlert(events, pipelineResult));
    }

    if (pipelineResult.causalChains?.some(c => c.src === 'technology' || c.dst === 'technology')) {
      briefings.push(this.infrastructureStressReport(events, pipelineResult));
    }

    briefings.push(this.regionalRiskOutlook(events, pipelineResult));

    return briefings.sort((a, b) => b.urgency - a.urgency);
  },

  // ── DAILY BRIEF ────────────────────────────────────────────────────────
  dailyBrief(events, r, alertLevel) {
    const gd = this._globalDigest(events, r);
    const conf = this._confidence(alertLevel, r);
    return {
      type:                 'daily_brief',
      title:                'DAILY INTELLIGENCE BRIEF',
      classification:       this.CLASSIFICATION[alertLevel] || this.CLASSIFICATION.monitor,
      urgency:              this._urgencyScore(alertLevel),
      confidence:           conf,
      generated_at:         new Date().toISOString(),
      body:                 gd.body,
      drivers:              gd.drivers,
      affected_systems:     gd.affected_systems,
      propagation_vectors:  gd.propagation_vectors,
      escalation_probability: gd.escalation_probability,
      uncertainty_assessment: this._uncertainty(conf, r),
      key_metrics: {
        total_signals:   events.length,
        critical:        events.filter(e=>e.escalation_level==='critical').length,
        high:            events.filter(e=>e.escalation_level==='high').length,
        rising:          events.filter(e=>e.trend_direction==='rising').length,
        gri_index:       r.assessment?.dominant_risks?.[0]?.avg_score || 0,
      },
    };
  },

  _globalDigest(events, r) {
    const topD = this._topDomains(events).slice(0,3);
    const acc  = r.acceleration || {};
    const chains = r.causalChains || [];
    const topChain = chains[0];

    let body = `Зафиксировано ${events.length} активных сигналов. `;
    body += `Доминирующие домены риска: ${topD.map(d=>IE.DL[d]||d).join(', ')}. `;

    if (acc.state && acc.state !== 'latent') {
      body += `Системное ускорение: ${acc.state} (sync=${((acc.metrics?.sync_score||0)*100).toFixed(0)}%). `;
    }

    if (topChain) {
      body += `Ведущий каскадный путь: ${IE.DL[topChain.src]||topChain.src} → ${IE.DL[topChain.dst]||topChain.dst} через механизм "${topChain.mechanism}". `;
    }

    const critical = events.filter(e=>e.escalation_level==='critical');
    if (critical.length) {
      body += `Критических сигналов: ${critical.length}. Ведущий: "${(critical[0].title||'').slice(0,60)}".`;
    }

    const drivers = topD.map(d => {
      const sub = events.filter(e=>e.domain===d);
      return `${IE.DL[d]||d}: ${sub.length} событий, avg_esc=${Math.round(sub.reduce((s,e)=>s+(e.escalation_score||0),0)/Math.max(1,sub.length))}`;
    });

    const affected = topD.map(d=>IE.DL[d]||d);

    const pvecs = [...new Set(events.filter(e=>(e.escalation_score||0)>=30).flatMap(e=>e.vectors||[]))].slice(0,4);

    const risingFrac = events.filter(e=>e.trend_direction==='rising').length / Math.max(1,events.length);
    const esc_prob   = Math.min(0.95, risingFrac * 0.5 + (acc.metrics?.convergence_density||0) * 0.3 + 0.1);

    return { body, drivers, affected_systems: affected, propagation_vectors: pvecs, escalation_probability: +esc_prob.toFixed(2) };
  },

  // ── ESCALATION BULLETIN ────────────────────────────────────────────────
  escalationBulletin(events, r) {
    const acc    = r.acceleration || {};
    const chains = (r.causalChains || []).slice(0, 4);
    const ampls  = (r.amplifiers || []).slice(0, 3);

    let body = `ESCALATION BULLETIN. `;
    body += `Acceleration state: ${acc.state||'latent'}. `;
    body += `Convergence density: ${((acc.metrics?.convergence_density||0)*100).toFixed(0)}%. `;
    body += `Active cascade paths: ${chains.length}. `;
    if (chains[0]) body += `Primary: ${IE.DL[chains[0].src]||chains[0].src} → ${IE.DL[chains[0].dst]||chains[0].dst} (w=${(chains[0].weight*100).toFixed(0)}%). `;
    if (ampls.length) body += `Amplification vectors: ${ampls.map(a=>a.vector).join(', ')}. `;

    return {
      type:           'escalation_bulletin',
      title:          'ESCALATION ASSESSMENT BULLETIN',
      classification: this.CLASSIFICATION.elevated,
      urgency:        60,
      confidence:     0.72,
      generated_at:   new Date().toISOString(),
      body,
      drivers:        chains.map(c=>`${IE.DL[c.src]||c.src}→${IE.DL[c.dst]||c.dst}`),
      affected_systems: [...new Set(chains.flatMap(c=>[c.src,c.dst]))].map(d=>IE.DL[d]||d),
      propagation_vectors: ampls.map(a=>a.vector),
      escalation_probability: Math.min(0.9, 0.3 + chains.length * 0.12),
      uncertainty_assessment: 'Moderate. Cascade trajectories confirmed by structural analysis.',
    };
  },

  // ── CRITICAL TRANSITION ALERT ──────────────────────────────────────────
  transitionAlert(events, r) {
    const prioritized = (r.prioritized || []).filter(e=>e.signal_class==='critical_system'||e.priority_score>=60);
    let body = `CRITICAL TRANSITION ALERT. System regime indicators suggest elevated nonlinear transition risk. `;
    if (prioritized.length) {
      body += `Top systemic signal: "${(prioritized[0].title||'').slice(0,80)}". `;
    }
    body += `Recommend immediate monitoring of geopolitical-economic cascade vectors.`;

    return {
      type:           'transition_alert',
      title:          'CRITICAL TRANSITION ALERT',
      classification: this.CLASSIFICATION.critical,
      urgency:        80,
      confidence:     0.65,
      generated_at:   new Date().toISOString(),
      body,
      drivers:        prioritized.slice(0,3).map(e=>(e.title||'').slice(0,60)),
      affected_systems: [...new Set(prioritized.map(e=>e.domain))].map(d=>IE.DL[d]||d),
      propagation_vectors: [...new Set(prioritized.flatMap(e=>e.vectors||[]))].slice(0,3),
      escalation_probability: 0.55,
      uncertainty_assessment: 'High. Nonlinear dynamics reduce forecast confidence.',
    };
  },

  // ── CONVERGENCE ALERT ─────────────────────────────────────────────────
  convergenceAlert(events, r) {
    const acc   = r.acceleration || {};
    const cd    = (acc.metrics?.convergence_density || 0);
    const rising_d = (r.acceleration?.clusters || []).map(c=>IE.DL[c.domain]||c.domain);

    let body = `CONVERGENCE ALERT. Systemic synchronization across ${(cd*5).toFixed(0)} domains detected. `;
    if (rising_d.length) body += `Accelerating domains: ${rising_d.join(', ')}. `;
    body += `Synchronized escalation indicates elevated nonlinear transition probability.`;

    return {
      type:           'convergence_alert',
      title:          'CONVERGENCE SYNCHRONIZATION ALERT',
      classification: this.CLASSIFICATION.critical,
      urgency:        75,
      confidence:     0.70,
      generated_at:   new Date().toISOString(),
      body,
      drivers:        rising_d,
      affected_systems: rising_d,
      propagation_vectors: [...new Set(events.filter(e=>(e.escalation_score||0)>=30).flatMap(e=>e.vectors||[]))].slice(0,4),
      escalation_probability: Math.min(0.85, 0.4 + cd * 0.6),
      uncertainty_assessment: 'Multi-domain convergence confirmed. Confidence moderate-high.',
    };
  },

  // ── INFRASTRUCTURE STRESS REPORT ──────────────────────────────────────
  infrastructureStressReport(events, r) {
    const infra = events.filter(e=>(e.vectors||[]).some(v=>['cyber','infrastructure'].includes(v)));
    const cyber = infra.filter(e=>(e.vectors||[]).includes('cyber'));
    const phys  = infra.filter(e=>(e.vectors||[]).includes('infrastructure'));

    let body = `INFRASTRUCTURE STRESS REPORT. `;
    body += `Cyber events: ${cyber.length}. Physical infrastructure: ${phys.length}. `;
    if (cyber.length) body += `Top cyber: "${(cyber[0].title||'').slice(0,60)}". `;
    body += `Cross-domain infrastructure exposure identified.`;

    return {
      type:           'infrastructure_stress',
      title:          'INFRASTRUCTURE STRESS REPORT',
      classification: this.CLASSIFICATION.elevated,
      urgency:        55,
      confidence:     0.78,
      generated_at:   new Date().toISOString(),
      body,
      drivers:        infra.slice(0,3).map(e=>(e.title||'').slice(0,60)),
      affected_systems: ['Technology','Economy','Infrastructure'],
      propagation_vectors: ['cyber','infrastructure'],
      escalation_probability: Math.min(0.8, 0.2 + cyber.length*0.08 + phys.length*0.06),
      uncertainty_assessment: 'Technical indicators reliable. Attribution uncertainty high.',
    };
  },

  // ── REGIONAL RISK OUTLOOK ─────────────────────────────────────────────
  regionalRiskOutlook(events, r) {
    const trajectories = r.trajectories || [];
    const unstable = trajectories.filter(t=>['unstable','systemic_risk','deteriorating'].includes(t.trajectory));

    let body = `REGIONAL RISK OUTLOOK. `;
    if (unstable.length) {
      body += `High-risk countries: ${unstable.map(t=>t.name_ru||t.iso3).join(', ')}. `;
      const top = unstable[0];
      if (top) body += `Highest risk: ${top.name_ru||top.iso3} (score: ${top.risk_score}, fragility: ${top.fragility}). `;
    }
    body += `Transboundary contagion risk elevated in ${unstable.length} regional clusters.`;

    return {
      type:           'regional_outlook',
      title:          'REGIONAL RISK OUTLOOK',
      classification: this.CLASSIFICATION.monitor,
      urgency:        40,
      confidence:     0.68,
      generated_at:   new Date().toISOString(),
      body,
      drivers:        unstable.slice(0,3).map(t=>`${t.name_ru||t.iso3}: ${t.trajectory}`),
      affected_systems: [...new Set(unstable.flatMap(t=>Object.keys(t.domain_breakdown||{})))].map(d=>IE.DL[d]||d).slice(0,4),
      propagation_vectors: ['political','kinetic','economic'],
      escalation_probability: Math.min(0.7, 0.15 + unstable.length * 0.08),
      uncertainty_assessment: 'Country-level data sparse. Regional trends reliable.',
    };
  },

  // ── UTILS ──────────────────────────────────────────────────────────────
  _topDomains(events) {
    const d = {};
    events.forEach(e => { if (e.domain) d[e.domain] = (d[e.domain]||0) + (e.escalation_score||0); });
    return Object.entries(d).sort((a,b)=>b[1]-a[1]).map(([k])=>k);
  },

  _confidence(alertLevel, r) {
    const base = {monitor:.85, elevated:.75, critical:.65, cascading:.55, systemic_break:.50};
    return base[alertLevel] || 0.7;
  },

  _urgencyScore(alertLevel) {
    return {monitor:20, elevated:45, critical:70, cascading:85, systemic_break:95}[alertLevel] || 20;
  },

  _uncertainty(conf, r) {
    if (conf >= 0.8) return 'Low uncertainty. Structural indicators consistent.';
    if (conf >= 0.65) return 'Moderate uncertainty. Multiple data sources confirm trend direction.';
    return 'High uncertainty. Nonlinear dynamics and sparse data reduce confidence.';
  },
};

console.log('[IE] Intelligence Briefings loaded');
