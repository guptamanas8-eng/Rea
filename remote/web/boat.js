// Toot Boat: an original co-op physics game for the TV. Up to four puffball critters share one
// boat and push it upriver by tooting from their seats: toot together to go straight, alone to turn.
// Everything is drawn and synthesised in code. Phones send toots through the server.
(function () {
  'use strict';
  const W = 960, H = 540, STEP = 1 / 60;
  const BOAT_L = 120, BOAT_W = 74, MASS = 1, INERTIA = MASS * (BOAT_L * BOAT_L + BOAT_W * BOAT_W) / 12;
  const TOOT = 230, FLOW = 22, GAS_REGEN = 0.28;

  let canvas, ctx, rng, hooks, raf = 0, last = 0, acc = 0, hudT = 0, endTimer = null;
  let boat, seats, crew, course, items, puffs, texts, state, countdown, time, stars, finishTime, camY, audio, r3d = null;
  // what the 3D view reads each frame
  const view3d = { get boat() { return boat; }, get seats() { return seats; }, get items() { return items; }, get puffs() { return puffs; }, get course() { return course; } };

  // ------------------------------------------------------------- helpers
  function mulberry(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const rand = (a, b) => a + (b - a) * rng();
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  // --------------------------------------------------------------- river
  // the course runs "up" the screen: y goes from 0 at the start to -course.length at the finish
  function buildCourse(length) {
    const waves = [0, 1, 2].map(i => ({ a: rand(60, 160) / (i + 1), f: rand(0.0008, 0.0016) * (i + 1), p: rand(0, 6.3) }));
    const wwaves = [0, 1].map(() => ({ a: rand(30, 70), f: rand(0.0006, 0.0014), p: rand(0, 6.3) }));
    course = {
      length,
      cx: (y) => W / 2 + waves.reduce((s, w) => s + w.a * Math.sin(y * w.f + w.p), 0) * clamp(-y / 600, 0, 1),
      width: (y) => clamp(340 + wwaves.reduce((s, w) => s + w.a * Math.sin(y * w.f + w.p), 0), 220, 460),
    };
    items = [];
    for (let y = -700; y > -length + 300; y -= rand(140, 260)) {
      const c = course.cx(y), hw = course.width(y) / 2, r = rng();
      const x = c + rand(-hw + 50, hw - 50);
      if (r < 0.36) items.push({ kind: 'rock', x, y, r: rand(16, 34) });
      else if (r < 0.5) items.push({ kind: 'log', x, y, len: rand(80, 140), a: rand(-0.6, 0.6), spin: rand(-0.3, 0.3), drift: rand(-20, 20) });
      else if (r < 0.6) items.push({ kind: 'whirl', x, y, r: rand(60, 85), dir: rng() < 0.5 ? -1 : 1 });
      else if (r < 0.8) items.push({ kind: 'bean', x, y, got: false });
      else items.push({ kind: 'star', x, y, got: false });
    }
  }
  function banks(y) { const c = course.cx(y), hw = course.width(y) / 2; return [c - hw, c + hw]; }

  // ---------------------------------------------------------------- boat
  const BOT_NAMES = ['Pip', 'Bubbles', 'Gus'], BOT_COLORS = ['#90a4ae', '#a1887f', '#9575cd'];
  function makeBoat(crewIn, bots) {
    // computer crewmates fill seats so a solo player still gets to toot "together"
    crew = crewIn.concat(Array.from({ length: Math.max(0, Math.min(bots || 0, 4 - crewIn.length)) },
      (_, i) => ({ id: 'bot' + i, name: BOT_NAMES[i] + ' 🤖', color: BOT_COLORS[i], bot: true })));
    // seat positions in boat space (x across, y along, + = toward the stern)
    const layouts = { 1: [-22, 22], 2: [-22, 22], 3: [-26, 0, 26], 4: [-30, -10, 10, 30] };
    const n = crew.length === 1 ? 2 : crew.length;
    seats = layouts[n].map((x, i) => {
      const owner = crew.length === 1 ? crew[0] : crew[i];
      return { x, y: n === 4 && (i === 1 || i === 2) ? 6 : 26, owner, gas: 1, puff: 0, color: owner.color, botT: 1 + i * 0.3,
        label: crew.length === 1 ? (i === 0 ? 'L' : 'R') : owner.name };
    });
    boat = { x: course.cx(-80), y: -80, a: 0, vx: 0, vy: 0, w: 0 };
  }
  function fwd() { return [Math.sin(boat.a), -Math.cos(boat.a)]; }
  function toWorld(px, py) { const c = Math.cos(boat.a), s = Math.sin(boat.a); return [boat.x + px * c - py * s, boat.y + px * s + py * c]; }

  function toot(seatIndex, power) {
    const s = seats[seatIndex];
    if (!s || state !== 'play') return;
    // an empty tank still gives a feeble puff, so the boat can never be stuck for good
    const empty = s.gas < 0.08;
    const p = empty ? 0.15 : clamp(power, 0.2, 1) * Math.min(1, s.gas / 0.25 + 0.15);
    if (empty && Math.random() < 0.4) say('Low on gas! Grab beans', s.x, s.color);
    s.gas = Math.max(0, s.gas - (empty ? 0 : 0.08 + 0.18 * p));
    const [fx, fy] = fwd(), j = TOOT * p;
    boat.vx += fx * j / MASS; boat.vy += fy * j / MASS;
    // torque from where the seat sits: r × F with F along the boat's forward axis
    const c = Math.cos(boat.a), sn = Math.sin(boat.a), rx = s.x * c - s.y * sn, ry = s.x * sn + s.y * c;
    boat.w += (rx * fy * j - ry * fx * j) / INERTIA * 0.55;
    s.puff = 0.35;
    const [wx, wy] = toWorld(s.x, BOAT_L / 2 + 6);
    for (let i = 0; i < 4 + p * 8; i++) puffs.push({ x: wx + rand(-8, 8), y: wy + rand(-4, 8), vx: -fx * rand(30, 90) + rand(-25, 25), vy: -fy * rand(30, 90) + rand(-25, 25),
      r: rand(6, 12) + p * 10, t: 0, life: rand(0.9, 1.6) });
    if (p > 0.75) texts.push({ text: pick(['PFFFT!', 'BRRAP!', 'TOOT!', 'PRRRT!']), x: wx, y: wy + 20, t: 0, color: s.color });
    playToot(p, seatIndex);
  }
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  function say(text, x, color) { texts.push({ text, x: boat.x + x, y: boat.y - 70, t: 0, color: color || '#fff' }); }

  // --------------------------------------------------------------- audio
  function playToot(p, voice) {
    if (window.Toot) { window.Toot.play(p, voice); return; }   // the realistic synth (toot.js)
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
      const t0 = audio.currentTime, dur = 0.18 + p * 0.55, base = 70 + voice * 18 + Math.random() * 25;
      const osc = audio.createOscillator(), og = audio.createGain(), lp = audio.createBiquadFilter();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(base * 1.4, t0);
      osc.frequency.exponentialRampToValueAtTime(base * 0.7, t0 + dur);
      const lfo = audio.createOscillator(), lg = audio.createGain();
      lfo.frequency.value = 18 + Math.random() * 14; lg.gain.value = base * 0.35; lfo.connect(lg); lg.connect(osc.frequency);
      lp.type = 'lowpass'; lp.frequency.value = 600 + p * 500; lp.Q.value = 6;
      og.gain.setValueAtTime(0.0001, t0); og.gain.exponentialRampToValueAtTime(0.5 * (0.5 + p / 2), t0 + 0.03);
      og.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      osc.connect(lp); lp.connect(og); og.connect(audio.destination);
      osc.start(t0); lfo.start(t0); osc.stop(t0 + dur + 0.05); lfo.stop(t0 + dur + 0.05);
    } catch (e) { /* no audio on this device */ }
  }

  // -------------------------------------------------------------- update
  function hullPoints() { return [[-BOAT_W / 2, -BOAT_L / 2 + 14], [BOAT_W / 2, -BOAT_L / 2 + 14], [0, -BOAT_L / 2], [-BOAT_W / 2, BOAT_L / 2], [BOAT_W / 2, BOAT_L / 2]].map(p => toWorld(p[0], p[1])); }
  function bounce(nx, ny, depth, rest) {
    boat.x += nx * depth; boat.y += ny * depth;
    const vn = boat.vx * nx + boat.vy * ny;
    if (vn < 0) { boat.vx -= (1 + rest) * vn * nx; boat.vy -= (1 + rest) * vn * ny; boat.w *= 0.6; if (vn < -60) say(pick(['BONK!', 'OOF!', 'THUD!']), 0); }
  }
  function update(dt) {
    if (state === 'countdown') { countdown -= dt; if (countdown <= 0) { state = 'play'; say('GO!', 0, '#ffeb3b'); } }
    if (state === 'play') time += dt;
    // water: strong sideways drag (a keel), lighter drag forward, slow spin damping, gentle downstream flow
    const [fx, fy] = fwd(), vf = boat.vx * fx + boat.vy * fy, vl = boat.vx * fy - boat.vy * fx;
    const nvf = vf * Math.exp(-0.55 * dt), nvl = vl * Math.exp(-3 * dt);
    boat.vx = fx * nvf + fy * nvl; boat.vy = fy * nvf - fx * nvl;
    boat.vy += FLOW * dt * 0.8;
    boat.w *= Math.exp(-1.8 * dt);
    for (const it of items) {
      if (it.kind === 'whirl') {
        const dx = it.x - boat.x, dy = it.y - boat.y, d = Math.hypot(dx, dy);
        if (d < it.r * 1.6) { const f = (1 - d / (it.r * 1.6)) * 160 * dt; boat.vx += dx / d * f - dy / d * f * it.dir; boat.vy += dy / d * f + dx / d * f * it.dir; boat.w += it.dir * 1.6 * dt; }
      } else if (it.kind === 'log') { it.a += it.spin * dt; it.x += it.drift * dt; const [l, r] = banks(it.y); if (it.x < l + 40 || it.x > r - 40) it.drift *= -1; }
    }
    boat.x += boat.vx * dt; boat.y += boat.vy * dt; boat.a += boat.w * dt;
    // banks
    for (const [px, py] of hullPoints()) {
      const [l, r] = banks(py);
      if (px < l) bounce(1, 0, l - px, 0.35); else if (px > r) bounce(-1, 0, px - r, 0.35);
    }
    if (boat.y > 40) bounce(0, -1, boat.y - 40, 0.2);
    // rocks and logs
    const pts = hullPoints();
    for (const it of items) {
      if (it.kind === 'rock') {
        for (const [px, py] of pts) { const dx = px - it.x, dy = py - it.y, d = Math.hypot(dx, dy); if (d < it.r) { bounce(dx / d, dy / d, it.r - d, 0.45); break; } }
      } else if (it.kind === 'log') {
        const ax = Math.cos(it.a) * it.len / 2, ay = Math.sin(it.a) * it.len / 2;
        for (const [px, py] of pts) {
          const t = clamp(((px - (it.x - ax)) * 2 * ax + (py - (it.y - ay)) * 2 * ay) / (4 * (ax * ax + ay * ay)), 0, 1);
          const qx = it.x - ax + 2 * ax * t, qy = it.y - ay + 2 * ay * t, dx = px - qx, dy = py - qy, d = Math.hypot(dx, dy);
          if (d < 12 && d > 0) { bounce(dx / d, dy / d, 12 - d, 0.4); break; }
        }
      } else if ((it.kind === 'bean' || it.kind === 'star') && !it.got && Math.hypot(it.x - boat.x, it.y - boat.y) < 55) {
        it.got = true;
        if (it.kind === 'bean') { seats.forEach(s => s.gas = Math.min(1, s.gas + 0.6)); texts.push({ text: 'BEANS! +GAS', x: it.x, y: it.y, t: 0, color: '#ffb74d' }); }
        else { stars++; texts.push({ text: '★ +1', x: it.x, y: it.y, t: 0, color: '#ffeb3b' }); }
      }
    }
    for (const s of seats) { s.gas = Math.min(1, s.gas + GAS_REGEN * dt); s.puff = Math.max(0, s.puff - dt); }
    if (state === 'play') seats.forEach((s, i) => { if (s.owner.bot && (s.botT -= dt) <= 0) botThink(s, i); });
    for (const p of puffs) { p.t += dt; p.x += p.vx * dt; p.y += p.vy * dt + FLOW * dt; p.vx *= 0.96; p.vy *= 0.96; }
    puffs = puffs.filter(p => p.t < p.life);
    for (const t of texts) t.t += dt;
    texts = texts.filter(t => t.t < 1.3);
    camY += (boat.y - H * 0.18 - camY) * Math.min(1, dt * 4);
    if (state === 'play' && boat.y < -course.length) {
      state = 'done'; finishTime = time;
      say('FINISH!', 0, '#ffeb3b');
      endTimer = setTimeout(() => hooks.result && hooks.result({ finished: true, time: +finishTime.toFixed(2), stars, total: items.filter(i => i.kind === 'star').length }), 4500);
    }
    if (state === 'play' && time > 600) {
      state = 'done';
      endTimer = setTimeout(() => hooks.result && hooks.result({ finished: false, time: null, stars, total: items.filter(i => i.kind === 'star').length }), 3000);
    }
    if ((hudT -= dt) <= 0) { hudT = 0.4; sendHud(); }
  }

  // a computer crewmate looks up the river and toots from its seat when that helps, with a little
  // randomness so it feels like a (decent) family member rather than an autopilot
  function botThink(s, i) {
    s.botT = rand(0.7, 1.3);
    const ty = boat.y - 260, want = Math.atan2(course.cx(ty) - boat.x, boat.y - ty);
    let e = want - boat.a;
    while (e > Math.PI) e -= 2 * Math.PI; while (e < -Math.PI) e += 2 * Math.PI;
    e -= boat.w * 0.4;                                   // allow for the spin already happening
    const side = Math.sign(s.x);                         // -1 left seat (turns right), +1 right seat, 0 middle
    let go;
    if (e > 0.14) go = side < 0;
    else if (e < -0.14) go = side > 0;
    else go = s.gas > 0.3;
    if (rng() < 0.12) go = !go;                          // nobody's perfect
    if (go) toot(i, rand(0.4, 0.85));
  }

  // ------------------------------------------------------------- drawing
  function draw() {
    if (r3d) {
      r3d.render(performance.now() / 1000);
      ctx.clearRect(0, 0, W, H);
      for (const t of texts) {
        const p = r3d.project(t.x, t.y, 70 + t.t * 50);
        if (p) { ctx.globalAlpha = 1 - t.t / 1.3; label(t.text, p[0], p[1], 24, t.color, 'center'); ctx.globalAlpha = 1; }
      }
      drawHud();
      return;
    }
    const top = camY - H * 0.62, bottom = top + H;
    ctx.fillStyle = '#5aa845'; ctx.fillRect(0, 0, W, H);
    // grass speckles that scroll with the world
    ctx.fillStyle = '#4c9a3a';
    for (let y = Math.floor(top / 40) * 40; y < bottom; y += 40) for (let k = 0; k < 6; k++) {
      const x = ((Math.sin(y * 12.9898 + k * 78.233) * 43758.5453) % 1 + 1) % 1 * W; ctx.fillRect(x, y - top, 4, 4);
    }
    // river, sand edge then water, in horizontal strips
    for (const [pad, color] of [[14, '#d9c08a'], [0, '#3a8fd1']]) {
      ctx.fillStyle = color; ctx.beginPath();
      const step = 8, left = [], right = [];
      for (let y = top - step; y <= bottom + step; y += step) { const [l, r] = banks(y); left.push([l - pad, y - top]); right.push([r + pad, y - top]); }
      ctx.moveTo(left[0][0], left[0][1]); left.forEach(p => ctx.lineTo(p[0], p[1])); right.reverse().forEach(p => ctx.lineTo(p[0], p[1])); ctx.closePath(); ctx.fill();
    }
    // flowing ripples
    ctx.strokeStyle = 'rgba(255,255,255,.18)'; ctx.lineWidth = 2;
    const flowOff = (time * FLOW * 1.5) % 60;
    for (let y = Math.floor(top / 60) * 60 + flowOff; y < bottom; y += 60) {
      const [l, r] = banks(y);
      for (let k = 0; k < 4; k++) { const x = l + (r - l) * ((k + 0.5 + Math.sin(y * 0.01 + k) * 0.2) / 4); ctx.beginPath(); ctx.moveTo(x - 14, y - top); ctx.quadraticCurveTo(x, y - top - 5, x + 14, y - top); ctx.stroke(); }
    }
    // finish line
    const fy = -course.length - top;
    if (fy > -20 && fy < H + 20) { const [l, r] = banks(-course.length); for (let x = l, i = 0; x < r; x += 20, i++) for (let j = 0; j < 2; j++) { ctx.fillStyle = (i + j) % 2 ? '#fff' : '#222'; ctx.fillRect(x, fy + j * 10, 20, 10); } }
    for (const it of items) { const y = it.y - top; if (y < -100 || y > H + 100) continue; drawItem(it, it.x, y); }
    for (const p of puffs) { const a = 1 - p.t / p.life; ctx.fillStyle = `rgba(160,205,90,${0.55 * a})`; ctx.beginPath(); ctx.arc(p.x, p.y - top, p.r * (1 + p.t), 0, 7); ctx.fill(); }
    drawBoat(boat.x, boat.y - top);
    for (const t of texts) { ctx.globalAlpha = 1 - t.t / 1.3; label(t.text, t.x, t.y - top - t.t * 40, 22, t.color, 'center'); ctx.globalAlpha = 1; }
    drawHud();
  }
  function drawItem(it, x, y) {
    if (it.kind === 'rock') {
      ctx.fillStyle = '#7d7d7d'; ctx.beginPath(); ctx.arc(x, y, it.r, 0, 7); ctx.fill();
      ctx.fillStyle = '#9e9e9e'; ctx.beginPath(); ctx.arc(x - it.r * 0.3, y - it.r * 0.3, it.r * 0.45, 0, 7); ctx.fill();
    } else if (it.kind === 'log') {
      ctx.save(); ctx.translate(x, y); ctx.rotate(it.a); ctx.fillStyle = '#8d5a2b';
      ctx.beginPath(); ctx.arc(-it.len / 2 + 11, 0, 11, Math.PI / 2, Math.PI * 1.5); ctx.arc(it.len / 2 - 11, 0, 11, -Math.PI / 2, Math.PI / 2); ctx.closePath(); ctx.fill();
      ctx.fillStyle = '#c49a6c'; ctx.beginPath(); ctx.ellipse(it.len / 2 - 4, 0, 5, 10, 0, 0, 7); ctx.fill(); ctx.restore();
    } else if (it.kind === 'whirl') {
      ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 3;
      for (let k = 0; k < 3; k++) { ctx.beginPath(); ctx.arc(x, y, it.r * (0.3 + k * 0.3), time * 3 * it.dir + k, time * 3 * it.dir + k + 4); ctx.stroke(); }
    } else if (it.kind === 'bean' && !it.got) {
      const b = Math.sin(time * 4 + x) * 3;
      for (const [dx, c] of [[-7, '#a1462b'], [7, '#b8552f']]) { ctx.fillStyle = c; ctx.beginPath(); ctx.ellipse(x + dx, y + b, 8, 12, dx * 0.04, 0, 7); ctx.fill(); }
    } else if (it.kind === 'star' && !it.got) {
      ctx.save(); ctx.translate(x, y); ctx.rotate(time * 1.5); ctx.fillStyle = '#ffd600'; ctx.beginPath();
      for (let i = 0; i < 10; i++) { const r = i % 2 ? 8 : 18, a = i * Math.PI / 5; ctx.lineTo(Math.sin(a) * r, -Math.cos(a) * r); } ctx.closePath(); ctx.fill(); ctx.restore();
    }
  }
  function drawBoat(x, y) {
    ctx.save(); ctx.translate(x, y); ctx.rotate(boat.a);
    ctx.fillStyle = 'rgba(0,0,0,.18)'; ctx.beginPath(); ctx.ellipse(6, 8, BOAT_W / 2 + 4, BOAT_L / 2 + 4, 0, 0, 7); ctx.fill();
    // hull: pointed bow, square stern
    ctx.fillStyle = '#8b5a2b'; ctx.beginPath(); ctx.moveTo(0, -BOAT_L / 2 - 8);
    ctx.quadraticCurveTo(BOAT_W / 2 + 6, -BOAT_L / 4, BOAT_W / 2, BOAT_L / 2); ctx.lineTo(-BOAT_W / 2, BOAT_L / 2); ctx.quadraticCurveTo(-BOAT_W / 2 - 6, -BOAT_L / 4, 0, -BOAT_L / 2 - 8); ctx.fill();
    ctx.fillStyle = '#b07a43'; ctx.beginPath(); ctx.moveTo(0, -BOAT_L / 2 + 2);
    ctx.quadraticCurveTo(BOAT_W / 2 - 4, -BOAT_L / 4, BOAT_W / 2 - 7, BOAT_L / 2 - 7); ctx.lineTo(-BOAT_W / 2 + 7, BOAT_L / 2 - 7); ctx.quadraticCurveTo(-BOAT_W / 2 + 4, -BOAT_L / 4, 0, -BOAT_L / 2 + 2); ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,.15)'; ctx.lineWidth = 2;
    for (let k = -20; k <= 40; k += 15) { ctx.beginPath(); ctx.moveTo(-BOAT_W / 2 + 8, k); ctx.lineTo(BOAT_W / 2 - 8, k); ctx.stroke(); }
    for (const s of seats) drawCritter(s.x, s.y, s);
    ctx.restore();
  }
  function drawCritter(x, y, s) {
    // a round puffball seen from above: body, two eyes looking forward, cheeks puff when tooting
    const r = seats.length === 4 ? 13 : 16, puff = s.puff > 0 ? 1 + s.puff * 0.6 : 1;
    ctx.fillStyle = s.color; ctx.beginPath(); ctx.ellipse(x, y, r * puff, r * (1 + (puff - 1) * 0.4), 0, 0, 7); ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.beginPath(); ctx.arc(x - r * 0.35, y - r * 0.35, r * 0.3, 0, 7); ctx.fill();
    for (const ex of [-0.38, 0.38]) { ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x + ex * r, y - r * 0.55, r * 0.28, 0, 7); ctx.fill();
      ctx.fillStyle = '#111'; ctx.beginPath(); ctx.arc(x + ex * r, y - r * 0.68, r * 0.13, 0, 7); ctx.fill(); }
  }
  function label(str, x, y, size, color, align) {
    ctx.font = `900 ${size}px system-ui, sans-serif`; ctx.textAlign = align || 'left';
    ctx.fillStyle = 'rgba(0,0,0,.55)'; ctx.fillText(str, x + 2, y + 2); ctx.fillStyle = color || '#fff'; ctx.fillText(str, x, y);
  }
  function drawHud() {
    label(fmt(time), 20, 40, 30); label(`★ ${stars}`, 20, 76, 24, '#ffeb3b');
    // river progress on the right
    const prog = clamp(-boat.y / course.length, 0, 1);
    ctx.fillStyle = 'rgba(0,0,0,.35)'; ctx.fillRect(W - 30, 40, 10, H - 120);
    ctx.fillStyle = '#fff'; ctx.fillRect(W - 34, 40 + (H - 120) * (1 - prog) - 3, 18, 6);
    label('🏁', W - 37, 34, 16);
    // gas gauges along the bottom
    const n = seats.length, bw = Math.min(200, (W - 40) / n - 12);
    seats.forEach((s, i) => {
      const x = 20 + i * (bw + 12), y = H - 34;
      ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(x, y, bw, 22);
      ctx.fillStyle = s.color; ctx.fillRect(x, y, bw * s.gas, 22);
      label(s.label, x + 6, y + 17, 15, '#fff');
    });
    let msg = null;
    if (state === 'countdown') msg = String(Math.ceil(countdown));
    else if (state === 'done') msg = finishTime != null ? `FINISHED IN ${fmt(finishTime)}!` : 'OUT OF TIME';
    if (msg) label(msg, W / 2, H * 0.36, msg.length > 3 ? 46 : 90, '#fff', 'center');
    if (state === 'countdown' || time < 6) label(seats.length === 2 && crew.length === 1 ? 'Left and right toots turn the boat. Both together go straight!' :
      'Toot together to go straight. Alone, you turn!', W / 2, H * 0.36 + 44, 20, '#fff', 'center');
  }

  // ---------------------------------------------------------------- loop
  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.25, (now - last) / 1000 || 0); last = now; acc += dt;
    while (acc >= STEP) { update(STEP); acc -= STEP; }
    draw();
  }
  function sendHud() {
    if (!hooks.hud) return;
    hooks.hud({ seats: seats.map(s => ({ owner: s.owner.id, gas: +s.gas.toFixed(2) })), progress: +clamp(-boat.y / course.length, 0, 1).toFixed(3),
      time: +time.toFixed(1), stars, status: state });
  }

  window.Boat = {
    start(cfg) {
      this.stop();
      canvas = cfg.canvas; canvas.width = W; canvas.height = H; ctx = canvas.getContext('2d');
      hooks = cfg; rng = mulberry(cfg.seed || 1);
      buildCourse(cfg.length || 9000); makeBoat(cfg.crew, cfg.bots);
      puffs = []; texts = []; stars = 0; time = 0; finishTime = null; countdown = 3.99; state = 'countdown';
      camY = boat.y - H * 0.18; acc = 0; last = performance.now(); hudT = 0;
      r3d = null;
      if (cfg.threeD !== false && window.Boat3D && window.Boat3D.supported()) {
        try { window.Boat3D.init(canvas.parentNode, canvas, view3d); r3d = window.Boat3D; canvas.style.background = 'transparent'; }
        catch (e) { r3d = null; }                         // no 3D on this TV: the flat view still works
      }
      raf = requestAnimationFrame(frame);
    },
    // a toot from a player: seat index (or -1 for "my seat"), power 0..1 from how long they charged
    toot(id, seat, power) {
      const mine = seats.map((s, i) => s.owner.id === id ? i : -1).filter(i => i >= 0);
      const i = mine.includes(seat) ? seat : mine[0];
      if (i != null) toot(i, power);
    },
    // TV remote for the first player: 'left' / 'right' toot their outermost seats, 'all' toots every seat they have
    remote(kind, power) {
      const id = crew.find(c => !c.bot).id, mine = seats.map((s, i) => s.owner.id === id ? i : -1).filter(i => i >= 0);
      if (!mine.length) return;
      const list = kind === 'left' ? [mine[0]] : kind === 'right' ? [mine[mine.length - 1]] : mine;
      list.forEach(i => toot(i, power));
    },
    unlockAudio() { if (window.Toot) window.Toot.unlock(); try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (e) {} },
    stop() { cancelAnimationFrame(raf); clearTimeout(endTimer); raf = 0; if (r3d) { r3d.dispose(); r3d = null; } },
    get is3d() { return !!r3d; },
    get running() { return !!raf; },
    _debug() { return { state, time, stars, boat: { x: Math.round(boat.x), y: Math.round(boat.y), a: +boat.a.toFixed(2), vx: Math.round(boat.vx), vy: Math.round(boat.vy) },
      progress: -boat.y / course.length, seats: seats.map(s => ({ owner: s.owner.id, gas: +s.gas.toFixed(2) })), cx: course.cx(boat.y) }; },
  };
})();
