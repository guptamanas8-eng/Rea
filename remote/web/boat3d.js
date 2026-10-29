// 3D view for Toot Boat, built with three.js (bundled, MIT). The simulation stays in boat.js; this
// module only draws it: sloped river banks with trees, water, rocks, logs, whirlpools, beans and stars,
// the wooden boat with its puffball crew, toot clouds, and a chase camera behind the boat.
// World (x, y) from the simulation maps to three.js (x, height, y); the river runs toward -z.
import * as THREE from '/vendor/three.module.min.js';

const W = 960, H = 540;
let renderer, scene, camera, st, boatGroup, critters = [], itemMeshes = [], puffMeshes = [], ripples, whirls = [];
let camHead = 0, camPos = null, lastT = 0;

function rngFrom(seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
  t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

function supported() {
  try { const c = document.createElement('canvas'); return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl'))); }
  catch (e) { return false; }
}

// ------------------------------------------------------------------ land and water
function buildTerrain(course) {
  const step = 40, y0 = 600, y1 = -course.length - 900, rows = Math.ceil((y0 - y1) / step) + 1;
  const cols = [[-1500, 46, 'g'], [-70, 40, 'g'], [-26, 14, 's'], [4, -16, 'b'], [-4, -16, 'b'], [26, 14, 's'], [70, 40, 'g'], [1500, 46, 'g']];
  const pos = [], col = [], idx = [], c = new THREE.Color(), rnd = rngFrom(7);
  for (let r = 0; r < rows; r++) {
    const y = y0 - r * step, cx = course.cx(y), hw = course.width(y) / 2;
    cols.forEach(([dx, h, kind], k) => {
      const x = k < 4 ? cx - hw + dx * (k === 0 ? 1 : 1) : cx + hw + dx;
      pos.push(k === 0 ? cx - hw - 1500 : k === 7 ? cx + hw + 1500 : x, h + (kind === 'g' ? (rnd() - 0.5) * 8 : 0), y);
      c.set(kind === 'g' ? '#5aa845' : kind === 's' ? '#d9c08a' : '#2a6a96');
      if (kind === 'g') c.offsetHSL(0, 0, (rnd() - 0.5) * 0.05);
      col.push(c.r, c.g, c.b);
    });
    if (r > 0) for (let k = 0; k < cols.length - 1; k++) {
      const a = (r - 1) * cols.length + k, b = a + 1, d = r * cols.length + k, e = d + 1;
      idx.push(a, b, d, b, e, d);   // counter-clockwise seen from above, so faces point up
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  g.setIndex(idx); g.computeVertexNormals();
  scene.add(new THREE.Mesh(g, new THREE.MeshLambertMaterial({ vertexColors: true })));

  // water surface: a strip that follows the river
  const wp = [], wi = [];
  for (let r = 0; r < rows; r++) {
    const y = y0 - r * step, cx = course.cx(y), hw = course.width(y) / 2 + 22;
    wp.push(cx - hw, 0, y, cx + hw, 0, y);
    if (r > 0) { const a = (r - 1) * 2; wi.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  }
  const wg = new THREE.BufferGeometry();
  wg.setAttribute('position', new THREE.Float32BufferAttribute(wp, 3)); wg.setIndex(wi); wg.computeVertexNormals();
  scene.add(new THREE.Mesh(wg, new THREE.MeshPhongMaterial({ color: '#3d93d6', specular: '#cfefff', shininess: 70, transparent: true, opacity: 0.86 })));

  // ripples drifting downstream (recycled around the boat each frame)
  ripples = new THREE.InstancedMesh(new THREE.PlaneGeometry(26, 3), new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.35 }), 90);
  ripples.userData.seeds = Array.from({ length: 90 }, () => [rnd(), rnd()]);
  scene.add(ripples);

  // finish line across the river
  const can = document.createElement('canvas'); can.width = 64; can.height = 16;
  const cx2 = can.getContext('2d'); for (let i = 0; i < 16; i++) for (let j = 0; j < 4; j++) { cx2.fillStyle = (i + j) % 2 ? '#fff' : '#222'; cx2.fillRect(i * 4, j * 4, 4, 4); }
  const tex = new THREE.CanvasTexture(can); tex.magFilter = THREE.NearestFilter;
  const fy = -course.length, fw = course.width(fy) + 40;
  const finish = new THREE.Mesh(new THREE.PlaneGeometry(fw, 30), new THREE.MeshBasicMaterial({ map: tex }));
  finish.rotation.x = -Math.PI / 2; finish.position.set(course.cx(fy), 1, fy); scene.add(finish);
  for (const side of [-1, 1]) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 120, 8), new THREE.MeshLambertMaterial({ color: '#eeeeee' }));
    post.position.set(course.cx(fy) + side * (fw / 2 + 10), 60, fy); scene.add(post);
  }
  const banner = new THREE.Mesh(new THREE.BoxGeometry(fw + 20, 22, 4), new THREE.MeshBasicMaterial({ map: tex }));
  banner.position.set(course.cx(fy), 118, fy); scene.add(banner);
}

function buildTrees(course) {
  const rnd = rngFrom(11), spots = [];
  for (let y = 300; y > -course.length - 700; y -= 55) for (const side of [-1, 1]) {
    if (rnd() < 0.35) continue;
    const hw = course.width(y) / 2, x = course.cx(y) + side * (hw + 90 + rnd() * 650);
    spots.push([x, y, rnd(), 0.7 + rnd() * 0.7]);
  }
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(6, 9, 60, 6), new THREE.MeshLambertMaterial({ color: '#6d4c41' }), spots.length);
  const pines = new THREE.InstancedMesh(new THREE.ConeGeometry(42, 120, 7), new THREE.MeshLambertMaterial({ color: '#2e6b2f', flatShading: true }), spots.length);
  const rounds = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(48, 0), new THREE.MeshLambertMaterial({ color: '#3f8f3a', flatShading: true }), spots.length);
  let np = 0, nr = 0;
  spots.forEach(([x, y, kind, sc], i) => {
    s.set(sc, sc, sc); m.compose(p.set(x, 40 + 30 * sc, y), q, s); trunks.setMatrixAt(i, m);
    if (kind < 0.5) { m.compose(p.set(x, 40 + 60 * sc + 50 * sc, y), q, s); pines.setMatrixAt(np++, m); }
    else { m.compose(p.set(x, 40 + 60 * sc + 30 * sc, y), q, s); rounds.setMatrixAt(nr++, m); }
  });
  pines.count = np; rounds.count = nr;
  scene.add(trunks, pines, rounds);
}

// ------------------------------------------------------------------ things in the river
function starShape() {
  const sh = new THREE.Shape();
  for (let i = 0; i < 10; i++) { const r = i % 2 ? 7 : 16, a = i * Math.PI / 5; const x = Math.sin(a) * r, y = Math.cos(a) * r; i ? sh.lineTo(x, y) : sh.moveTo(x, y); }
  sh.closePath(); return sh;
}
function buildItems(items) {
  const rockMat = new THREE.MeshLambertMaterial({ color: '#8a8a8a', flatShading: true });
  const logMat = new THREE.MeshLambertMaterial({ color: '#8d5a2b' }), endMat = new THREE.MeshLambertMaterial({ color: '#c9a06e' });
  const beanMat = new THREE.MeshLambertMaterial({ color: '#a8492c' });
  const starMat = new THREE.MeshStandardMaterial({ color: '#ffd600', emissive: '#7a5c00', metalness: 0.3, roughness: 0.4 });
  const starGeo = new THREE.ExtrudeGeometry(starShape(), { depth: 5, bevelEnabled: false });
  const whirlMat = new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.4 });
  for (const it of items) {
    let mesh;
    if (it.kind === 'rock') {
      mesh = new THREE.Mesh(new THREE.DodecahedronGeometry(it.r, 0), rockMat);
      mesh.scale.set(1, 0.7, 1); mesh.rotation.set(Math.random(), Math.random(), 0); mesh.position.set(it.x, 2, it.y);
    } else if (it.kind === 'log') {
      mesh = new THREE.Group();
      const body = new THREE.Mesh(new THREE.CylinderGeometry(11, 11, it.len, 10, 1, true), logMat); body.rotation.z = Math.PI / 2; mesh.add(body);
      for (const e of [-1, 1]) { const cap = new THREE.Mesh(new THREE.CircleGeometry(11, 10), endMat); cap.position.x = e * it.len / 2; cap.rotation.y = e * Math.PI / 2; mesh.add(cap); }
      mesh.position.y = 3;
    } else if (it.kind === 'whirl') {
      mesh = new THREE.Group();
      for (let k = 0; k < 3; k++) { const ring = new THREE.Mesh(new THREE.TorusGeometry(it.r * (0.3 + k * 0.3), 2.2, 4, 28, Math.PI * 1.4), whirlMat); ring.rotation.x = -Math.PI / 2; ring.rotation.z = k * 2; mesh.add(ring); }
      mesh.position.set(it.x, 1.5, it.y); whirls.push([mesh, it]);
    } else if (it.kind === 'bean') {
      mesh = new THREE.Group();
      for (const dx of [-7, 7]) { const b = new THREE.Mesh(new THREE.SphereGeometry(7, 10, 8), beanMat); b.scale.set(1, 0.8, 1.6); b.position.x = dx; b.rotation.y = dx * 0.04; mesh.add(b); }
      mesh.position.set(it.x, 8, it.y);
    } else if (it.kind === 'star') {
      mesh = new THREE.Mesh(starGeo, starMat); mesh.position.set(it.x, 24, it.y);
    }
    scene.add(mesh); itemMeshes.push([mesh, it]);
  }
}

// ------------------------------------------------------------------ boat and crew
function boatShape(L, Wd) {
  const s = new THREE.Shape();
  s.moveTo(0, -L / 2 - 8);
  s.quadraticCurveTo(Wd / 2 + 6, -L / 4, Wd / 2, L / 2);
  s.lineTo(-Wd / 2, L / 2);
  s.quadraticCurveTo(-Wd / 2 - 6, -L / 4, 0, -L / 2 - 8);
  return s;
}
function buildBoat(seats) {
  boatGroup = new THREE.Group();
  const hullGeo = new THREE.ExtrudeGeometry(boatShape(120, 74), { depth: 26, bevelEnabled: true, bevelSize: 3, bevelThickness: 3, bevelSegments: 2 });
  hullGeo.rotateX(Math.PI / 2); hullGeo.translate(0, 26, 0);   // shape y → boat length (z), extrusion → height
  const hull = new THREE.Mesh(hullGeo, new THREE.MeshLambertMaterial({ color: '#8b5a2b' }));
  hull.position.y = -14; boatGroup.add(hull);
  const deckGeo = new THREE.ShapeGeometry(boatShape(106, 62)); deckGeo.rotateX(Math.PI / 2);
  const deck = new THREE.Mesh(deckGeo, new THREE.MeshLambertMaterial({ color: '#c08a52', side: THREE.DoubleSide })); deck.position.y = 10; boatGroup.add(deck);
  for (let z = -20; z <= 40; z += 15) { const plank = new THREE.Mesh(new THREE.BoxGeometry(56, 1, 2), new THREE.MeshLambertMaterial({ color: '#9c6b3b' })); plank.position.set(0, 10.6, z); boatGroup.add(plank); }
  scene.add(boatGroup);
  // puffballs: round fuzzy-topped critters with big eyes, one per seat, in each player's colour
  const eyeMat = new THREE.MeshBasicMaterial({ color: '#ffffff' }), pupilMat = new THREE.MeshBasicMaterial({ color: '#111111' });
  critters = seats.map(s => {
    const g = new THREE.Group(), r = seats.length === 4 ? 14 : 16;
    const body = new THREE.Mesh(new THREE.SphereGeometry(r, 18, 14), new THREE.MeshLambertMaterial({ color: s.color })); g.add(body);
    const tuft = new THREE.Mesh(new THREE.ConeGeometry(r * 0.35, r * 0.6, 6), new THREE.MeshLambertMaterial({ color: s.color })); tuft.position.y = r * 1.05; tuft.rotation.z = 0.3; g.add(tuft);
    for (const ex of [-0.38, 0.38]) {
      const eye = new THREE.Mesh(new THREE.SphereGeometry(r * 0.3, 10, 8), eyeMat); eye.position.set(ex * r, r * 0.3, -r * 0.82); g.add(eye);
      const pupil = new THREE.Mesh(new THREE.SphereGeometry(r * 0.14, 8, 6), pupilMat); pupil.position.set(ex * r, r * 0.32, -r * 1.06); g.add(pupil);
    }
    g.position.set(s.x, 10 + r * 0.9, s.y);
    boatGroup.add(g);
    return { g, body, r, s };
  });
  const puffMat = () => new THREE.MeshLambertMaterial({ color: '#a8cf5a', transparent: true, opacity: 0.5, depthWrite: false });
  for (let i = 0; i < 90; i++) { const p = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 6), puffMat()); p.visible = false; scene.add(p); puffMeshes.push(p); }
}

// ------------------------------------------------------------------ per frame
function wrapAngle(a) { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; }
function render(time) {
  const dt = Math.min(0.1, Math.max(0, time - lastT)); lastT = time;
  const b = st.boat, bob = Math.sin(time * 2.2) * 1.5;
  boatGroup.position.set(b.x, bob, b.y);
  boatGroup.rotation.set(Math.sin(time * 1.7) * 0.03 + b.w * 0.05, -b.a, -b.w * 0.08);
  for (const c of critters) {
    const puff = c.s.puff > 0 ? 1 + c.s.puff * 0.9 : 1;
    c.g.scale.set(puff, 1 + (puff - 1) * 0.4, puff);
    c.g.position.y = 10 + c.r * 0.9 + (c.s.puff > 0 ? c.s.puff * 18 : Math.sin(time * 5 + c.s.x) * 0.8);   // little hop on a big toot
  }
  for (const [m, it] of itemMeshes) {
    if (it.kind === 'log') { m.position.x = it.x; m.position.z = it.y; m.rotation.y = -it.a; }
    else if (it.kind === 'bean') { m.visible = !it.got; m.position.y = 8 + Math.sin(time * 3 + it.x) * 3; m.rotation.y = time; }
    else if (it.kind === 'star') { m.visible = !it.got; m.rotation.y = time * 2; m.position.y = 24 + Math.sin(time * 2 + it.x) * 4; }
  }
  for (const [m, it] of whirls) m.rotation.y = -time * 2.5 * it.dir;
  // toot clouds
  puffMeshes.forEach((m, i) => {
    const p = st.puffs[st.puffs.length - 1 - i];
    if (!p) { m.visible = false; return; }
    m.visible = true; const sc = p.r * 0.55 * (1 + p.t * 0.8);
    m.scale.set(sc, sc, sc); m.position.set(p.x, 18 + p.t * 40, p.y); m.material.opacity = 0.45 * (1 - p.t / p.life);
  });
  // ripples near the boat, flowing downstream
  const mtx = new THREE.Matrix4(), q = new THREE.Quaternion().setFromEuler(new THREE.Euler(-Math.PI / 2, 0, 0)), one = new THREE.Vector3(1, 1, 1), v = new THREE.Vector3();
  ripples.userData.seeds.forEach(([u, w], i) => {
    const span = 1400, y = b.y - 1000 + ((w * span + time * 30) % span);
    const hw = st.course.width(y) / 2 - 10, x = st.course.cx(y) - hw + u * hw * 2;
    mtx.compose(v.set(x, 0.6, y), q, one); ripples.setMatrixAt(i, mtx);
  });
  ripples.instanceMatrix.needsUpdate = true;
  // chase camera: behind and above, turning smoothly with the boat
  camHead += wrapAngle(b.a - camHead) * Math.min(1, dt * 2.2);
  const fx = Math.sin(camHead), fz = -Math.cos(camHead);
  const want = new THREE.Vector3(b.x - fx * 300, 210, b.y - fz * 300);
  if (!camPos) camPos = want.clone(); else camPos.lerp(want, Math.min(1, dt * 3));
  camera.position.copy(camPos);
  camera.lookAt(b.x + fx * 170, 0, b.y + fz * 170);
  renderer.render(scene, camera);
}

window.Boat3D = {
  supported,
  init(container, before, state) {
    st = state;
    renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' });
    renderer.setPixelRatio(1); renderer.setSize(W, H, false);
    Object.assign(renderer.domElement.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh' });
    container.insertBefore(renderer.domElement, before);
    scene = new THREE.Scene();
    scene.background = new THREE.Color('#9fd6ff');
    scene.fog = new THREE.Fog('#9fd6ff', 900, 2600);
    camera = new THREE.PerspectiveCamera(55, W / H, 5, 3200);
    scene.add(new THREE.HemisphereLight('#e6f6ff', '#4f7f35', 0.95));
    const sun = new THREE.DirectionalLight('#ffffff', 1.0); sun.position.set(400, 700, 300); scene.add(sun);
    critters = []; itemMeshes = []; puffMeshes = []; whirls = []; camPos = null; camHead = state.boat.a; lastT = 0;
    buildTerrain(state.course); buildTrees(state.course); buildItems(state.items); buildBoat(state.seats);
  },
  render(time) { render(time); },
  // screen position (in the 960x540 overlay) of a world point at a height
  project(x, y, h) {
    const v = new THREE.Vector3(x, h || 0, y).project(camera);
    return v.z < 1 ? [(v.x + 1) / 2 * W, (1 - v.y) / 2 * H] : null;
  },
  dispose() {
    if (!renderer) return;
    renderer.domElement.remove();
    scene.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) [].concat(o.material).forEach(m => { if (m.map) m.map.dispose(); m.dispose(); }); });
    renderer.dispose(); renderer = null;
  },
};
