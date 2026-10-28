// Highway Brawl: an original pseudo-3D motorbike racing and brawling game for the TV.
// The road renderer follows the classic segment-projection technique
// (as popularised by Jake Gordon's javascript-racer); everything is drawn in code, no images.
// Runs on the TV page; phones send controls through the server.
(function () {
  'use strict';
  const W = 960, H = 540;                              // internal resolution, scaled up to the TV
  const SEG = 200, RUMBLE = 3, ROAD_W = 2000, CAM_H = 1000, FOV = 100;
  const CAM_DEPTH = 1 / Math.tan((FOV / 2) * Math.PI / 180);
  const PLAYER_Z = CAM_H * CAM_DEPTH;
  const STEP = 1 / 60;
  const MAX = SEG / STEP;                              // top speed, 12000 units/s ≈ 200 km/h on the dial
  const ACCEL = MAX / 5, BRAKE = -MAX, DECEL = -MAX / 5, OFF_DECEL = -MAX / 2, OFF_LIMIT = MAX / 4;
  const CENTRIFUGAL = 0.3;
  const BIKE_W = 300, BIKE_H = 380, BIKE_X = BIKE_W / ROAD_W;
  const AI_NAMES = ['Viper', 'Blaze', 'Duke', 'Raven', 'Tank', 'Nitro', 'Ace', 'Fang'];
  const AI_COLORS = ['#455a64', '#795548', '#5d4037', '#37474f', '#4e342e', '#546e7a', '#3e2723', '#263238'];
  const LIGHT = { road: '#6f6f6f', grass: '#3f9b2f', rumble: '#d8d8d8', lane: '#d8d8d8' };
  const DARK = { road: '#686868', grass: '#378a28', rumble: '#b8322a', lane: null };
  const FINISH = { road: '#f2f2f2', grass: '#3f9b2f', rumble: '#f2f2f2', lane: null };

  let canvas, ctx, segments, trackLength, riders, cars, views, laps, raceTime, countdown, state, rng, hooks;
  let acc = 0, last = 0, raf = 0, hudTimer = 0, endTimer = null, mountains;

  // ------------------------------------------------------------- helpers
  function mulberry(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const rand = (a, b) => a + (b - a) * rng();
  const pick = (arr) => arr[Math.floor(rng() * arr.length)];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, p) => a + (b - a) * p;
  const easeIn = (a, b, p) => a + (b - a) * p * p;
  const easeInOut = (a, b, p) => a + (b - a) * ((-Math.cos(p * Math.PI) / 2) + 0.5);
  const mod = (z) => ((z % trackLength) + trackLength) % trackLength;
  const segAt = (z) => segments[Math.floor(mod(z) / SEG) % segments.length];
  const overlap = (x1, w1, x2, w2, f) => Math.abs(x1 - x2) < (w1 + w2) / 2 * (f || 1);
  const ordinal = (n) => n + (['th', 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || ['th', 'st', 'nd', 'rd'][n % 100] || 'th');

  // --------------------------------------------------------------- track
  function lastY() { return segments.length ? segments[segments.length - 1].p2.world.y : 0; }
  function addSegment(curve, y) {
    const n = segments.length;
    segments.push({ index: n, curve, sprites: [], cars: [],
      p1: { world: { y: lastY(), z: n * SEG }, camera: {}, screen: {} },
      p2: { world: { y, z: (n + 1) * SEG }, camera: {}, screen: {} },
      color: Math.floor(n / RUMBLE) % 2 ? DARK : LIGHT });
  }
  function addRoad(enter, hold, leave, curve, hill) {
    const y0 = lastY(), y1 = y0 + Math.round(hill) * SEG, total = enter + hold + leave;
    for (let n = 0; n < enter; n++) addSegment(easeIn(0, curve, n / enter), easeInOut(y0, y1, n / total));
    for (let n = 0; n < hold; n++) addSegment(curve, easeInOut(y0, y1, (enter + n) / total));
    for (let n = 0; n < leave; n++) addSegment(easeInOut(curve, 0, n / leave), easeInOut(y0, y1, (enter + hold + n) / total));
  }
  function buildTrack() {
    segments = [];
    addRoad(30, 30, 30, 0, 0);
    while (segments.length < 2200) {
      const len = Math.round(rand(20, 70)), kind = rng();
      if (kind < 0.3) addRoad(len, len, len, 0, rand(-30, 30));
      else if (kind < 0.8) addRoad(len, len, len, pick([-1, 1]) * rand(2, 6), rand(-40, 40));
      else { const c = rand(2, 5); addRoad(len, len, len, c, 0); addRoad(len, len, len, -c, rand(-20, 20)); }
    }
    addRoad(80, 80, 80, -2, -lastY() / SEG);          // back down to the start height so the loop joins
    addRoad(40, 40, 40, 0, 0);
    for (let i = 0; i < 3; i++) segments[i].color = FINISH;
    trackLength = segments.length * SEG;
    // scenery: alternates between farmland and pine hills along the lap
    for (let n = 12; n < segments.length - 12; n += Math.round(rand(3, 7))) {
      const zone = Math.floor(n / 400) % 2, side = pick([-1, 1]);
      const kinds = zone ? ['pine', 'pine', 'rock', 'pole', 'bush'] : ['tree', 'tree', 'bush', 'pole', 'board'];
      const kind = pick(kinds), sz = SPRITE[kind];
      segments[n].sprites.push({ kind, offset: side * (1.15 + sz.w / ROAD_W / 2 + rand(0, 1.6)), seed: rng() });
      if (rng() < 0.35) { const k2 = pick(kinds), s2 = SPRITE[k2];
        segments[n].sprites.push({ kind: k2, offset: -side * (1.15 + s2.w / ROAD_W / 2 + rand(0, 1.8)), seed: rng() }); }
    }
  }
  const SPRITE = {
    tree: { w: 1600, h: 2100, hit: 0.18 }, pine: { w: 1100, h: 2300, hit: 0.15 }, bush: { w: 900, h: 480, hit: 0.35 },
    pole: { w: 160, h: 1800, hit: 0.08 }, board: { w: 2000, h: 1500, hit: 0.6 }, rock: { w: 900, h: 600, hit: 0.4 },
    car: { w: 700, h: 520 }, truck: { w: 900, h: 950 },
  };

  // -------------------------------------------------------------- riders
  function makeRiders(racers, aiCount) {
    riders = [];
    const all = racers.map(r => ({ ...r, human: true }));
    for (let i = 0; i < aiCount; i++) all.push({ id: 'ai' + i, name: AI_NAMES[i % AI_NAMES.length], color: AI_COLORS[i % AI_COLORS.length], human: false });
    // humans start mid-pack, like the old games
    const order = all.filter(r => !r.human).slice(0, Math.ceil(aiCount / 2)).concat(all.filter(r => r.human), all.filter(r => !r.human).slice(Math.ceil(aiCount / 2)));
    order.forEach((r, i) => riders.push({ ...r, z: (14 - Math.floor(i / 2) * 2.5) * SEG, x: i % 2 ? 0.45 : -0.45,
      speed: 0, health: 100, crashed: 0, finished: false, finishTime: 0, cool: 0, anim: null, ko: 0,
      input: { l: false, r: false, gas: false, brake: false }, msg: null, msgT: 0, sky: 0,
      aiSpeed: rand(0.80, 0.93), aiLane: pick([-0.55, 0, 0.55]), aiAggro: rand(0.4, 1.1), aiLaneT: rand(1, 4) }));
  }
  function makeCars() {
    cars = [];
    for (let i = 0; i < 26; i++) {
      const z = rand(150, segments.length - 60) * SEG, truck = rng() < 0.25;   // clear of the starting grid
      const car = { z, x: pick([-0.66, 0, 0.66]) + rand(-0.1, 0.1), speed: MAX * rand(0.22, 0.42), kind: truck ? 'truck' : 'car',
        color: pick(['#c62828', '#1565c0', '#f9a825', '#2e7d32', '#eeeeee', '#6a1b9a', '#ef6c00']) };
      segAt(z).cars.push(car); cars.push(car);
    }
  }

  function say(r, text, t) { r.msg = text; r.msgT = t || 1.2; }
  function crash(r, why) {
    if (r.crashed > 0 || r.finished) return;
    r.crashed = 2.6; r.ko += why === 'ko' ? 1 : 0;
    say(r, why === 'ko' ? 'KNOCKED OUT!' : 'CRASH!', 2.2);
  }
  function attack(a, kind) {
    if (a.crashed > 0 || a.cool > 0 || state !== 'race') return;
    a.cool = kind === 'kick' ? 0.8 : 0.45;
    const reach = kind === 'kick' ? 0.62 : 0.5;
    let best = null;
    for (const b of riders) {
      if (b === a || b.crashed > 0 || b.finished) continue;
      const dz = b.z - a.z, dx = b.x - a.x;
      if (Math.abs(dz) < 260 && Math.abs(dx) < reach && Math.abs(dx) > 0.02 && (!best || Math.abs(dx) < Math.abs(best.x - a.x))) best = b;
    }
    const side = best ? Math.sign(best.x - a.x) : (a.input.l ? -1 : a.input.r ? 1 : (a.x > 0 ? -1 : 1));
    a.anim = { kind, side, t: 0.25 };
    if (!best) return;
    best.health -= kind === 'kick' ? 20 : 12;
    best.x += side * (kind === 'kick' ? 0.3 : 0.18);
    best.speed *= 0.9;
    if (a.human) say(a, kind === 'kick' ? 'WHAM!' : 'POW!', 0.6);
    if (best.health <= 0) { crash(best, 'ko'); if (a.human) say(a, 'KO! ' + best.name.toUpperCase() + ' IS DOWN', 1.6); }
    else if (best.human) say(best, 'OUCH!', 0.6);
  }

  // -------------------------------------------------------------- update
  function updateRider(r, dt, leaderZ) {
    const seg = segAt(r.z), pct = r.speed / MAX;
    if (r.anim && (r.anim.t -= dt) <= 0) r.anim = null;
    r.cool = Math.max(0, r.cool - dt);
    if (r.msgT > 0) r.msgT -= dt;
    if (r.crashed > 0) {
      r.crashed -= dt; r.speed *= Math.pow(0.12, dt); r.z += r.speed * dt;
      if (r.crashed <= 0) { r.x = clamp(r.x, -0.8, 0.8); r.speed = 0; if (r.health <= 0) r.health = 100; }
      return;
    }
    let inp = r.input;
    if (!r.human || r.finished) inp = aiThink(r, dt, leaderZ);
    if (state !== 'race') inp = { l: false, r: false, gas: false, brake: false };
    const dx = dt * 2 * pct;
    if (inp.l) r.x -= dx; else if (inp.r) r.x += dx;
    if (r.human && !r.finished) r.x -= dx * pct * seg.curve * CENTRIFUGAL;
    if (inp.gas) r.speed += ACCEL * dt; else if (inp.brake) r.speed += BRAKE * dt; else r.speed += DECEL * dt;
    if (Math.abs(r.x) > 1 && r.speed > OFF_LIMIT) r.speed += OFF_DECEL * dt;
    r.x = clamp(r.x, -2.5, 2.5);
    r.speed = clamp(r.speed, 0, MAX * (r.human ? 1 : 0.97));
    // roadside objects
    if (Math.abs(r.x) > 1) for (const s of seg.sprites) {
      if (overlap(r.x, BIKE_X, s.offset, SPRITE[s.kind].hit, 0.9)) {
        if (r.speed > MAX / 4) { crash(r, 'wall'); r.speed *= 0.5; } else { r.speed = 0; r.x += r.x > s.offset ? 0.1 : -0.1; }
        break;
      }
    }
    // traffic
    for (const c of seg.cars) {
      const dz = mod(c.z - r.z);
      if (dz < SEG * 1.2 && r.speed > c.speed && overlap(r.x, BIKE_X, c.x, SPRITE[c.kind].w / ROAD_W, 0.85)) {
        if (r.speed > MAX * 0.5) crash(r, 'car');
        r.speed = c.speed * 0.6; r.z = c.z - SEG * 0.6 + Math.round((r.z - c.z) / trackLength) * trackLength;
        break;
      }
    }
    // riders bump sideways instead of passing through each other
    for (const o of riders) if (o !== r && o.crashed <= 0 && Math.abs(o.z - r.z) < 120 && Math.abs(o.x - r.x) < BIKE_X) r.x += (r.x < o.x ? -1 : 1) * 0.02;
    r.z += r.speed * dt;
    r.sky += seg.curve * pct * dt * 0.02;
    if (!r.finished && r.z >= laps * trackLength) {
      r.finished = true; r.finishTime = raceTime;
      const pos = place(r);
      if (r.human) say(r, `FINISHED ${ordinal(pos)}!`, 99);
    }
  }
  function aiThink(r, dt, leaderZ) {
    // rubber band a little so the pack stays close to the humans
    let target = r.aiSpeed * MAX;
    if (leaderZ != null) { if (r.z < leaderZ - 4000) target *= 1.08; else if (r.z > leaderZ + 16000) target *= 0.7; else if (r.z > leaderZ + 6000) target *= 0.85; }
    if ((r.aiLaneT -= dt) <= 0) { r.aiLane = pick([-0.55, 0, 0.55]); r.aiLaneT = rand(2, 6); }
    const seg = segAt(r.z);
    for (let n = 1; n < 8; n++) {                       // move over for traffic ahead
      const s = segments[(seg.index + n) % segments.length];
      if (s.cars.some(c => Math.abs(c.x - r.aiLane) < 0.4 && c.speed < r.speed)) { r.aiLane = r.aiLane > 0 ? -0.55 : 0.55; break; }
    }
    const want = r.aiLane - seg.curve * 0.04;
    const inp = { l: r.x > want + 0.05, r: r.x < want - 0.05, gas: r.speed < target, brake: r.speed > target * 1.08 };
    if (!r.finished && state === 'race' && r.cool <= 0) {
      for (const o of riders) {
        if (o === r || o.crashed > 0 || o.finished) continue;
        if (Math.abs(o.z - r.z) < 240 && Math.abs(o.x - r.x) < 0.5 && rng() < r.aiAggro * dt * (o.human ? 2.2 : 0.8)) {
          attack(r, rng() < 0.3 ? 'kick' : 'punch'); break;
        }
      }
    }
    return inp;
  }
  function updateCars(dt) {
    for (const c of cars) {
      const before = segAt(c.z);
      c.z = mod(c.z + c.speed * dt);
      const after = segAt(c.z);
      if (before !== after) { before.cars.splice(before.cars.indexOf(c), 1); after.cars.push(c); }
    }
  }
  function place(r) {
    const sorted = riders.slice().sort((a, b) => (b.finished - a.finished) || (a.finished ? a.finishTime - b.finishTime : b.z - a.z));
    return sorted.indexOf(r) + 1;
  }
  function update(dt) {
    if (state === 'countdown') { countdown -= dt; if (countdown <= 0) { state = 'race'; riders.forEach(r => r.human && say(r, 'GO!', 0.8)); } }
    if (state === 'race' || state === 'done') raceTime += dt;
    const humans = riders.filter(r => r.human);
    const leaderZ = humans.length ? Math.max(...humans.map(r => r.z)) : null;
    updateCars(dt);
    riders.forEach(r => updateRider(r, dt, leaderZ));
    if (state === 'race') {
      const firstDone = humans.filter(r => r.finished).map(r => r.finishTime);
      if (humans.every(r => r.finished) || (firstDone.length && raceTime - Math.min(...firstDone) > 45) || raceTime > 600) {
        state = 'done';
        endTimer = setTimeout(finishRace, 4000);
      }
    }
    if ((hudTimer -= dt) <= 0) { hudTimer = 0.5; sendHud(); }
  }

  // ------------------------------------------------------------- drawing
  function project(p, cx, cy, cz, w, h) {
    p.camera.x = (p.world.x || 0) - cx; p.camera.y = (p.world.y || 0) - cy; p.camera.z = (p.world.z || 0) - cz;
    p.screen.scale = CAM_DEPTH / p.camera.z;
    p.screen.x = Math.round(w / 2 + p.screen.scale * p.camera.x * w / 2);
    p.screen.y = Math.round(h / 2 - p.screen.scale * p.camera.y * h / 2);
    p.screen.w = Math.round(p.screen.scale * ROAD_W * w / 2);
  }
  function poly(x1, y1, x2, y2, x3, y3, x4, y4, color) {
    ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.lineTo(x3, y3); ctx.lineTo(x4, y4); ctx.closePath(); ctx.fill();
  }
  function drawSegment(w, x1, y1, w1, x2, y2, w2, c) {
    const r1 = w1 / 6, r2 = w2 / 6;
    ctx.fillStyle = c.grass; ctx.fillRect(0, y2, w, y1 - y2);
    poly(x1 - w1 - r1, y1, x1 - w1, y1, x2 - w2, y2, x2 - w2 - r2, y2, c.rumble);
    poly(x1 + w1 + r1, y1, x1 + w1, y1, x2 + w2, y2, x2 + w2 + r2, y2, c.rumble);
    poly(x1 - w1, y1, x1 + w1, y1, x2 + w2, y2, x2 - w2, y2, c.road);
    if (c.lane) {
      const l1 = w1 / 40, l2 = w2 / 40, lw1 = w1 * 2 / 3, lw2 = w2 * 2 / 3;
      for (let i = 1, a1 = x1 - w1 + lw1, a2 = x2 - w2 + lw2; i < 3; i++, a1 += lw1, a2 += lw2) poly(a1 - l1 / 2, y1, a1 + l1 / 2, y1, a2 + l2 / 2, y2, a2 - l2 / 2, y2, c.lane);
    }
  }
  function drawBackground(w, h, r) {
    const g = ctx.createLinearGradient(0, 0, 0, h * 0.6);
    g.addColorStop(0, '#3d7fd1'); g.addColorStop(1, '#bfe4ff');
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
    for (const [layer, color, speed, base] of [[0, '#7f97b5', 0.3, 0.42], [1, '#5b8a4e', 0.6, 0.5]]) {
      const m = mountains[layer], off = r.sky * speed;
      ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(0, h);
      for (let i = 0; i <= 48; i++) {
        const t = i / 48, k = ((t + off) % 1 + 1) % 1, idx = Math.floor(k * m.length);
        ctx.lineTo(t * w, h * base - m[idx] * h * 0.16);
      }
      ctx.lineTo(w, h); ctx.closePath(); ctx.fill();
    }
  }
  function drawSprite(kind, x, y, sw, sh, seed, clipY) {
    if (sw < 1 || y - sh > clipY) return;
    const clipped = y > clipY;
    if (clipped) { ctx.save(); ctx.beginPath(); ctx.rect(x - sw, 0, sw * 2, clipY); ctx.clip(); }
    const l = x - sw / 2;
    if (kind === 'tree') {
      ctx.fillStyle = '#5d4037'; ctx.fillRect(x - sw * 0.06, y - sh * 0.4, sw * 0.12, sh * 0.4);
      ctx.fillStyle = seed < 0.5 ? '#2e7d32' : '#388e3c';
      for (const [cx, cy, rr] of [[0, 0.62, 0.34], [-0.2, 0.5, 0.25], [0.2, 0.5, 0.25], [0, 0.82, 0.22]]) { ctx.beginPath(); ctx.arc(x + cx * sw, y - cy * sh, rr * sw, 0, 7); ctx.fill(); }
    } else if (kind === 'pine') {
      ctx.fillStyle = '#4e342e'; ctx.fillRect(x - sw * 0.05, y - sh * 0.2, sw * 0.1, sh * 0.2);
      ctx.fillStyle = seed < 0.5 ? '#1b5e20' : '#2e5e2e';
      for (let i = 0; i < 3; i++) poly(x, y - sh * (0.55 + i * 0.22), x + sw * (0.5 - i * 0.1), y - sh * (0.15 + i * 0.22), x - sw * (0.5 - i * 0.1), y - sh * (0.15 + i * 0.22), x, y - sh * (0.55 + i * 0.22), ctx.fillStyle);
    } else if (kind === 'bush') {
      ctx.fillStyle = '#2f7d32';
      for (const [cx, rr] of [[-0.25, 0.3], [0.25, 0.3], [0, 0.4]]) { ctx.beginPath(); ctx.ellipse(x + cx * sw, y - sh * 0.4, rr * sw, sh * 0.5, 0, 0, 7); ctx.fill(); }
    } else if (kind === 'pole') {
      ctx.fillStyle = '#9e9e9e'; ctx.fillRect(l, y - sh, sw, sh);
      ctx.fillStyle = '#616161'; ctx.fillRect(l - sw * 1.5, y - sh, sw * 4, sw * 0.8);
    } else if (kind === 'board') {
      ctx.fillStyle = '#6d4c41'; ctx.fillRect(l + sw * 0.15, y - sh * 0.5, sw * 0.06, sh * 0.5); ctx.fillRect(l + sw * 0.79, y - sh * 0.5, sw * 0.06, sh * 0.5);
      const cols = ['#e53935', '#fdd835', '#1e88e5', '#43a047', '#fb8c00'];
      ctx.fillStyle = cols[Math.floor(seed * cols.length)]; ctx.fillRect(l, y - sh, sw, sh * 0.55);
      ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.fillRect(l + sw * 0.1, y - sh * 0.85, sw * 0.8, sh * 0.08); ctx.fillRect(l + sw * 0.1, y - sh * 0.7, sw * 0.55, sh * 0.08);
    } else if (kind === 'rock') {
      poly(l, y, l + sw * 0.15, y - sh * 0.7, l + sw * 0.6, y - sh, x + sw / 2, y, '#8d8d8d');
    }
    if (clipped) ctx.restore();
  }
  function drawCar(c, x, y, sw, sh, clipY) {
    if (sw < 1 || y - sh > clipY) return;
    const l = x - sw / 2, truck = c.kind === 'truck';
    ctx.fillStyle = '#111'; ctx.fillRect(l + sw * 0.06, y - sh * 0.16, sw * 0.18, sh * 0.16); ctx.fillRect(l + sw * 0.76, y - sh * 0.16, sw * 0.18, sh * 0.16);
    ctx.fillStyle = c.color;
    if (truck) { ctx.fillRect(l, y - sh, sw, sh * 0.88); ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.fillRect(l + sw * 0.48, y - sh * 0.95, sw * 0.04, sh * 0.8); }
    else { ctx.fillRect(l, y - sh * 0.62, sw, sh * 0.48); poly(l + sw * 0.12, y - sh * 0.62, l + sw * 0.22, y - sh, l + sw * 0.78, y - sh, l + sw * 0.88, y - sh * 0.62, c.color);
      ctx.fillStyle = '#263238'; poly(l + sw * 0.18, y - sh * 0.64, l + sw * 0.26, y - sh * 0.93, l + sw * 0.74, y - sh * 0.93, l + sw * 0.82, y - sh * 0.64, '#263238'); }
    ctx.fillStyle = '#ff1744'; ctx.fillRect(l + sw * 0.04, y - sh * 0.45, sw * 0.14, sh * 0.08); ctx.fillRect(l + sw * 0.82, y - sh * 0.45, sw * 0.14, sh * 0.08);
  }
  function drawBike(r, x, y, sw, sh, clipY, lean, showName) {
    if (sw < 1 || y - sh > clipY) return;
    if (r.crashed > 0) {                                // bike on its side, rider tumbling
      ctx.fillStyle = '#212121'; ctx.beginPath(); ctx.ellipse(x - sw * 0.3, y - sh * 0.1, sw * 0.55, sh * 0.1, 0.2, 0, 7); ctx.fill();
      ctx.fillStyle = r.color; ctx.beginPath(); ctx.ellipse(x + sw * 0.4, y - sh * 0.18, sw * 0.3, sh * 0.12, -0.4 + r.crashed, 0, 7); ctx.fill();
      ctx.fillStyle = '#eee'; ctx.beginPath(); ctx.arc(x + sw * 0.75, y - sh * 0.22, sw * 0.13, 0, 7); ctx.fill();
      return;
    }
    ctx.save(); ctx.translate(x, y); ctx.rotate(lean);
    ctx.fillStyle = '#151515'; ctx.fillRect(-sw * 0.12, -sh * 0.36, sw * 0.24, sh * 0.36);        // rear tyre
    ctx.fillStyle = '#424242'; ctx.fillRect(-sw * 0.3, -sh * 0.52, sw * 0.6, sh * 0.2);            // seat and tail
    ctx.fillStyle = '#ff1744'; ctx.fillRect(-sw * 0.1, -sh * 0.44, sw * 0.2, sh * 0.05);           // tail light
    ctx.strokeStyle = '#333'; ctx.lineWidth = Math.max(1, sw * 0.06);                               // handlebars
    ctx.beginPath(); ctx.moveTo(-sw * 0.5, -sh * 0.66); ctx.lineTo(sw * 0.5, -sh * 0.66); ctx.stroke();
    ctx.fillStyle = r.color; poly(-sw * 0.36, -sh * 0.86, sw * 0.36, -sh * 0.86, sw * 0.26, -sh * 0.5, -sw * 0.26, -sh * 0.5, r.color);  // jacket
    ctx.strokeStyle = r.color; ctx.lineWidth = Math.max(1, sw * 0.1); ctx.lineCap = 'round';
    const a = r.anim;
    for (const side of [-1, 1]) {
      ctx.beginPath(); ctx.moveTo(side * sw * 0.32, -sh * 0.82);
      if (a && a.kind === 'punch' && a.side === side) ctx.lineTo(side * sw * 1.1, -sh * 0.86);
      else ctx.lineTo(side * sw * 0.48, -sh * 0.66);
      ctx.stroke();
    }
    if (a && a.kind === 'kick') {                                                                    // leg out to the side
      ctx.strokeStyle = '#1a237e'; ctx.lineWidth = Math.max(1, sw * 0.12);
      ctx.beginPath(); ctx.moveTo(a.side * sw * 0.2, -sh * 0.5); ctx.lineTo(a.side * sw * 1.05, -sh * 0.4); ctx.stroke();
    }
    ctx.fillStyle = '#f5f5f5'; ctx.beginPath(); ctx.arc(0, -sh * 0.93, sw * 0.2, 0, 7); ctx.fill();  // helmet
    ctx.fillStyle = r.color; ctx.fillRect(-sw * 0.2, -sh * 0.97, sw * 0.4, sh * 0.04);
    ctx.restore();
    if (showName && sw > 14) {
      ctx.font = `bold ${Math.round(clamp(sw * 0.35, 9, 16))}px system-ui, sans-serif`; ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(0,0,0,.5)'; ctx.fillText(r.name, x + 1, y - sh * 1.12 + 1);
      ctx.fillStyle = r.human ? '#fff' : '#ffd54f'; ctx.fillText(r.name, x, y - sh * 1.12);
    }
  }

  function renderView(vp, me) {
    const { x: ox, y: oy, w, h } = vp, N = segments.length, dd = vp.dd;
    ctx.save(); ctx.beginPath(); ctx.rect(ox, oy, w, h); ctx.clip(); ctx.translate(ox, oy);
    drawBackground(w, h, me);
    const camZ = mod(me.z - PLAYER_Z), base = segAt(camZ), basePct = (camZ % SEG) / SEG;
    const meSeg = segAt(me.z), mePct = (mod(me.z) % SEG) / SEG;
    const camY = lerp(meSeg.p1.world.y, meSeg.p2.world.y, mePct) + CAM_H;
    let maxy = h, x = 0, dx = -(base.curve * basePct);
    for (let n = 0; n < dd; n++) {
      const s = segments[(base.index + n) % N];
      s.looped = s.index < base.index; s.clip = maxy;
      const cz = camZ - (s.looped ? trackLength : 0);
      project(s.p1, me.x * ROAD_W - x, camY, cz, w, h);
      project(s.p2, me.x * ROAD_W - x - dx, camY, cz, w, h);
      x += dx; dx += s.curve;
      if (s.p1.camera.z <= CAM_DEPTH || s.p2.screen.y >= s.p1.screen.y || s.p2.screen.y >= maxy) continue;
      drawSegment(w, s.p1.screen.x, s.p1.screen.y, s.p1.screen.w, s.p2.screen.x, s.p2.screen.y, s.p2.screen.w, s.color);
      maxy = s.p2.screen.y;
    }
    // riders by segment, so they're drawn in depth order with the scenery
    const bySeg = new Map();
    for (const r of riders) if (r !== me) { const i = segAt(r.z).index; (bySeg.get(i) || bySeg.set(i, []).get(i)).push(r); }
    for (let n = dd - 1; n > 0; n--) {
      const s = segments[(base.index + n) % N];
      if (!(s.p1.screen.scale > 0)) continue;
      for (const sp of s.sprites) {
        const sc = s.p1.screen.scale, d = SPRITE[sp.kind];
        drawSprite(sp.kind, s.p1.screen.x + sc * sp.offset * ROAD_W * w / 2, s.p1.screen.y, d.w * sc * w / 2, d.h * sc * w / 2, sp.seed, s.clip);
      }
      for (const c of s.cars) {
        const p = (mod(c.z) % SEG) / SEG, sc = lerp(s.p1.screen.scale, s.p2.screen.scale, p), d = SPRITE[c.kind];
        drawCar(c, lerp(s.p1.screen.x, s.p2.screen.x, p) + sc * c.x * ROAD_W * w / 2, lerp(s.p1.screen.y, s.p2.screen.y, p), d.w * sc * w / 2, d.h * sc * w / 2, s.clip);
      }
      for (const r of bySeg.get(s.index) || []) {
        const p = (mod(r.z) % SEG) / SEG, sc = lerp(s.p1.screen.scale, s.p2.screen.scale, p);
        const lean = r.crashed > 0 ? 0 : (r.input.l ? -0.12 : r.input.r ? 0.12 : 0);
        drawBike(r, lerp(s.p1.screen.x, s.p2.screen.x, p) + sc * r.x * ROAD_W * w / 2, lerp(s.p1.screen.y, s.p2.screen.y, p),
          BIKE_W * sc * w / 2, BIKE_H * sc * w / 2, s.clip, lean, true);
      }
    }
    // our own bike, bottom centre
    const sc = CAM_DEPTH / PLAYER_Z, bounce = me.speed > 0 && me.crashed <= 0 ? (Math.sin(raceTime * 40) * 1.5 * me.speed / MAX) : 0;
    const lean = me.crashed > 0 ? 0 : (me.input.l ? -0.18 : me.input.r ? 0.18 : 0) - meSeg.curve * 0.02 * me.speed / MAX;
    drawBike(me, w / 2, h - h * 0.04 + bounce, BIKE_W * sc * w / 2, BIKE_H * sc * w / 2, h, lean, false);
    drawHud(vp, me);
    ctx.restore();
  }
  function text(str, x, y, size, color, align) {
    ctx.font = `900 ${Math.round(size)}px system-ui, sans-serif`; ctx.textAlign = align || 'left';
    ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fillText(str, x + size * 0.06, y + size * 0.06);
    ctx.fillStyle = color || '#fff'; ctx.fillText(str, x, y);
  }
  function drawHud(vp, me) {
    const { w, h } = vp, u = h / 20;
    const pos = place(me), lap = Math.min(laps, Math.floor(Math.max(0, me.z) / trackLength) + 1);
    text(`${ordinal(pos)}`, u * 0.8, u * 2.4, u * 2.2, '#ffeb3b');
    text(`of ${riders.length} · lap ${lap}/${laps}`, u * 0.8, u * 3.6, u * 0.9);
    text(`${Math.round(me.speed / MAX * 200)} km/h`, w - u * 0.8, u * 2.2, u * 1.6, '#fff', 'right');
    ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(w - u * 7.8, u * 2.9, u * 7, u * 0.7);
    ctx.fillStyle = me.health > 50 ? '#66bb6a' : me.health > 25 ? '#ffca28' : '#ef5350';
    ctx.fillRect(w - u * 7.8, u * 2.9, u * 7 * Math.max(0, me.health) / 100, u * 0.7);
    if (views.length > 1) text(me.name, u * 0.8, h - u * 0.8, u * 1.1, me.color);
    // progress strip: everyone's position on this lap
    const sy = u * 4.6, sx = u * 0.8, sw = Math.min(w * 0.3, u * 12);
    ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(sx, sy, sw, u * 0.35);
    for (const r of riders) { ctx.fillStyle = r === me ? '#fff' : r.human ? r.color : '#bdbdbd';
      ctx.fillRect(sx + sw * clamp(r.z / (laps * trackLength), 0, 1) - u * 0.15, sy - (r === me ? u * 0.2 : 0), u * 0.3, u * (r === me ? 0.75 : 0.35)); }
    let msg = null;
    if (state === 'countdown') msg = String(Math.ceil(countdown));
    else if (state === 'done') msg = null;               // the results box is showing
    else if (me.msgT > 0 && me.msg) msg = me.msg;
    if (msg) text(msg, w / 2, h * 0.42, msg.length > 8 ? u * 2.2 : u * 4, msg.startsWith('FINISHED') ? '#ffeb3b' : '#fff', 'center');
  }
  function layout(n) {
    if (n <= 1) return [{ x: 0, y: 0, w: W, h: H, dd: 220 }];
    if (n === 2) return [{ x: 0, y: 0, w: W, h: H / 2, dd: 150 }, { x: 0, y: H / 2, w: W, h: H / 2, dd: 150 }];
    return [0, 1, 2, 3].map(i => ({ x: (i % 2) * W / 2, y: Math.floor(i / 2) * H / 2, w: W / 2, h: H / 2, dd: 110 }));
  }
  function render() {
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
    const humans = riders.filter(r => r.human);
    views.forEach((vp, i) => { if (humans[i]) renderView(vp, humans[i]); else drawStandings(vp); });
    if (views.length > 1) { ctx.fillStyle = '#000'; if (views.length === 2) ctx.fillRect(0, H / 2 - 1, W, 2); else { ctx.fillRect(W / 2 - 1, 0, 2, H); ctx.fillRect(0, H / 2 - 1, W, 2); } }
    if (state === 'done') drawResults();
  }
  function standings() { return riders.slice().sort((a, b) => place(a) - place(b)); }
  function drawStandings(vp) {
    ctx.save(); ctx.translate(vp.x, vp.y); ctx.fillStyle = '#101318'; ctx.fillRect(0, 0, vp.w, vp.h);
    const u = vp.h / 14; text('STANDINGS', u, u * 1.6, u, '#ffeb3b');
    standings().slice(0, 10).forEach((r, i) => text(`${i + 1}. ${r.name}`, u, u * (2.9 + i * 1.05), u * 0.8, r.human ? r.color : '#cfd8dc'));
    ctx.restore();
  }
  function drawResults() {
    ctx.fillStyle = 'rgba(0,0,0,.72)'; ctx.fillRect(W * 0.25, H * 0.12, W * 0.5, H * 0.76);
    text('RESULTS', W / 2, H * 0.22, 30, '#ffeb3b', 'center');
    standings().forEach((r, i) => text(`${i + 1}. ${r.name}${r.finished ? '  ' + r.finishTime.toFixed(1) + 's' : ''}`, W * 0.3, H * 0.3 + i * 22, 17, r.human ? '#fff' : '#b0bec5'));
  }

  // ---------------------------------------------------------------- loop
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.25, (now - last) / 1000 || 0); last = now; acc += dt;
    while (acc >= STEP) { update(STEP); acc -= STEP; }
    render();
  }
  function sendHud() {
    if (!hooks || !hooks.hud) return;
    const out = {};
    for (const r of riders) if (r.human) out[r.id] = { pos: place(r), of: riders.length, lap: Math.min(laps, Math.floor(Math.max(0, r.z) / trackLength) + 1), laps,
      speed: Math.round(r.speed / MAX * 200), health: Math.max(0, Math.round(r.health)), status: state === 'countdown' ? 'countdown' : r.finished ? 'finished' : r.crashed > 0 ? 'crashed' : 'racing' };
    hooks.hud(out);
  }
  function finishRace() {
    if (hooks && hooks.result) hooks.result(standings().map((r, i) => ({ id: r.human ? r.id : null, name: r.name, pos: i + 1,
      finished: r.finished, time: r.finished ? +r.finishTime.toFixed(2) : null, ko: r.ko })));
  }

  // ----------------------------------------------------------------- API
  window.Race = {
    start(cfg) {
      this.stop();
      canvas = cfg.canvas; canvas.width = W; canvas.height = H; ctx = canvas.getContext('2d');
      hooks = cfg; rng = mulberry(cfg.seed || 1); laps = cfg.laps || 2;
      buildTrack(); makeRiders(cfg.racers, cfg.ai == null ? 5 : cfg.ai); makeCars();
      mountains = [0, 1].map(() => { const a = []; let v = 0.5; for (let i = 0; i < 64; i++) { v = clamp(v + rand(-0.25, 0.25), 0, 1); a.push(v); } return a; });
      views = layout(cfg.racers.length);
      raceTime = 0; countdown = 3.99; state = 'countdown'; acc = 0; last = performance.now(); hudTimer = 0;
      raf = requestAnimationFrame(frame);
    },
    input(id, inp) { const r = riders && riders.find(x => x.id === id); if (r) r.input = { l: !!inp.l, r: !!inp.r, gas: !!inp.gas, brake: !!inp.brake }; },
    action(id, kind) { const r = riders && riders.find(x => x.id === id); if (r && (kind === 'punch' || kind === 'kick')) attack(r, kind); },
    stop() { cancelAnimationFrame(raf); clearTimeout(endTimer); raf = 0; },
    get running() { return !!raf; },
    _debug() { return { state, raceTime, trackLength, riders: riders.map(r => ({ id: r.id, z: Math.round(r.z), x: +r.x.toFixed(2), speed: Math.round(r.speed), health: r.health, crashed: r.crashed > 0, finished: r.finished })) }; },
  };
})();
