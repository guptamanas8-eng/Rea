// Tug of War, TV view. Flat cartoon style (thick black outlines, teal field, navy middle band) laid out
// sideways for a TV: Team A pulls from the left, Team B from the right. The server runs the rope;
// this only draws it, easing toward each new position and making pullers lean when their team taps.
(function () {
  'use strict';
  const W = 960, H = 540, CX = W / 2, BAND = 118, ROPE_Y = 300, INK = '#151515';
  const TEAM = { A: { main: '#19bdf0', dark: '#0a8fc0', light: '#7fdcff' }, B: { main: '#ff5a5a', dark: '#d63c3c', light: '#ff9a9a' } };
  let canvas, ctx, raf = 0, view = null, shownP = 0, targetP = 0, lean = { A: 0, B: 0 }, pops = [], rate = { A: [], B: [] }, qr = null;

  // ------------------------------------------------------------ drawing helpers
  function rr(x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
  function blob(fill, lw) { ctx.fillStyle = fill; ctx.fill(); ctx.lineWidth = lw || 5; ctx.strokeStyle = INK; ctx.stroke(); }
  function circle(x, y, r, fill, lw) { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); blob(fill, lw); }
  function text(str, x, y, size, color, align, weight) {
    ctx.font = `${weight || 900} ${size}px system-ui, sans-serif`; ctx.textAlign = align || 'center'; ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round'; ctx.lineWidth = Math.max(3, size / 5); ctx.strokeStyle = INK; ctx.strokeText(str, x, y);
    ctx.fillStyle = color || '#fff'; ctx.fillText(str, x, y);
  }

  // a puller seen from above, facing the rope (dir +1 = facing right, -1 = facing left)
  function puller(x, y, dir, team, name, leanAmt, idle) {
    const c = TEAM[team], back = -dir;
    x += back * leanAmt * 10;
    ctx.save();
    ctx.translate(x, y);
    ctx.fillStyle = 'rgba(0,0,0,.15)'; ctx.beginPath(); ctx.ellipse(back * 10, 16, 40, 30, 0, 0, 7); ctx.fill();
    // feet braced behind, one forward
    ctx.beginPath(); ctx.ellipse(back * 34, 26, 11, 8, 0, 0, 7); blob('#8a4b26', 4);
    ctx.beginPath(); ctx.ellipse(dir * 26, -24, 11, 8, 0, 0, 7); blob('#8a4b26', 4);
    // body: a rounded shirt leaning back from the rope
    ctx.save(); ctx.rotate(dir * (0.08 + leanAmt * 0.12));
    rr(-30, -26, 60, 52, 22); blob(c.main, 5);
    ctx.restore();
    // arms reaching forward to the rope
    const hy = ROPE_Y - y;
    for (const [sy, hx] of [[-14, 28], [12, 48]]) {
      ctx.beginPath(); ctx.lineCap = 'round';
      ctx.moveTo(dir * 6, sy); ctx.lineTo(dir * hx, hy + (sy < 0 ? -2 : 2));
      ctx.lineWidth = 19; ctx.strokeStyle = INK; ctx.stroke(); ctx.lineWidth = 11; ctx.strokeStyle = c.main; ctx.stroke();
      circle(dir * hx, hy + (sy < 0 ? -2 : 2), 9, '#f6d3a8', 4);
    }
    // head: black hair with a strip of face toward the rope, eyes squeezed shut with effort
    circle(back * 4, 0, 27, '#111', 5);
    ctx.beginPath(); ctx.ellipse(dir * 10, 0, 13, 21, 0, 0, 7); ctx.fillStyle = '#f6d3a8'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.lineCap = 'round';
    for (const ey of [-8, 8]) {
      if (idle) { ctx.beginPath(); ctx.arc(dir * 14, ey, 2.6, 0, 7); ctx.fillStyle = INK; ctx.fill(); }   // relaxed while waiting
      else { ctx.beginPath(); ctx.moveTo(dir * 11, ey - 4); ctx.lineTo(dir * 16, ey); ctx.lineTo(dir * 11, ey + 4); ctx.stroke(); }   // squeezed shut, pulling hard
    }
    ctx.restore();
    text(name, x, y + 52, 15, '#fff', 'center', 800);
  }

  function rope(x1, x2, markerX) {
    ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(x1, ROPE_Y); ctx.lineTo(x2, ROPE_Y);
    ctx.lineWidth = 20; ctx.strokeStyle = INK; ctx.stroke();
    ctx.lineWidth = 13; ctx.strokeStyle = '#f7932d'; ctx.stroke();
    // the twist: short diagonal strands that slide with the rope
    ctx.strokeStyle = '#b8611b'; ctx.lineWidth = 3;
    const off = ((markerX % 16) + 16) % 16;
    for (let x = x1 - 16 + off; x < x2; x += 16) { if (x < x1 + 4) continue; ctx.beginPath(); ctx.moveTo(x - 4, ROPE_Y - 6); ctx.lineTo(x + 4, ROPE_Y + 6); ctx.stroke(); }
    // the yellow diamond marks the middle of the rope
    ctx.beginPath(); ctx.moveTo(markerX, ROPE_Y - 34); ctx.lineTo(markerX + 22, ROPE_Y); ctx.lineTo(markerX, ROPE_Y + 34); ctx.lineTo(markerX - 22, ROPE_Y); ctx.closePath();
    blob('#ffd21f', 5);
  }

  function badge(team, corner) {
    const c = TEAM[team], left = corner === 'tl', x = left ? 62 : W - 62, y = left ? 62 : H - 62;
    const score = view.tug.score ? view.tug.score[team] : 0;
    // pill with the score, then the big round button over the corner
    const px = left ? x + 20 : x - 130;
    rr(px, y - 26, 110, 52, 26); blob('#2a2e37', 5);
    text(String(score), left ? px + 78 : px + 32, y + 1, 30, c.main);
    ctx.beginPath(); ctx.moveTo(left ? 0 : W, y + (left ? -90 : 90)); ctx.lineTo(x, y);
    ctx.lineWidth = 120; ctx.strokeStyle = INK; ctx.lineCap = 'butt'; ctx.stroke(); ctx.lineWidth = 110; ctx.strokeStyle = c.dark; ctx.stroke();
    circle(x, y, 50, c.dark, 6); circle(x, y, 39, c.main, 4);
    ctx.beginPath(); ctx.arc(x, y, 34, Math.PI * 1.1, Math.PI * 1.6); ctx.lineWidth = 4; ctx.strokeStyle = c.light; ctx.stroke();
  }

  function draw(now) {
    raf = requestAnimationFrame(draw);
    if (!view || !view.tug) return;
    const t = view.tug, dt = 1 / 60;
    shownP += (targetP - shownP) * 0.25;
    lean.A *= 0.9; lean.B *= 0.9;
    // field
    ctx.fillStyle = '#61c3aa'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#33577a'; ctx.fillRect(CX - BAND, 0, BAND * 2, H);
    ctx.fillStyle = '#ffffff'; ctx.fillRect(CX - BAND - 14, 0, 14, H); ctx.fillRect(CX + BAND, 0, 14, H);
    // team names over each half
    for (const [k, x] of [['A', CX / 2 - 20], ['B', CX * 1.5 + 20]]) text(t.names[k], x, 112, 34, '#fff');
    // rope and pullers move with the rope; the marker sits at the rope's middle
    const markerX = CX - shownP * BAND;
    const A = t.A, B = t.B, idle = view.phase !== 'tug' || t.stage !== 'pull';
    const spacing = 104;
    const ax = (i) => markerX - 150 - i * spacing, bx = (i) => markerX + 150 + i * spacing;
    rope(Math.min(ax(Math.max(0, A.length - 1)) - 40, markerX - 60), Math.max(bx(Math.max(0, B.length - 1)) + 40, markerX + 60), markerX);
    A.forEach((p, i) => puller(ax(i), ROPE_Y + (i % 2 ? 30 : -30), 1, 'A', p.name, lean.A, idle));
    B.forEach((p, i) => puller(bx(i), ROPE_Y + (i % 2 ? -30 : 30), -1, 'B', p.name, lean.B, idle));
    if (!A.length) text('Waiting for a player…', CX / 2 - 20, ROPE_Y, 20, '#e8fff8', 'center', 700);
    if (!B.length) text('Waiting for a player…', CX * 1.5 + 20, ROPE_Y, 20, '#e8fff8', 'center', 700);
    badge('A', 'tl'); badge('B', 'br');
    // tap meters: taps per second for each team, under the badges
    for (const [k, x, y] of [['A', 18, 140], ['B', W - 18, H - 150]]) {
      const n = rate[k].filter(ts => now - ts < 1000).reduce((s, x2) => s + 1, 0);
      rate[k] = rate[k].filter(ts => now - ts < 1000);
      if (view.phase === 'tug' && t.stage === 'pull') text(`${n} taps/s`, x, y, 16, TEAM[k].light, k === 'A' ? 'left' : 'right', 800);
    }
    // little "+n name" pops near each puller who tapped
    for (const p of pops) { p.t += dt; ctx.globalAlpha = Math.max(0, 1 - p.t / 0.8); text(p.text, p.x, p.y - p.t * 50, 16, TEAM[p.team].light, 'center', 900); }
    ctx.globalAlpha = 1; pops = pops.filter(p => p.t < 0.8);
    // headline
    if (view.phase === 'lobby') {
      text('TUG OF WAR', CX, 40, 34, '#ffd21f');
      if (qr && view.join_url) {
        rr(CX - 70, H - 170, 140, 140, 14); blob('#fff', 5); ctx.drawImage(qr, CX - 62, H - 162, 124, 124);
        text('Scan to join: ' + view.join_url.replace('http://', ''), CX, H - 14, 15, '#fff', 'center', 800);
      }
      text(`${A.length}/${t.max}  vs  ${B.length}/${t.max}`, CX, 78, 18, '#e8fff8', 'center', 800);
    } else if (view.phase === 'tug') {
      text(`ROUND ${t.round} · FIRST TO ${t.first_to}`, CX, 34, 22, '#fff');
      if (t.stage === 'countdown') {
        const left = Math.max(0, t.stage_left - (now - t._at) / 1000);
        text(left > 0.6 ? String(Math.ceil(left - 0.5)) : 'PULL!', CX, 200, 90, '#ffd21f');
      } else if (t.stage === 'roundover' && t.winner) text(`${t.names[t.winner]} win the round!`, CX, 200, 44, TEAM[t.winner].light);
    } else if (view.phase === 'over' && t.champion) {
      ctx.fillStyle = 'rgba(0,0,0,.45)'; ctx.fillRect(0, 150, W, 130);
      text(`🏆 ${t.names[t.champion]} WIN!`, CX, 200, 54, TEAM[t.champion].light);
      text(`${t.score.A} – ${t.score.B}`, CX, 252, 28, '#fff');
    }
  }

  window.Tug = {
    start(c) {
      canvas = c; canvas.width = W; canvas.height = H; ctx = canvas.getContext('2d');
      qr = new Image(); qr.src = '/api/game/qr.svg?' + Date.now();
      if (!raf) raf = requestAnimationFrame(draw);
    },
    view(v) {
      if (v.tug) { v.tug._at = performance.now(); if (v.tug.p != null && (v.tug.stage !== 'pull' || !view)) targetP = v.tug.p; if (v.tug.stage === 'countdown') { targetP = 0; } }
      view = v;
    },
    event(ev) {
      if (ev.type !== 'tug' || !view || !view.tug) return;
      targetP = ev.p;
      const now = performance.now(), t = view.tug;
      for (const k of ['A', 'B']) {
        if (ev.team_taps[k]) { lean[k] = Math.min(1.4, lean[k] + 0.35 * ev.team_taps[k]); for (let i = 0; i < ev.team_taps[k]; i++) rate[k].push(now); }
        t[k].forEach((p, i) => {
          const n = ev.taps[p.id]; if (!n) return;
          const x = (k === 'A' ? CX - shownP * BAND - 150 - i * 104 : CX - shownP * BAND + 150 + i * 104);
          pops.push({ text: `+${n}`, x: x + (Math.random() - 0.5) * 20, y: ROPE_Y + (k === 'A' ? (i % 2 ? 30 : -30) : (i % 2 ? -30 : 30)) - 44, t: 0, team: k });
        });
      }
    },
    stop() { cancelAnimationFrame(raf); raf = 0; view = null; },
    get running() { return !!raf; },
  };
})();
