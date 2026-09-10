/*
 * 小忆桌宠 · Web 渲染层 (Task 1)
 * VRM 加载、渲染循环、7 状态动画机、程序化动作、身体部位点击上报
 *
 * 对外接口（window.petX）：
 *   petX.init(readyCallback, errorCallback)
 *   petX.setState(state)      state ∈ idle/talk/think/listen/sleep/happy/sad
 *   petX.setEmotion(name)     name ∈ happy/normal/bored/lonely
 *   petX.lipSync(on)          口型开关
 *   petX.playAction(action)   action ∈ hello/pet/feed/hit_tail
 *   petX.onBodyPart(callback) callback("head"|"body"|"tail"|"ear")
 */
import * as THREE from 'three';
import { GLTFLoader } from './loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from './three-vrm.module.js';

const DEG = Math.PI / 180;

const CHIBI_HEAD_SCALE = 1.5;   // Q版大头倍数

const MODEL_URL = (() => {
  const params = new URLSearchParams(window.location.search);
  return params.get('model') || window.PETX_MODEL_URL || './cat.vrm';
})();

/* 性能档位 → 渲染质量 */
const PERF_MODE = (() => {
  const params = new URLSearchParams(window.location.search);
  return params.get('pfm') || 'high';
})();
const LOW_QUALITY = PERF_MODE === 'low';   // 关抗锯齿 + 降 pixelRatio
/* 目标渲染帧率上限（Chromium 空闲节流：低档 20fps 足够呼吸/眨眼平滑，省CPU） */
const TARGET_FPS = PERF_MODE === 'low' ? 20 : 30;
const FRAME_INTERVAL_MS = 1000 / TARGET_FPS;

const EXPR_PRESETS = [
  'aa', 'ih', 'ou', 'ee', 'oh',
  'blink', 'blinkLeft', 'blinkRight',
  'lookDown', 'lookLeft', 'lookRight', 'lookUp',
  'neutral', 'happy', 'angry', 'sad', 'relaxed', 'surprised',
];

/* state -> 目标参数（骨骼角度单位 deg，长度单位 m） */
const STATE_DEFS = {
  idle:   { expr: 'neutral', exprW: 0.9,  head: [0, 0, 0],    lean: 0,   bounce: 0.000, bounceFreq: 2.0, breath: 1.0, tail: 1.0, blink: 0,  blinkRate: 1.0, blinkDur: 0.16 },
  talk:   { expr: 'happy',   exprW: 0.9,  head: [-3, 0, 0],   lean: 3,   bounce: 0.010, bounceFreq: 3.2, breath: 1.5, tail: 2.2, blink: 0,  blinkRate: 1.0, blinkDur: 0.14 },
  think:  { expr: 'neutral', exprW: 0.85, head: [6, -6, 8],   lean: 0,   bounce: 0.003, bounceFreq: 1.1, breath: 0.85, tail: 0.5, blink: 0, blur: 0, blinkRate: 0.7, blinkDur: 0.20 },
  listen: { expr: 'neutral', exprW: 0.9,  head: [5, 7, 0],    lean: 4,   bounce: 0.005, bounceFreq: 1.6, breath: 1.15, tail: 1.3, blink: 0, blinkRate: 0.9, blinkDur: 0.17 },
  sleep:  { expr: 'relaxed', exprW: 0.95, head: [12, 0, 4],   lean: 7,   bounce: 0.000, bounceFreq: 1.0, breath: 2.2, tail: 0.12, blink: 1, blinkRate: 0.2, blinkDur: 1.00 },
  happy:  { expr: 'happy',   exprW: 1.0,  head: [-6, 0, 3],   lean: -3,  bounce: 0.022, bounceFreq: 5.5, breath: 1.7, tail: 3.5, blink: 0,  blinkRate: 1.3, blinkDur: 0.13 },
  sad:    { expr: 'sad',     exprW: 1.0,  head: [14, 0, -4],  lean: 3,   bounce: 0.002, bounceFreq: 1.2, breath: 0.75, tail: 0.1, blink: 0,  blinkRate: 0.6, blinkDur: 0.22 },
};

/* 基础姿态：T-pose → 自然下垂（A-pose）
 * 所有状态/动作的基底偏移（deg）。旋转轴规则（VRM 归一化骨骼局部空间）：
 *   upperArm 绕 Z 轴 → 手臂放下；lowerArm 绕 Z 轴 → 小臂微收
 * 若无该骨骼（罕见模型）eng.add 自动 no-op，安全降级 */
const ARMS_DOWN = {
  leftUpperArm: [0, 0, 58],
  leftLowerArm: [0, 0, 14],
  rightUpperArm: [0, 0, -58],
  rightLowerArm: [0, 0, -14],
};

const EMOTION_DEFS = {
  normal: { expr: 'neutral', exprW: 0.0, head: [0, 0, 0], breath: 1.0, tail: 1.0 },
  happy:  { expr: 'happy',   exprW: 0.55, head: [-4, 0, 2], breath: 1.3, tail: 1.6 },
  bored:  { expr: 'neutral', exprW: 0.0, head: [5, 4, 3], breath: 0.7, tail: 0.3 },
  lonely: { expr: 'sad',     exprW: 0.45, head: [8, 0, -2], breath: 0.9, tail: 0.4 },
};

/* 程序化动作：p = 进度 0..1，env = 包络（0 -> 1 -> 0） */
const ACTION_DEFS = {
  hello: { duration: 2.2, apply(env, p, t, bones, setExpr) {
    const sway = Math.sin(p * Math.PI * 6) * env;
    bones.armL.add(0, sway * 32 * DEG, -sway * 18 * DEG);
    bones.armR.add(sway * 5 * DEG, 0, -sway * 5 * DEG);
    bones.head.add(0, sway * 6 * DEG, sway * 5 * DEG);
    setExpr('happy', env * 0.7);
  } },
  pet: { duration: 2.4, apply(env, p, t, bones, setExpr) {
    const w = Math.sin(p * Math.PI * 4) * env;
    bones.head.add(-w * 8 * DEG, w * 5 * DEG, w * 7 * DEG);
    bones.spine.add(-w * 3 * DEG, 0, 0);
    bones.armL.add(0, -env * 14 * DEG, env * 12 * DEG);
    setExpr('happy', env * 0.6);
  } },
  feed: { duration: 1.8, apply(env, p, t, bones, setExpr) {
    const hop = Math.abs(Math.sin(Math.PI * p)) * env;
    bones.hipsPos.add(0, hop * 0.10, 0);
    bones.head.add(-hop * 11 * DEG, 0, hop * 5 * DEG);
    setExpr('happy', env * 0.85);
  } },
  hit_tail: { duration: 1.6, apply(env, p, t, bones, setExpr) {
    const decay = env * (1 - p * 0.7);
    bones.tail.add(Math.sin(t * 22 * Math.PI) * decay * 14 * DEG, 0, 0);
    bones.tail.add(0, Math.cos(p * Math.PI * 5) * decay * 24 * DEG, 0);
    bones.head.add(decay * 10 * DEG, 0, decay * 12 * DEG);
    setExpr('surprised', env * 0.8);
  } },
};

/* bones：rest 姿态 + 每帧增量（deg） */
function makeBoneBook(vrm) {
  const book = {
    nodes: {},
    restQ: {},
    restPos: {},
    armL: { add: () => {} , used: false }, // 无手臂时 no-op
  };
  const bones = ['hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
    'leftUpperArm', 'leftLowerArm', 'rightUpperArm', 'rightLowerArm'];
  for (const name of bones) {
    const node = vrm.humanoid.getNormalizedBoneNode(name);
    if (!node) continue;
    book.nodes[name] = node;
    book.restQ[name] = node.quaternion.clone();
    book.restPos[name] = node.position.clone();
  }
  book.tail = [];
  book.tailRest = {};
  book.tailNames = [];
  vrm.scene.traverse(obj => {
    if (obj.isBone && /tail/i.test(obj.name)) {
      book.tail.push(obj);
      book.tailNames.push(obj.name);
      book.tailRest[obj.name] = { x: obj.rotation.x, y: obj.rotation.y, z: obj.rotation.z };
    }
  });
  return book;
}

function boneDeltaEngine(book) {
  /* 每帧从 rest 姿态出发施加增量（deg/m），支持动作叠加 */
  const deltas = {};    // normalized 骨骼名 -> {x,y,z}(deg)
  const posDeltas = {}; // normalized 骨骼名 -> Vector3 增量
  const tailDeltas = {};// 尾巴骨链 name -> {x,y,z}(deg)

  function node(name) { return book.nodes[name]; }

  function add(name, x, y, z) {
    if (!node(name)) return;
    const d = deltas[name] || (deltas[name] = { x: 0, y: 0, z: 0 });
    d.x += x; d.y += y; d.z += z;
  }
  function addPos(name, x, y, z) {
    if (!node(name)) return;
    const d = posDeltas[name] || (posDeltas[name] = { x: 0, y: 0, z: 0 });
    d.x += x; d.y += y; d.z += z;
  }
  function addTail(name, x, y, z) {
    const d = tailDeltas[name] || (tailDeltas[name] = { x: 0, y: 0, z: 0 });
    d.x += x; d.y += y; d.z += z;
  }
  function isFin3(o) {
    return Number.isFinite(o.x) && Number.isFinite(o.y) && Number.isFinite(o.z);
  }
  function flush() {
    for (const name in deltas) {
      if (!isFin3(deltas[name])) { console.warn('[petX] 骨骼增量含 NaN，跳过该帧:', name); continue; }
      const e = new THREE.Euler(deltas[name].x * DEG, deltas[name].y * DEG, deltas[name].z * DEG, 'XYZ');
      node(name).quaternion.copy(book.restQ[name]).multiply(new THREE.Quaternion().setFromEuler(e));
    }
    for (const name in posDeltas) {
      if (!isFin3(posDeltas[name])) { console.warn('[petX] 骨骼位移增量含 NaN，忽略:', name); continue; }
      const n = node(name);
      const v = new THREE.Vector3(posDeltas[name].x, posDeltas[name].y, posDeltas[name].z)
        .applyQuaternion(book.restQ[name]);
      n.position.copy(book.restPos[name]).add(v);
    }
    for (const bone of book.tail) {
      const d = tailDeltas[bone.name];
      const rest = book.tailRest[bone.name];
      if (!d || !isFin3(d)) continue;
      bone.rotation.x = rest.x + d.x * DEG;
      bone.rotation.y = rest.y + d.y * DEG;
      bone.rotation.z = rest.z + d.z * DEG;
    }
    /* 关键：每帧清零增量，避免跨帧累积 */
    for (const name in deltas) { deltas[name].x = 0; deltas[name].y = 0; deltas[name].z = 0; }
    for (const name in posDeltas) { posDeltas[name].x = 0; posDeltas[name].y = 0; posDeltas[name].z = 0; }
    for (const name in tailDeltas) { tailDeltas[name].x = 0; tailDeltas[name].y = 0; tailDeltas[name].z = 0; }
  }
  return { add, addPos, addTail, flush };
}

const petX = {
  version: '1.0.0',
  ready: false,
  modelInfo: null,
  state: 'idle',
  emotion: 'normal',
  lipSyncEnabled: true,
  config: {
    modelUrl: MODEL_URL,
    initialState: 'idle',
  },
};

let _scene, _camera, _renderer, _vrm = null;
let _book = null, _engine = null;
let _chibiHeadBone = null;   // Q版头部缩放目标（每帧施加以免被 _vrm.update 重置）
let _initialized = false, _loadStarted = false;
let _readyCb = null, _errCb = null;
let _canvasEl = null, _statusEl = null;
let _union = null;        // hit zones
let _hitZones = [];
let _bodyPartCb = null;
let _currentActionKey = null, _currentActionStart = -1;

/* ---------------- 状态量（当前值 + 目标值） ---------------- */
const TARGET = { head: [0, 0, 0], exprW: {}, lean: 0, bounce: 0, bounceFreq: 2, breath: 1, tail: 1, blink: 0, blinkRate: 1, blinkDur: 0.16 };
const CURRENT = makeCurrent();

function makeCurrent() {
  return {
    head: [0, 0, 0], exprW: {}, lean: 0, bounce: 0, bounceFreq: 2, breath: 1,
    tail: 1, blink: 0, blinkRate: 1, blinkDur: 0.16,
    actionEnv: 0,
  };
}

/* ---------------- 浏览器渲染基础 ---------------- */
function setupRenderer(canvas) {
  _renderer = new THREE.WebGLRenderer({ canvas, antialias: !LOW_QUALITY, alpha: true });
  // low 档：限制 pixelRatio=1（省GPU）；medium/high 允许到 2
  const maxPR = LOW_QUALITY ? 1 : 2;
  _renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxPR));
  _renderer.setClearColor(0x000000, 0);
  _renderer.outputColorSpace = THREE.SRGBColorSpace;

  _scene = new THREE.Scene();

  _camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 0.01, 100);
  _camera.position.set(0, 1.4, 4);

  // 增强光照：桌面宠物窗口小、避免剪影，改用半球光 + 多向平行光
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(1.5, 2.5, 2.5);
  _scene.add(key);
  const fill = new THREE.DirectionalLight(0xddddff, 0.7);
  fill.position.set(-2, 1, 2);
  _scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.6);
  rim.position.set(0, 2, -2.5);
  _scene.add(rim);
  _scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7fa0, 1.1));
  _scene.add(new THREE.AmbientLight(0xffffff, 0.85));

  window.addEventListener('resize', onResize);
  onResize();
}

function onResize() {
  const w = window.innerWidth, h = window.innerHeight;
  if (_renderer) {
    _renderer.setSize(w, h, false);
    _camera.aspect = w / h;
    _camera.updateProjectionMatrix();
  }
}

function setStatus(txt, isError) {
  if (!_statusEl) return;
  if (txt == null) {
    _statusEl.classList.add('hidden');
  } else {
    _statusEl.textContent = txt;
    _statusEl.classList.toggle('error', !!isError);
    _statusEl.classList.remove('hidden');
  }
}

/* ---------------- 模型加载 ---------------- */
function loadModel() {
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser, {
    // 接受 VRM 官方许可与自定义许可，避免 license 校验抛异常
    acceptLicenseUrls: ['https://vrm.dev/licenses/1.0/', 'https://vrm.dev/licenses/1.0'],
  }));
  setStatus('加载模型 ' + petX.config.modelUrl + ' …');
  loader.load(petX.config.modelUrl, onModelLoaded, undefined, err => {
    const msg = '模型加载失败: ' + (petX.config.modelUrl) + ' ' + ((err && err.message) || err);
    setStatus(msg, true);
    console.error('[petX]', msg);
    console.error('[petX] stack:', (err && err.stack) || '(no stack)');
    if (_errCb) { try { _errCb(msg); } catch (_) {} }
  });
}

function onModelLoaded(gltf) {
  const vrm = gltf.userData.vrm;
  _vrm = vrm;
  try { VRMUtils.removeUnnecessaryJoints(vrm.scene); } catch (_) {}

  _scene.add(vrm.scene);

  // 让角色正面朝向镜头：VRoid 常默认面向 -Z，相机在 +Z，故绕 Y 转 180°
  try { vrm.scene.rotation.y = Math.PI; } catch (_) {}

  if (vrm.lookAt) {
    try { vrm.lookAt.target = _camera; vrm.lookAt.autoUpdate = true; } catch (_) {}
  }
  vrm.scene.traverse(o => { if (o.isMesh) { o.frustumCulled = false; } });

  // 各初始化步骤加保护：任意一步失败都不阻断渲染（模型已 add 进场景）
  try { _book = makeBoneBook(vrm); } catch (_) { console.warn('[petX] makeBoneBook failed', _); }
  try { _engine = boneDeltaEngine(_book); } catch (_) {}
  try { upTargets(); } catch (_) {}
  try { buildHitZones(vrm); } catch (_) { console.warn('[petX] buildHitZones failed', _); }
  try { makeChibi(vrm); } catch (_) { console.warn('[petX] makeChibi failed', _); }
  try { fitCamera(vrm.scene); } catch (_) { console.warn('[petX] fitCamera failed', _); }

  petX.ready = true;
  const foundExprs = [];
  try {
    const em = vrm.expressionManager;
    for (const name of EXPR_PRESETS) {
      if (em && em.getExpression(name)) foundExprs.push(name);
    }
  } catch (_) {}
  petX.modelInfo = {
    name: (gltf.parser && gltf.parser.json && gltf.parser.json.extensions &&
      gltf.parser.json.extensions.VRMC_vrm && gltf.parser.json.extensions.VRMC_vrm.meta &&
      gltf.parser.json.extensions.VRMC_vrm.meta.name) || 'unknown',
    expressions: foundExprs,
  };
  setStatus(null);
  console.error('[petX-DEBUG] model ready OK, name=' + petX.modelInfo.name + ', expressions=' + foundExprs.length);

  if (_readyCb) { try { _readyCb({ ok: true, model: petX.modelInfo }); } catch (_) {} }
  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'model_ready', model: petX.modelInfo });
}

/* Q版大头：放大头部骨骼链，让小窗里角色头部占据更大比例。
 * 同时放大头部 mesh（头发/脸）随骨骼缩放。用场景里的原始骨骼以避免被 _vrm.update 重置。 */
function makeChibi(vrm) {
  const HEAD_SCALE = CHIBI_HEAD_SCALE;
  // 1) 场景内找 head 骨（VRM 常见命名 *_head / *_Head / head）
  let headBone = null;
  vrm.scene.traverse(o => {
    if (!headBone && o.isBone && /head$/i.test(o.name)) headBone = o;
  });
  // 2) 归一化 head 骨兜底
  if (!headBone) headBone = vrm.humanoid.getNormalizedBoneNode('head');
  _chibiHeadBone = headBone;
  if (headBone) {
    headBone.scale.setScalar(HEAD_SCALE);
  }
}

function fitCamera(scene) {
  // Q版：以头部为瞄准中心，拉近取景（头部+上半身），让脸占窗口主导
  const box = new THREE.Box3().setFromObject(scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const h = Math.max(size.y, 0.2);

  // 头顶 = 包围盒最高点（含 Q版放大后的头发），据此留出头顶余量，保证不裁发顶
  const topY = box.max.y;
  const frameH = h * 0.50;                       // 取景高度（聚焦，让头占主导）
  const HEAD_MARGIN = h * 0.12;                  // 头顶上方留白（略大，容忍透视压缩+呼吸晃动）
  // 让可见区顶部 = 发顶 + 余量：targetY + frameH/2 = topY + HEAD_MARGIN
  const targetY = (topY + HEAD_MARGIN) - frameH / 2;
  const dist = (frameH / 2) / Math.tan((_camera.fov / 2) * DEG) * 1.02;
  _camera.position.set(center.x, targetY, center.z + Math.max(dist, size.z * 0.6 + 0.4));
  _camera.lookAt(center.x, targetY, center.z);
  _camera.updateProjectionMatrix();
  console.error('[petX-DEBUG] fitCamera box=(' + size.x.toFixed(2) + ',' + size.y.toFixed(2) + ',' + size.z.toFixed(2) +
    ') h=' + h.toFixed(2) + ' topY=' + topY.toFixed(2) + ' frameH=' + frameH.toFixed(2) +
    ' targetY=' + targetY.toFixed(2) + ' dist=' + dist.toFixed(2) + ' aspectW=' + window.innerWidth +
    ' aspectH=' + window.innerHeight + ' fov=' + _camera.fov);
}

/* ---------------- 点击 / 身体部位上报 ---------------- */
function buildHitZones(vrm) {
  _union = new THREE.Group();
  _hitZones = [];
  const addZone = (part, center, radius) => {
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 12, 10),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }),
    );
    sphere.position.copy(center);
    sphere.userData.part = part;
    _union.add(sphere);
    _hitZones.push({ part, center, radius });
  };

  const modelHeight = new THREE.Box3().setFromObject(vrm.scene).getSize(new THREE.Vector3()).y || 1.5;
  const head = vrm.humanoid.getNormalizedBoneNode('head');
  const spine = vrm.humanoid.getNormalizedBoneNode('spine');
  const hips = vrm.humanoid.getNormalizedBoneNode('hips');
  const earNodes = [];
  vrm.scene.traverse(o => { if (o.isBone && /ear/i.test(o.name)) earNodes.push(o); });

  if (head) {
    const hp = head.getWorldPosition(new THREE.Vector3());
    addZone('head', hp.clone().add(new THREE.Vector3(0, modelHeight * 0.03, 0)), modelHeight * 0.15);
    if (earNodes.length) {
      for (const e of earNodes) {
        addZone('ear', e.getWorldPosition(new THREE.Vector3()), Math.max(modelHeight * 0.06, 0.05));
      }
    } else {
      addZone('ear', hp.clone().add(new THREE.Vector3(modelHeight * 0.07, modelHeight * 0.08, 0)), Math.max(modelHeight * 0.06, 0.05));
      addZone('ear', hp.clone().add(new THREE.Vector3(-modelHeight * 0.07, modelHeight * 0.08, 0)), Math.max(modelHeight * 0.06, 0.05));
    }
  }
  if (_book.tail.length) {
    const last = _book.tail[_book.tail.length - 1];
    addZone('tail', last.getWorldPosition(new THREE.Vector3()), Math.max(modelHeight * 0.12, 0.12));
  } else if (hips) {
    addZone('tail', hips.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, -modelHeight * 0.02, -modelHeight * 0.09)), modelHeight * 0.13);
  }
  if (spine) {
    addZone('body', spine.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, modelHeight * 0.02, 0)), modelHeight * 0.22);
  } else if (hips) {
    addZone('body', hips.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, modelHeight * 0.3, 0)), modelHeight * 0.22);
  }

  _scene.add(_union);
}

function hitTest(evt) {
  if (!_vrm || !_union) return;
  const rect = _canvasEl.getBoundingClientRect();
  const px = evt.clientX - rect.left;
  const py = evt.clientY - rect.top;
  const ndc = new THREE.Vector2(
    (px / rect.width) * 2 - 1,
    1 - (py / rect.height) * 2,
  );
  const ray = new THREE.Raycaster();
  ray.setFromCamera(ndc, _camera);
  const hits = ray.intersectObjects(_union.children, false);
  if (!hits.length) return;

  /* 命中候选 = 被射线穿过的球体；按屏幕空间归一化距离取最优 */
  const hitZones = [];
  for (const h of hits) {
    const z = _hitZones.find(zz => zz.part === h.object.userData.part);
    if (z) hitZones.push(z);
  }
  let best = null, bestRatio = Infinity;
  const pv = new THREE.Vector3();
  for (const z of hitZones) {
    pv.copy(z.center).project(_camera);
    const sx = (pv.x * 0.5 + 0.5) * rect.width;
    const sy = (-pv.y * 0.5 + 0.5) * rect.height;
    const dist = Math.abs(z.center.z - _camera.position.z);
    const rPx = (z.radius / (2 * Math.tan((_camera.fov / 2) * DEG) * dist)) * rect.height;
    const ratio = Math.hypot(px - sx, py - sy) / Math.max(rPx, 1e-6);
    if (ratio < bestRatio) { bestRatio = ratio; best = z; }
  }
  if (!best) return;
  console.log('[petX] body part hit:', best.part);
  if (_bodyPartCb) { try { _bodyPartCb(best.part); } catch (_) {} }
  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'body_part', part: best.part });
}

/* ---------------- 状态/表达目标计算 ---------------- */
function computeTargets() {
  const s = STATE_DEFS[petX.state] || STATE_DEFS.idle;
  const e = EMOTION_DEFS[petX.emotion] || EMOTION_DEFS.normal;

  TARGET.head[0] = s.head[0] + e.head[0];
  TARGET.head[1] = s.head[1] + e.head[1];
  TARGET.head[2] = s.head[2] + e.head[2];
  TARGET.lean = s.lean;
  TARGET.bounce = s.bounce;
  TARGET.bounceFreq = s.bounceFreq;
  TARGET.breath = s.breath * e.breath;
  TARGET.tail = s.tail * e.tail;
  TARGET.blink = s.blink;              // sleep=1 → 常闭
  TARGET.blinkRate = s.blinkRate;
  TARGET.blinkDur = s.blinkDur;

  TARGET.exprW = {};
  if (s.expr) TARGET.exprW[s.expr] = s.exprW;
  if (e && e.exprW > 0 && e.expr) TARGET.exprW[e.expr] = Math.max(TARGET.exprW[e.expr] || 0, e.exprW);
}

function upTargets() { computeTargets(); }

/* ---------------- 主循环 ---------------- */
let _time = 0, _lastT = 0;
let _nextBlinkAt = 0, _blinkPhase = -1, _blinkDur = 0.16, _doubleBlink = false;
let _lastRenderMs = -1000;
function shouldRender(nowMs) {   // 帧率上限节流：不到目标帧间隔则跳过 WebGL 渲染，省 CPU
  if (nowMs - _lastRenderMs < FRAME_INTERVAL_MS) return false;
  _lastRenderMs = nowMs;
  return true;
}

/* 眨眼间隔：以"无记忆"的指数分布为主（均值 ~3.8s），偶发长停顿(走神)，
 * 偶尔短促(警觉)。真人眨眼间隔近似指数分布且方差大，避免固定节拍感。 */
function _nextBlinkInterval() {
  const r = Math.random();
  // 约 12% 概率：明显长停顿（走神/专注），5~9 s，拉大间隔方差、打破节拍
  if (r < 0.12) return 5.0 + Math.random() * 4.0;
  // 约 9% 概率：短促（被打断），1.2~2.4 s
  if (r < 0.21) return 1.2 + Math.random() * 1.2;
  // 主体：指数分布（率参数 1/3.8，均值 ~3.8s，范围 0.8~7s）
  const u = Math.random();
  const exp = -Math.log(1 - u) * 3.8 + 0.8;
  return Math.min(exp, 7.0);
}

function tick(t) {
  const now = t / 1000;
  const dt = Math.min(now - (_lastT || now), 0.05);
  _lastT = now;
  _time += dt;

  if (!_vrm) {
    if (shouldRender(t)) { renderFrame(); }
    return;
  }

  // 帧率上限节流：低于目标帧率时跳过整个计算体（骨骼/update/渲染），省 CPU。
  // 注意：_lastT/_time 仍每帧推进，保证下一真实帧的 dt 正确、动画不加速。
  if (!shouldRender(t)) { return; }

  /* 状态平滑：约 0.3s 收敛 */
  const k = 1 - Math.exp(-dt * 9);
  CURRENT.head[0] += (TARGET.head[0] - CURRENT.head[0]) * k;
  CURRENT.head[1] += (TARGET.head[1] - CURRENT.head[1]) * k;
  CURRENT.head[2] += (TARGET.head[2] - CURRENT.head[2]) * k;
  CURRENT.lean += (TARGET.lean - CURRENT.lean) * k;
  CURRENT.bounce += (TARGET.bounce - CURRENT.bounce) * k;
  CURRENT.bounceFreq += (TARGET.bounceFreq - CURRENT.bounceFreq) * k;
  CURRENT.breath += (TARGET.breath - CURRENT.breath) * k;
  CURRENT.tail += (TARGET.tail - CURRENT.tail) * k;
  CURRENT.blink += (TARGET.blink - CURRENT.blink) * k;
  CURRENT.blinkRate += (TARGET.blinkRate - CURRENT.blinkRate) * k;
  CURRENT.blinkDur += (TARGET.blinkDur - CURRENT.blinkDur) * k;
  for (const name in TARGET.exprW) {
    CURRENT.exprW[name] = (CURRENT.exprW[name] || 0) + (TARGET.exprW[name] - (CURRENT.exprW[name] || 0)) * k;
  }
  for (const name in CURRENT.exprW) {
    if (TARGET.exprW[name] === undefined) CURRENT.exprW[name] += (0 - CURRENT.exprW[name]) * k;
  }

  /* 眨眼调度 —— 更自然：指数分布间隔 + 不对称眨眼曲线 + 随机连眨 */
  let blinkPulse = 0;
  if (CURRENT.blink >= 0.99) {
    blinkPulse = 1;
    _blinkPhase = -1;
  } else {
    if (_blinkPhase < 0 && _time > _nextBlinkAt) {
      _blinkPhase = 0;
      _blinkDur = Math.max(CURRENT.blinkDur, 0.08);
      // 连眨概率：较低（约 12%），让偶尔的二次眨眼显得随机而非规律性连眨
      _doubleBlink = Math.random() < 0.12;
    }
    if (_blinkPhase >= 0) {
      if (_blinkPhase >= 1) {
        _blinkPhase = -1;
        // 眨眼间隔：指数分布（3.2s 均值），偶发超长停顿(偷懒)与短促(警戒)，
        // 比固定均匀区间更接近真人眨眼节奏
        _nextBlinkAt = _time + _nextBlinkInterval() / Math.max(CURRENT.blinkRate, 0.05);
        if (_doubleBlink) {
          _blinkPhase = -2; // double：短暂停留后再眨一次
          setTimeout(() => { if (_blinkPhase === -2) _blinkPhase = 0; }, 130 + Math.random() * 120);
        }
      } else {
        // 不对称眨眼：闭得快、睁得慢（真人眨眼不对称），用分段曲线逼近
        const p = Math.min(_blinkPhase, 1);
        if (p < 0.35) {
          blinkPulse = Math.sin(Math.PI * (p / 0.35) * 0.5); // 闭合段（前 35%）快速到 1
        } else {
          blinkPulse = 0.5 + 0.5 * Math.cos(Math.PI * ((p - 0.35) / 0.65)); // 睁开段平滑回 0
        }
        blinkPulse = Math.max(0, Math.min(1, blinkPulse));
        _blinkPhase += dt / Math.max(_blinkDur, 0.05);
        if (blinkPulse < 0.02 && _blinkPhase > 0.6) { _blinkPhase = -1; }
      }
    }
  }
  const blinkTarget = Math.max(CURRENT.blink, blinkPulse);

  /* 口型：talk + lipSync enabled → 伪随机元音驱动 */
  let mouth = 0;
  if (petX.state === 'talk' && petX.lipSyncEnabled && CURRENT.exprW['happy'] < 1) {
    mouth = Math.max(0, Math.sin(_time * 9.3) * Math.sin(_time * 4.7 + 1.3) * 0.5 +
      Math.sin(_time * 17.7 + 0.5) * 0.25);
  }

  /* 动作包络 */
  let actionEnv = 0, actionApply = null, actionDef = null;
  if (_currentActionKey) {
    actionDef = ACTION_DEFS[_currentActionKey];
    if (actionDef) {
      const p = (_time - _currentActionStart) / actionDef.duration;
      if (p >= 1) {
        _currentActionKey = null;
      } else {
        actionEnv = Math.sin(Math.PI * Math.min(Math.max(p * 3.0, 0), 1));
        if (p > 0.2) actionEnv = Math.max(actionEnv, Math.exp(-Math.max(p - 0.2, 0) * 5));
        actionApply = actionDef.apply;
      }
    }
  }
  CURRENT.actionEnv += (actionEnv - CURRENT.actionEnv) * Math.min(dt * 18, 1);
  const env = CURRENT.actionEnv;

  /* ---- 骨骼应用 ---- */
  const b = _book, eng = _engine;
  /* A-pose 基底：手臂自然下垂（先于动作/状态，动作用 add 叠加在上） */
  for (const [boneName, [ax, ay, az]] of Object.entries(ARMS_DOWN)) {
    eng.add(boneName, ax, ay, az);
  }
  b.nodes.hips && eng.add('hips', 0, 0, Math.sin(_time * CURRENT.tail * 4) * Math.min(CURRENT.tail * 2.2, 9) * DEG);
  b.nodes.hips && eng.add('hips', 0, Math.sin(_time * CURRENT.tail * 2.6) * Math.min(CURRENT.tail * 1.6, 7) * DEG, 0);
  b.nodes.spine && eng.add('spine', -CURRENT.lean * 0.8 * DEG, 0, 0);
  b.nodes.chest && eng.add('chest', Math.sin(_time * CURRENT.breath * 2) * 1.6 * Math.min(CURRENT.breath * 0.8, 2) * DEG, 0, 0);
  b.nodes.head && eng.add('head', CURRENT.head[0] * DEG, CURRENT.head[1] * DEG, CURRENT.head[2] * DEG);
  b.nodes.hips && eng.addPos('hips', 0, Math.sin(_time * CURRENT.bounceFreq * Math.PI * 2) * CURRENT.bounce, 0);

  if (actionApply) {
    const bones = {
      head: { add: (x, y, z) => eng.add('head', x, y, z) },
      spine: { add: (x, y, z) => eng.add('spine', x, y, z) },
      armL: { add: (x, y, z) => { eng.add('leftUpperArm', x, y, z); eng.add('leftLowerArm', x, y, z); } },
      armR: { add: (x, y, z) => { eng.add('rightUpperArm', x, y, z); eng.add('rightLowerArm', x, y, z); } },
      hipsPos: { add: (x, y, z) => eng.addPos('hips', x, y, z) },
      tail: {
        add: (x, y, z) => {
          if (b.tailNames.length) {
            for (const tn of b.tailNames) eng.addTail(tn, x, y, z);
          } else {
            // 无尾骨模型：降级为髋部扭转，保证动作可见
            eng.add('hips', 0, y * 0.5, 0);
          }
        },
      },
    };
    const expAdd = {};
    const setExpr = (name, w) => { expAdd[name] = Math.max(expAdd[name] || 0, w); };
    try {
      const p = Math.min((_time - _currentActionStart) / actionDef.duration, 1);
      actionApply(env, p, _time, bones, setExpr);
    } catch (e) { console.warn('[petX] action error', e); }
    for (const name in expAdd) {
      CURRENT.exprW[name] = Math.max(CURRENT.exprW[name] || 0, expAdd[name] * env);
    }
  }

  eng.flush();

  /* ---- 表达 ---- */
  const em = _vrm.expressionManager;
  for (const name of EXPR_PRESETS) {
    if (!em || !em.getExpression(name)) continue;
    const w = name === 'blink' ? blinkTarget : (CURRENT.exprW[name] || 0);
    em.setValue(name, w);
  }

  if (_vrm.update) { try { _vrm.update(dt); } catch (_) {} }

  // 每帧重施加 Q版头部缩放（update 会重置骨骼局部变换）
  if (_chibiHeadBone) { try { _chibiHeadBone.scale.setScalar(CHIBI_HEAD_SCALE); } catch (_) {} }

  renderFrame();
}

function renderFrame() {
  if (_renderer && _camera && _scene) {
    _renderer.render(_scene, _camera);
  }
}

/* ---------------- 对外接口 ---------------- */
petX.init = function (readyCb, errCb) {
  // 竞态防御：模型已就绪时（Python 经 QWebChannel 二次调用 init），立即回调
  if (petX.ready) {
    if (readyCb) { try { readyCb({ ok: true, model: petX.modelInfo }); } catch (_) {} }
    return;
  }
  _readyCb = readyCb || null;
  _errCb = errCb || null;
  if (_initialized) {
    return;
  }
  _initialized = true;

  _canvasEl = document.getElementById('petx-canvas') || document.getElementById('app-canvas');
  _statusEl = document.getElementById('petx-status') || document.getElementById('status');
  if (!_canvasEl) {
    const msg = '[petX] 找不到画布元素';
    console.error(msg);
    if (_errCb) { try { _errCb(msg); } catch (_) {} }
    return;
  }
  setupRenderer(_canvasEl);

  _canvasEl.style.cursor = 'pointer';
  // 指针交互：区分 点击(body_part) 与 拖拽(移动窗口)
  // 单击(press 后位移<6px 且 button0)才做 hitTest；拖拽时每帧上报 drag_delta
  //
  // 关键：拖拽增量必须用【屏幕绝对坐标 screenX/screenY】。
  // 若用 clientX/clientY（相对窗口），窗口移动会连带改变坐标系，
  // 使下一帧增量 = 屏幕增量 - 窗口位移，导致拖拽被"反向抵消"而拖不动。
  const _pt = { active: false, moved: false, sx: 0, sy: 0, lx: 0, ly: 0, pid: -1 };
  _canvasEl.addEventListener('pointerdown', evt => {
    if (evt.button !== 0) return;
    _pt.active = true;
    _pt.moved = false;
    _pt.pid = evt.pointerId;
    // 捕获指针，保证拖拽过程中即使指针移出画布也持续收到事件
    try { _canvasEl.setPointerCapture(evt.pointerId); } catch (_) {}
    _pt.sx = _pt.lx = evt.screenX;
    _pt.sy = _pt.ly = evt.screenY;
  });
  _canvasEl.addEventListener('pointermove', evt => {
    if (!_pt.active || evt.pointerId !== _pt.pid) return;
    const total = Math.abs(evt.screenX - _pt.sx) + Math.abs(evt.screenY - _pt.sy);
    if (!_pt.moved && total > 6) {
      _pt.moved = true;
      _canvasEl.style.cursor = 'grabbing';
    }
    if (_pt.moved) {
      const dx = evt.screenX - _pt.lx, dy = evt.screenY - _pt.ly;
      _pt.lx = evt.screenX; _pt.ly = evt.screenY;
      window.bridge && bridge.reportEvent && bridge.reportEvent({ type: 'drag_delta', dx, dy });
    }
  });
  const __pointerUp = evt => {
    if (!_pt.active) return;
    _pt.active = false;
    _canvasEl.style.cursor = 'pointer';
    try { _canvasEl.releasePointerCapture(_pt.pid); } catch (_) {}
    if (!_pt.moved && evt.button === 0) hitTest(evt);
  };
  window.addEventListener('pointerup', __pointerUp);
  window.addEventListener('pointercancel', __pointerUp);

  petX.setState(petX.config.initialState || 'idle');
  petX.setEmotion('normal');

  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'app_ready' });

  if (!_loadStarted) { _loadStarted = true; loadModel(); }

  _renderer.setAnimationLoop(tick);
};

petX.setState = function (state) {
  if (!STATE_DEFS[state]) { console.warn('[petX] 未知状态', state); return false; }
  petX.state = state;
  upTargets();
  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'state_changed', state });
  return true;
};

petX.setEmotion = function (name) {
  if (!EMOTION_DEFS[name]) { console.warn('[petX] 未知情绪', name); return false; }
  petX.emotion = name;
  upTargets();
  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'emotion_changed', emotion: name });
  return true;
};

petX.lipSync = function (on) {
  petX.lipSyncEnabled = !!on;
  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'lip_sync', on: petX.lipSyncEnabled });
  return true;
};

petX.playAction = function (action) {
  if (!ACTION_DEFS[action]) { console.warn('[petX] 未知动作', action); return false; }
  _currentActionKey = action;
  _currentActionStart = _time;
  window.bridge && window.bridge.reportEvent && window.bridge.reportEvent({ type: 'action', action: action });
  return true;
};

petX.onBodyPart = function (cb) {
  _bodyPartCb = typeof cb === 'function' ? cb : null;
};

petX.getInfo = function () {
  return {
    state: petX.state, emotion: petX.emotion, ready: petX.ready,
    lipSync: petX.lipSyncEnabled, model: petX.modelInfo, action: _currentActionKey,
  };
};

petX._debug = function () {
  const out = { zones: [], hitAt: {} };
  try {
    const p = new THREE.Vector3();
    for (const z of _hitZones) {
      p.copy(z.center);
      p.project(_camera);
      out.zones.push({
        part: z.part, radius: z.radius,
        world: { x: +z.center.x.toFixed(3), y: +z.center.y.toFixed(3), z: +z.center.z.toFixed(3) },
        screenX: Math.round((p.x * 0.5 + 0.5) * window.innerWidth),
        screenY: Math.round((-p.y * 0.5 + 0.5) * window.innerHeight),
      });
    }
    out.hitAt = (px, py) => {
      const ndc = new THREE.Vector2((px / window.innerWidth) * 2 - 1, -(py / window.innerHeight) * 2 + 1);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, _camera);
      const hits = ray.intersectObjects(_union.children, false);
      const point = hits.length ? hits[0].point : null;
      const ratios = [];
      for (const z of _hitZones) {
        ratios.push({ part: z.part, ratio: point ? +(((point.distanceTo(z.center)) / z.radius).toFixed(2)) : null });
      }
      return { hits: hits.map(h => h.object.userData.part), point: point ? { x: +point.x.toFixed(3), y: +point.y.toFixed(3), z: +point.z.toFixed(3) } : null, ratios };
    };
  } catch (e) { out.err = String(e); }
  return out;
};


window.petX = petX;

/* ---------------- 桥接指令（QWebChannel / URL 参数 fallback） ---------------- */
if (window.bridge && typeof window.bridge.setHandler === 'function') {
  window.bridge.setHandler(function (cmd) {
    switch (cmd.type) {
      case 'set_state':
        petX.setState(cmd.state);
        break;
      case 'lip_sync': {
        const v = cmd.on !== undefined ? cmd.on : cmd.value;
        petX.lipSync(!!v);
        break;
      }
      case 'set_emotion':
        petX.setEmotion(cmd.emotion || cmd.name);
        break;
      case 'play_action':
        petX.playAction(cmd.action);
        break;
      case 'ping':
        window.bridge.reportEvent({ type: 'pong' });
        break;
      default:
        console.warn('[petX] 未知指令:', cmd.type);
    }
  });
}

/* ---------------- 初始化 ---------------- */
const doInit = () => {
  if (window.petX && window.petX.init) petX.init();
};
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', doInit);
} else {
  doInit();
}


