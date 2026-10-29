// Procedural toot sounds, shared by the TV (Toot Boat) and the phone controllers.
// A real one is a buzzing flap: a train of pulses whose rate wobbles and drifts, coloured by a couple
// of body resonances, with breathy noise, stutters and sometimes a wet sputter at the end. Each call
// builds a fresh random variation, so no two sound alike. window.Toot.play(power 0..1, voice 0..7).
(function () {
  'use strict';
  const SR = 22050;
  let ac = null;

  function context() {
    if (!ac) ac = new (window.AudioContext || window.webkitAudioContext)();
    if (ac.state === 'suspended') ac.resume();
    return ac;
  }

  // two-pole resonator (a formant), returns a per-sample filter function
  function resonator(freq, bw) {
    const r = Math.exp(-Math.PI * bw / SR), a1 = 2 * r * Math.cos(2 * Math.PI * freq / SR), a2 = -r * r, g = (1 - r * r) * 0.5;
    let y1 = 0, y2 = 0;
    return x => { const y = g * x + a1 * y1 + a2 * y2; y2 = y1; y1 = y; return y; };
  }

  function synth(power, voice) {
    const R = Math.random;
    const kinds = power < 0.35 ? ['squeak', 'classic', 'puff'] : power > 0.75 ? ['rumble', 'classic', 'wet', 'trumpet'] : ['classic', 'wet', 'squeak', 'trumpet'];
    const kind = kinds[Math.floor(R() * kinds.length)];
    const dur = Math.min(2.2, (kind === 'puff' ? 0.18 : kind === 'squeak' ? 0.25 : 0.3) + power * (kind === 'rumble' ? 1.4 : 0.9) + R() * 0.25);
    const n = Math.floor(dur * SR), out = new Float32Array(n);
    // base buzz rate: low rumbles to high squeaks, nudged per player so voices differ
    const base = { rumble: 38 + R() * 22, classic: 70 + R() * 50, wet: 55 + R() * 35, squeak: 170 + R() * 120, trumpet: 85 + R() * 45, puff: 110 + R() * 60 }[kind] * (1 + (voice % 4) * 0.06);
    const f1 = resonator(140 + R() * 120, 90 + R() * 60), f2 = resonator(420 + R() * 380, 160 + R() * 140), f3 = resonator(1300 + R() * 900, 500);
    const nf = resonator(300 + R() * 500, 600);
    const wob = [R() * 6, 3 + R() * 6, 9 + R() * 14], wobA = [0.12 + R() * 0.12, 0.06 + R() * 0.08, 0.03 + R() * 0.05], ph = [R() * 6, R() * 6, R() * 6];
    // stutters: short gaps where the flap closes (lots for wet ones)
    const gaps = [];
    const gapCount = kind === 'wet' ? 3 + Math.floor(R() * 5) : kind === 'puff' ? 0 : Math.floor(R() * 3);
    for (let i = 0; i < gapCount; i++) { const s = (0.15 + R() * 0.75) * n; gaps.push([s, s + (0.012 + R() * 0.035) * SR]); }
    const breath = kind === 'puff' ? 0.9 : kind === 'wet' ? 0.35 : 0.12;
    let phase = 0, brown = 0, period = 0, pulse = 0;
    for (let i = 0; i < n; i++) {
      const t = i / SR, k = i / n;
      // pitch drifts down as pressure drops; trumpets rise at the very end
      let f = base * (1.15 - 0.35 * k) * (1 + wobA[0] * Math.sin(2 * Math.PI * wob[0] * t + ph[0]) + wobA[1] * Math.sin(2 * Math.PI * wob[1] * t + ph[1]) + wobA[2] * Math.sin(2 * Math.PI * wob[2] * t + ph[2]));
      if (kind === 'trumpet' && k > 0.8) f *= 1 + (k - 0.8) * 3;
      phase += f / SR;
      if (phase >= 1) { phase -= 1; period = SR / f * (0.92 + R() * 0.16); pulse = 1 * (0.75 + R() * 0.5); }   // each flap a bit irregular
      // the flap excitation: a sharp click that decays within the period
      const ex = pulse; pulse *= 0.72;
      brown = brown * 0.985 + (R() * 2 - 1) * 0.15;
      const noise = nf(brown) * breath * (kind === 'wet' ? (0.5 + 0.5 * Math.sin(t * 60 + R())) : 1);
      let s = f1(ex) * 1.0 + f2(ex) * 0.55 + f3(ex) * 0.12 + noise * 2.2;
      // envelope: quick start, wobbly sustain, tail-off; wet ones end in a sputter
      let env = Math.min(1, t / 0.012) * (k < 0.85 ? 1 : (1 - k) / 0.15) * (0.75 + 0.25 * Math.sin(2 * Math.PI * (2 + wob[0]) * t));
      if (kind === 'wet' && k > 0.7 && Math.sin(t * 2 * Math.PI * (35 + R() * 4)) < -0.2) env *= 0.15;
      for (const [a, b] of gaps) if (i > a && i < b) env *= 0.05;
      out[i] = s * env;
    }
    // soft saturation for that flappy rasp, then normalise
    let peak = 0;
    for (let i = 0; i < n; i++) { out[i] = Math.tanh(out[i] * 3); peak = Math.max(peak, Math.abs(out[i])); }
    const g = (0.55 + power * 0.4) / (peak || 1);
    for (let i = 0; i < n; i++) out[i] *= g;
    return { data: out, kind, dur };
  }

  window.Toot = {
    play(power, voice) {
      try {
        const a = context(), s = synth(Math.max(0, Math.min(1, power)), voice || 0);
        const buf = a.createBuffer(1, s.data.length, SR);
        buf.getChannelData(0).set(s.data);
        const src = a.createBufferSource(), gain = a.createGain();
        src.buffer = buf; src.playbackRate.value = 0.92 + Math.random() * 0.16; gain.gain.value = 0.9;
        src.connect(gain); gain.connect(a.destination); src.start();
        return s.kind;
      } catch (e) { return null; }   // no audio on this device
    },
    unlock() { try { context(); } catch (e) {} },
    _synth: synth,
  };
})();
