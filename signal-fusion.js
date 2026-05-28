/**
 * Signal Fusion Engine v4
 * Cross-source correlation, temporal overlap detection,
 * geographic convergence, synchronized anomaly identification.
 *
 * Works entirely on the existing events array — no new data sources required.
 * All algorithms: O(n²) max, bounded by sliding windows.
 */

'use strict';

IE.FusionEngine = {

  // Geographic clustering radius (degrees, ~111km per degree)
  GEO_RADIUS_DEG: 3.5,
  // Temporal window for co-occurrence (days)
  TEMPORAL_WINDOW_DAYS: 3,
  // Minimum cluster size to report
  MIN_CLUSTER_SIZE: 2,

  /**
   * Main entry: fuse signals from event array.
   * Returns: { clusters, synchronized_anomalies, cross_source_patterns, fusion_index }
   */
  fuse(events) {
    const geoClust    = this.buildGeoTemporalClusters(events);
    const syncAnom    = this.identifySynchronizedAnomalies(events, geoClust);
    const crossPat    = this.detectCrossSourcePatterns(events);
    const fusionIdx   = this.computeFusionIndex(geoClust, syncAnom);

    return {
      clusters:              geoClust,
      synchronized_anomalies: syncAnom,
      cross_source_patterns: crossPat,
      fusion_index:          fusionIdx,
    };
  },

  /**
   * Build geo-temporal clusters: events within GEO_RADIUS and TEMPORAL_WINDOW.
   * Each cluster = { id, events, centroid, domains, vectors, pressure_score, label }
   */
  buildGeoTemporalClusters(events) {
    // Only events with valid lat/lng
    const geo = events.filter(e => e.lat && e.lng && Math.abs(e.lat) <= 90 && Math.abs(e.lng) <= 180);
    const used = new Set();
    const clusters = [];

    geo.forEach((anchor, i) => {
      if (used.has(i)) return;

      const members = [i];
      const anchorDate = new Date(anchor.date || Date.now()).getTime();

      geo.forEach((other, j) => {
        if (i === j || used.has(j)) return;
        const dist = this._geoDist(anchor.lat, anchor.lng, other.lat, other.lng);
        const dt   = Math.abs(new Date(other.date || Date.now()).getTime() - anchorDate) / 86400000;
        if (dist <= this.GEO_RADIUS_DEG && dt <= this.TEMPORAL_WINDOW_DAYS) {
          members.push(j);
        }
      });

      if (members.length >= this.MIN_CLUSTER_SIZE) {
        members.forEach(idx => used.add(idx));
        const evs = members.map(idx => geo[idx]);
        clusters.push(this._buildCluster(evs, clusters.length));
      }
    });

    return clusters.sort((a, b) => b.pressure_score - a.pressure_score);
  },

  _buildCluster(evs, id) {
    const centroid = {
      lat: evs.reduce((s, e) => s + e.lat, 0) / evs.length,
      lng: evs.reduce((s, e) => s + e.lng, 0) / evs.length,
    };
    const domains  = [...new Set(evs.map(e => e.domain).filter(Boolean))];
    const vectors  = [...new Set(evs.flatMap(e => e.vectors || []))];
    const scores   = evs.map(e => e.escalation_score || e.severity || 0);
    const avg_esc  = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
    const max_esc  = Math.max(...scores);

    // Pressure: multi-domain bonus
    const multi_bonus = domains.length >= 3 ? 1.4 : domains.length >= 2 ? 1.2 : 1.0;
    const pressure = Math.min(100, Math.round(avg_esc * multi_bonus));

    // Fused event label: primary domain + region
    const region = evs[0].region || '';
    const label  = `${domains.slice(0, 2).map(d => IE.DL[d] || d).join('+')} · ${region.slice(0, 20)}`;

    return {
      id: `cluster_${id}`,
      event_count:   evs.length,
      events:        evs.map(e => ({ id: e.id, title: (e.title||'').slice(0,60), domain: e.domain, escalation_score: e.escalation_score||0 })),
      centroid,
      domains,
      vectors,
      avg_esc,
      max_esc,
      pressure_score: pressure,
      multi_domain:   domains.length >= 2,
      label,
      dates: evs.map(e => e.date).filter(Boolean).sort(),
    };
  },

  /**
   * Identify synchronized anomalies:
   * Multiple anomaly-type events with overlapping vectors across domains.
   */
  identifySynchronizedAnomalies(events, clusters) {
    const anomalies = events.filter(e => e.signal_type === 'anomaly' || (e.severity_delta || 0) >= 8);
    if (anomalies.length < 2) return [];

    const result = [];

    // Vector co-occurrence matrix
    const vecDomains = {};
    anomalies.forEach(e => {
      (e.vectors || []).forEach(v => {
        if (!vecDomains[v]) vecDomains[v] = new Set();
        vecDomains[v].add(e.domain);
      });
    });

    // Vectors present in 2+ domains simultaneously → synchronized
    Object.entries(vecDomains).forEach(([vec, domains]) => {
      if (domains.size >= 2) {
        const evs = anomalies.filter(e => (e.vectors || []).includes(vec));
        const avg = Math.round(evs.reduce((s, e) => s + (e.escalation_score || 0), 0) / evs.length);
        result.push({
          type:         'synchronized_vector',
          vector:       vec,
          domains:      [...domains],
          event_count:  evs.length,
          avg_esc:      avg,
          sync_score:   Math.round(domains.size / 5 * 60 + avg * 0.4),
          evidence:     evs.slice(0, 3).map(e => (e.title || '').slice(0, 50)),
        });
      }
    });

    // Multi-domain cluster anomalies
    clusters.filter(c => c.multi_domain && c.pressure_score >= 45).forEach(c => {
      result.push({
        type:         'geo_convergence',
        cluster_id:   c.id,
        domains:      c.domains,
        event_count:  c.event_count,
        avg_esc:      c.avg_esc,
        centroid:     c.centroid,
        sync_score:   c.pressure_score,
        evidence:     c.events.slice(0, 2).map(e => e.title),
      });
    });

    return result.sort((a, b) => b.sync_score - a.sync_score).slice(0, 10);
  },

  /**
   * Detect cross-source patterns:
   * Same domain escalating across multiple source types (institutional / news / satellite / gov).
   */
  detectCrossSourcePatterns(events) {
    const SOURCE_TYPES = {
      institutional: ['reliefweb','who','unhcr','iom','wfp','gdacs','copernicus','usgs','cisa','nasa'],
      news:          ['reuters','bloomberg','financial times','guardian','bbc','sky news','al jazeera','kyiv post'],
      satellite:     ['nasa firms','gdacs','copernicus','sentinel','eonet','aviales'],
      government:    ['мчс','авиалесоохрана','росгидромет','cisa','usgs','fema'],
    };

    const classify = (src) => {
      const sl = (src || '').toLowerCase();
      for (const [type, kws] of Object.entries(SOURCE_TYPES)) {
        if (kws.some(k => sl.includes(k))) return type;
      }
      return 'media';
    };

    const domainSourceTypes = {};
    events.forEach(e => {
      const d  = e.domain;
      const st = classify(e.source);
      if (!d) return;
      if (!domainSourceTypes[d]) domainSourceTypes[d] = {};
      domainSourceTypes[d][st] = (domainSourceTypes[d][st] || 0) + 1;
    });

    return Object.entries(domainSourceTypes)
      .filter(([, types]) => Object.keys(types).length >= 3)
      .map(([domain, types]) => ({
        domain,
        source_types:    types,
        cross_source_count: Object.keys(types).length,
        confidence:      Math.min(1, Object.keys(types).length / 4),
      }))
      .sort((a, b) => b.cross_source_count - a.cross_source_count);
  },

  computeFusionIndex(clusters, syncAnomalies) {
    const mc  = clusters.filter(c => c.multi_domain).length;
    const sa  = syncAnomalies.filter(a => a.sync_score >= 40).length;
    const raw = Math.min(100, mc * 12 + sa * 8 +
      (clusters[0] ? clusters[0].pressure_score * 0.3 : 0));
    return Math.round(raw);
  },

  _geoDist(lat1, lng1, lat2, lng2) {
    // Euclidean degrees approximation (sufficient for clustering)
    return Math.sqrt((lat1 - lat2) ** 2 + (lng1 - lng2) ** 2);
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// GEO-TEMPORAL INTELLIGENCE LAYER
// ═══════════════════════════════════════════════════════════════════════════

IE.GeoIntelligence = {

  // Hex-grid approximation: 5°×5° cells
  HEX_SIZE: 5,

  /**
   * Build regional pressure zones from events with lat/lng.
   * Returns: {cell_key → {lat, lng, events, pressure, domains, label}}
   */
  buildPressureZones(events) {
    const cells = {};

    events.filter(e => e.lat && e.lng).forEach(e => {
      const cellLat = Math.floor(e.lat / this.HEX_SIZE) * this.HEX_SIZE;
      const cellLng = Math.floor(e.lng / this.HEX_SIZE) * this.HEX_SIZE;
      const key = `${cellLat}:${cellLng}`;

      if (!cells[key]) cells[key] = {
        key, lat: cellLat + 2.5, lng: cellLng + 2.5,
        events: [], total_esc: 0, domains: new Set(), vectors: new Set(),
      };

      cells[key].events.push(e);
      cells[key].total_esc += e.escalation_score || e.severity || 0;
      if (e.domain) cells[key].domains.add(e.domain);
      (e.vectors || []).forEach(v => cells[key].vectors.add(v));
    });

    return Object.values(cells)
      .map(c => ({
        ...c,
        count:    c.events.length,
        pressure: Math.round(c.total_esc / c.events.length),
        domains:  [...c.domains],
        vectors:  [...c.vectors],
        multi_domain: c.domains.size >= 2,
      }))
      .filter(c => c.count >= 1)
      .sort((a, b) => b.pressure - a.pressure);
  },

  /**
   * Detect instability corridors: chains of high-pressure cells
   * connected geographically (adjacent cells, both pressure >= threshold).
   */
  detectInstabilityCorridors(pressureZones, threshold = 35) {
    const hot = pressureZones.filter(z => z.pressure >= threshold);
    const corridors = [];
    const used = new Set();

    hot.forEach((start, i) => {
      if (used.has(i)) return;
      const corridor = [start];
      used.add(i);

      hot.forEach((other, j) => {
        if (used.has(j)) return;
        const lastZ = corridor[corridor.length - 1];
        const dist  = Math.max(Math.abs(lastZ.lat - other.lat), Math.abs(lastZ.lng - other.lng));
        if (dist <= this.HEX_SIZE * 2) {
          corridor.push(other);
          used.add(j);
        }
      });

      if (corridor.length >= 2) {
        const avg_p = Math.round(corridor.reduce((s, z) => s + z.pressure, 0) / corridor.length);
        corridors.push({
          cells: corridor.length,
          avg_pressure: avg_p,
          max_pressure: Math.max(...corridor.map(z => z.pressure)),
          span_lat: Math.abs(corridor[0].lat - corridor[corridor.length-1].lat),
          span_lng: Math.abs(corridor[0].lng - corridor[corridor.length-1].lng),
          domains:  [...new Set(corridor.flatMap(z => z.domains))],
          centroid: {
            lat: corridor.reduce((s,z)=>s+z.lat,0)/corridor.length,
            lng: corridor.reduce((s,z)=>s+z.lng,0)/corridor.length,
          },
        });
      }
    });

    return corridors.sort((a, b) => b.avg_pressure - a.avg_pressure).slice(0, 5);
  },

  /**
   * Transboundary contagion: high-pressure events in neighbouring regions
   * propagating cascade signals to each other.
   */
  detectTransboundaryContagion(events) {
    const BORDERS = {
      'Иран':            ['Ирак','Пакистан','Афганистан','Турция','Россия'],
      'Россия':          ['Украина','Беларусь','Финляндия','Монголия','Казахстан'],
      'Украина':         ['Россия','Беларусь','Польша','Румыния','Молдова'],
      'Израиль':         ['Ливан','Сирия','Египет','Иордания'],
      'China':           ['Russia','Mongolia','India','Vietnam'],
      'India':           ['Pakistan','China','Bangladesh','Nepal'],
      'Sudan':           ['Egypt','Ethiopia','South Sudan','Libya'],
    };

    const regionMap = {};
    events.forEach(e => {
      const r = (e.region || '').split('·')[0].trim().slice(0, 24);
      if (!regionMap[r]) regionMap[r] = [];
      regionMap[r].push(e);
    });

    const contagion = [];
    Object.entries(BORDERS).forEach(([src, neighbors]) => {
      const srcEvs = regionMap[src] || [];
      if (!srcEvs.length) return;
      const srcEsc = Math.round(srcEvs.reduce((s,e)=>s+(e.escalation_score||0),0)/srcEvs.length);
      if (srcEsc < 25) return;

      neighbors.forEach(nb => {
        const nbEvs = regionMap[nb] || Object.entries(regionMap)
          .filter(([k]) => k.toLowerCase().includes(nb.toLowerCase()))
          .flatMap(([,v]) => v);
        if (!nbEvs.length) return;
        const nbEsc = Math.round(nbEvs.reduce((s,e)=>s+(e.escalation_score||0),0)/nbEvs.length);

        // Contagion if both regions active AND have cascade overlap
        const srcCascade = new Set(srcEvs.flatMap(e => e.cascade||[]));
        const nbDomains  = new Set(nbEvs.map(e => e.domain));
        const overlap    = [...srcCascade].filter(d => nbDomains.has(d));

        if (overlap.length || (srcEsc >= 35 && nbEsc >= 25)) {
          contagion.push({
            from: src, to: nb,
            src_esc: srcEsc, nb_esc: nbEsc,
            shared_domains: overlap,
            contagion_score: Math.round((srcEsc + nbEsc) / 2 * (1 + overlap.length * 0.2)),
          });
        }
      });
    });

    return contagion.sort((a,b)=>b.contagion_score-a.contagion_score).slice(0,8);
  },
};

console.log('[IE] Signal Fusion + Geo-Intelligence loaded');
