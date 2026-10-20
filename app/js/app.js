/* 다먹을기니? — 화면 흐름: Splash → Onboarding 1~3 → Roulette ⇄ Spinning → Result */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

  const W = 390;
  const H = 844;
  const IDLE_EVERY = 2000; // 대기 중 2초마다 한 칸씩 좌→우
  const IDLE_MOVE = 700;
  const SPIN_MS = 4000;

  const stage = $("#stage");
  let scale = 1;

  /* ---------- 반응형: 390×844 무대를 화면에 맞게 ---------- */
  function fit() {
    const vw = window.visualViewport ? visualViewport.width : innerWidth;
    const vh = window.visualViewport ? visualViewport.height : innerHeight;
    scale = Math.min(vw / W, vh / H);
    stage.style.setProperty("--s", scale);
  }
  fit();
  addEventListener("resize", fit);
  window.visualViewport?.addEventListener("resize", fit);

  /* ---------- 트윈 ---------- */
  const ease = {
    inOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    outCubic: (t) => 1 - Math.pow(1 - t, 3),
    inQuad: (t) => t * t,
    outBack: (t) => {
      const c1 = 1.5, c3 = c1 + 1;
      return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
    },
  };

  function cubicBezier(x1, y1, x2, y2) {
    const bx = (t) => 3 * x1 * t * (1 - t) ** 2 + 3 * x2 * t * t * (1 - t) + t ** 3;
    const by = (t) => 3 * y1 * t * (1 - t) ** 2 + 3 * y2 * t * t * (1 - t) + t ** 3;
    return (x) => {
      let lo = 0, hi = 1, t = x;
      for (let i = 0; i < 24; i++) {
        t = (lo + hi) / 2;
        if (bx(t) < x) lo = t; else hi = t;
      }
      return by(t);
    };
  }
  // 천천히 출발 → 빠르게 → 길게 감속, 끝에서 살짝 지나쳤다 돌아오며 "찰칵" 멈춤
  const spinEase = cubicBezier(0.3, 0, 0.1, 1.035);

  function tween({ duration, ease: fn = (t) => t, onUpdate, onDone }) {
    const start = performance.now();
    let raf = 0, stopped = false;
    const step = (now) => {
      if (stopped) return;
      const t = duration <= 0 ? 1 : clamp((now - start) / duration, 0, 1);
      onUpdate(fn(t), t);
      if (t < 1) raf = requestAnimationFrame(step);
      else onDone?.();
    };
    raf = requestAnimationFrame(step);
    return () => { stopped = true; cancelAnimationFrame(raf); };
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  /* ---------- 효과음 (Web Audio로 합성) ---------- */
  const sfx = {
    ctx: null,
    lastTick: 0,
    init() {
      if (this.ctx) return;
      try {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch { this.ctx = null; }
    },
    resume() { this.ctx?.state === "suspended" && this.ctx.resume(); },
    noise(dur) {
      const b = this.ctx.createBuffer(1, Math.ceil(this.ctx.sampleRate * dur), this.ctx.sampleRate);
      const d = b.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
      const s = this.ctx.createBufferSource();
      s.buffer = b;
      return s;
    },
    click(at, freq, gain, dur = 0.035) {
      const c = this.ctx, n = this.noise(dur), f = c.createBiquadFilter(), g = c.createGain();
      f.type = "bandpass"; f.frequency.value = freq; f.Q.value = 1.6;
      g.gain.setValueAtTime(gain, at);
      g.gain.exponentialRampToValueAtTime(0.001, at + dur);
      n.connect(f).connect(g).connect(c.destination);
      n.start(at);
    },
    thump(at, from, to, gain, dur) {
      const c = this.ctx, o = c.createOscillator(), g = c.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(from, at);
      o.frequency.exponentialRampToValueAtTime(to, at + dur);
      g.gain.setValueAtTime(gain, at);
      g.gain.exponentialRampToValueAtTime(0.001, at + dur);
      o.connect(g).connect(c.destination);
      o.start(at); o.stop(at + dur + 0.02);
    },
    // 손잡이를 누를 때: "철-컥"
    press() {
      if (!this.ctx) return;
      const t = this.ctx.currentTime;
      this.click(t, 2600, 0.35, 0.03);
      this.click(t + 0.05, 1500, 0.4, 0.04);
      this.thump(t + 0.05, 170, 70, 0.35, 0.09);
    },
    // 끝까지 당겨졌을 때: 묵직한 "철커덕"
    clunk() {
      if (!this.ctx) return;
      const t = this.ctx.currentTime;
      this.click(t, 1900, 0.5, 0.04);
      this.thump(t, 140, 48, 0.6, 0.16);
      this.click(t + 0.07, 1100, 0.45, 0.05);
      this.thump(t + 0.07, 110, 45, 0.35, 0.12);
    },
    ratchet() {
      if (!this.ctx) return;
      this.click(this.ctx.currentTime, 3200, 0.12, 0.015);
    },
    tick() {
      if (!this.ctx) return;
      const now = performance.now();
      if (now - this.lastTick < 28) return;
      this.lastTick = now;
      this.click(this.ctx.currentTime, 2400, 0.16, 0.02);
    },
    tada() {
      if (!this.ctx) return;
      const c = this.ctx, t = c.currentTime;
      [[784, 0], [1047, 0.09], [1319, 0.18]].forEach(([f, d]) => {
        const o = c.createOscillator(), g = c.createGain();
        o.type = "triangle"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t + d);
        g.gain.exponentialRampToValueAtTime(0.18, t + d + 0.02);
        g.gain.exponentialRampToValueAtTime(0.001, t + d + 0.5);
        o.connect(g).connect(c.destination);
        o.start(t + d); o.stop(t + d + 0.55);
      });
    },
  };
  const buzz = (p) => { try { navigator.vibrate?.(p); } catch {} };

  /* ---------- 화면 전환 ---------- */
  const screens = { splash: $("#splash"), onboarding: $("#onboarding"), machine: $("#machine") };
  let current = "splash";
  function show(name) {
    screens[current].classList.remove("is-active");
    screens[name].classList.add("is-active");
    current = name;
  }

  /* ---------- 상품 목록 (미리 디코딩해 둔 대기열) ---------- */
  const MENU = (window.MENU || []).map(([id, category, name]) => ({ id, category, name, src: `img/${id}.webp` }));
  let bag = [], bagPos = 0, lastPicked = null;
  function draw() {
    if (bagPos >= bag.length) {
      bag = MENU.map((_, i) => i);
      for (let i = bag.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [bag[i], bag[j]] = [bag[j], bag[i]];
      }
      bagPos = 0;
    }
    const p = MENU[bag[bagPos++]];
    if (p === lastPicked && MENU.length > 1) return draw();
    lastPicked = p;
    return p;
  }
  const ready = [];
  let filling = false;
  async function fill() {
    if (filling) return;
    filling = true;
    while (ready.length < 48) {
      const p = draw();
      const im = new Image();
      im.decoding = "async";
      im.src = p.src;
      try { await im.decode(); } catch {}
      ready.push(p);
    }
    filling = false;
  }
  function take() {
    const p = ready.shift() || draw();
    fill();
    return p;
  }

  /* ---------- 가로 룰렛 머신 ---------- */
  // Figma Roulette 'Group 5'의 다섯 칸(중심 좌표·크기·기울기·투명도). 양 끝은 화면 밖 대기 칸.
  // 칸 요소는 가장 큰 결과 크기(340px)로 그리고 줄여 쓴다. 확대하면 흐려지기 때문.
  const BASE = 340;
  const SLOTS = [
    { x: -95, y: 474, s: 120 / BASE, r: -20, o: 0 },                 // -3 (화면 밖)
    { x: 50.06, y: 471.81, s: 147.203 / BASE, r: -17.18, o: 0.8 },  // -2  image 12
    { x: 118.64, y: 472.39, s: 200.968 / BASE, r: -15.26, o: 0.9 }, // -1  image 11
    { x: 193.75, y: 471.5, s: 241 / BASE, r: 0, o: 1 },             //  0  image 5 (241×241)
    { x: 276.35, y: 476.1, s: 200.968 / BASE, r: 9.78, o: 1 },      // +1  image 10
    { x: 346.42, y: 476.42, s: 147.203 / BASE, r: 11.13, o: 0.8 },  // +2  image 12
    { x: 485, y: 478, s: 120 / BASE, r: 14, o: 0 },                 // +3 (화면 밖)
  ];
  const Z = [0, 2, 4, 5, 3, 1, 0];
  const RESULT = { x: 195, y: 492, s: 340 / BASE, r: 0, o: 1 }; // Figma Result 'image 5' 340×340 at (25, 322)

  const reelEl = $("#reel");
  const reel = {
    items: [],
    p: 0,          // 전체가 오른쪽으로 이동한 칸 수
    focus: 0,      // 0: 룰렛, 1: 결과(당첨 상품만 크게)
    winner: null,
    stopIdle: null,
    idleTimer: 0,
  };

  function slotAt(pos) {
    const c = clamp(pos, -3, 3) + 3;
    const i = Math.min(5, Math.floor(c));
    const f = c - i;
    const a = SLOTS[i], b = SLOTS[i + 1];
    return { x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f), s: lerp(a.s, b.s, f), r: lerp(a.r, b.r, f), o: lerp(a.o, b.o, f) };
  }

  function buildReel() {
    for (let k = -3; k <= 3; k++) {
      const el = document.createElement("div");
      el.className = "reel-item";
      const img = document.createElement("img");
      img.alt = "";
      img.decoding = "async";
      el.append(img);
      reelEl.append(el);
      const item = { el, img, k, cycle: 0, product: null };
      setProduct(item, take());
      reel.items.push(item);
    }
    renderReel();
  }

  function setProduct(item, product) {
    item.product = product;
    item.img.src = product.src;
  }

  function renderReel() {
    const f = reel.focus;
    const ef = ease.outBack(clamp(f, 0, 1));
    for (const it of reel.items) {
      const x = it.k + reel.p;
      const cycle = Math.floor((x + 3.5) / 7);
      if (cycle !== it.cycle) {
        it.cycle = cycle;
        setProduct(it, take()); // 왼쪽 화면 밖에서 새 상품으로 다시 들어온다
      }
      const pos = x - cycle * 7;
      let g = slotAt(pos);
      let z = Z[Math.round(clamp(pos, -3, 3)) + 3];
      if (f > 0) {
        if (it === reel.winner) {
          g = {
            x: lerp(g.x, RESULT.x, ef), y: lerp(g.y, RESULT.y, ef), s: lerp(g.s, RESULT.s, ef),
            r: lerp(g.r, RESULT.r, ef), o: lerp(g.o, RESULT.o, clamp(f, 0, 1)),
          };
          z = 6;
        } else {
          g = { ...g, x: g.x + (pos < 0 ? -1 : 1) * 70 * f, o: g.o * (1 - clamp(f * 1.4, 0, 1)) };
        }
      }
      it.el.style.transform = `translate3d(${(g.x - BASE / 2).toFixed(2)}px, ${(g.y - BASE / 2).toFixed(2)}px, 0) rotate(${g.r.toFixed(2)}deg) scale(${g.s.toFixed(4)})`;
      it.el.style.opacity = g.o.toFixed(3);
      it.el.style.zIndex = z;
    }
  }

  function startIdle() {
    stopIdle();
    const loop = () => {
      reel.idleTimer = setTimeout(() => {
        const from = reel.p;
        reel.stopIdle = tween({
          duration: reduceMotion.matches ? 0 : IDLE_MOVE,
          ease: ease.inOutCubic,
          onUpdate: (e) => { reel.p = from + e; renderReel(); },
          onDone: () => { reel.p = Math.round(reel.p); loop(); },
        });
      }, IDLE_EVERY - IDLE_MOVE);
    };
    loop();
  }
  function stopIdle() {
    clearTimeout(reel.idleTimer);
    reel.stopIdle?.();
    reel.stopIdle = null;
  }

  function spinReel() {
    return new Promise((resolve) => {
      const from = reel.p;
      const to = Math.ceil(from) + 28 + Math.floor(Math.random() * 7); // 4초 동안 28~34칸
      let lastCell = Math.floor(from + 0.5);
      tween({
        duration: SPIN_MS,
        ease: spinEase,
        onUpdate: (e) => {
          reel.p = from + (to - from) * e;
          const cell = Math.floor(reel.p + 0.5);
          if (cell !== lastCell) { lastCell = cell; sfx.tick(); }
          renderReel();
        },
        onDone: () => {
          reel.p = to;
          renderReel();
          resolve(reel.items.find((it) => Math.abs(((it.k + to + 3.5) % 7 + 7) % 7 - 3.5) < 0.01));
        },
      });
    });
  }

  function animateFocus(to, duration) {
    return new Promise((resolve) => {
      const from = reel.focus;
      tween({
        duration: reduceMotion.matches ? 0 : duration,
        onUpdate: (e) => { reel.focus = lerp(from, to, e); renderReel(); },
        onDone: () => { reel.focus = to; renderReel(); resolve(); },
      });
    });
  }

  /* ---------- 문구 교체 ---------- */
  const copyEl = $("#machineCopy"), titleEl = $("#mTitle"), subEl = $("#mSub");
  async function setCopy(title, sub) {
    if (titleEl.textContent === title && subEl.textContent === sub) return;
    copyEl.classList.remove("is-in");
    copyEl.classList.add("is-out");
    await wait(150);
    titleEl.textContent = title;
    subEl.textContent = sub;
    copyEl.classList.remove("is-out");
    void copyEl.offsetWidth;
    copyEl.classList.add("is-in");
  }

  /* ---------- 머신 상태 ---------- */
  const machineEl = $("#machine");
  const confettiEl = $("#confetti");
  const announceEl = $("#announce");
  let busy = false;

  function setState(s) { machineEl.dataset.state = s; }

  function confettiOn() {
    confettiEl.classList.remove("is-off", "is-on");
    void confettiEl.offsetWidth;
    confettiEl.classList.add("is-on");
  }
  function confettiOff() {
    if (!confettiEl.classList.contains("is-on")) return;
    confettiEl.classList.add("is-off"); // 흩날리던 자리 그대로 흐려진 뒤 초기화
    setTimeout(() => confettiEl.classList.remove("is-on", "is-off"), 320);
  }

  async function spin() {
    if (busy) return;
    busy = true;
    const fromResult = machineEl.dataset.state === "result";
    setState("spinning");
    stopIdle();
    setCopy("돌리는중!", "어떤게 나올까?");
    announceEl.textContent = "룰렛이 돌아가는 중입니다.";
    if (fromResult) {
      confettiOff();
      await animateFocus(0, 380);
      reel.winner = null;
    }
    const winner = await spinReel();
    await wait(260);

    reel.winner = winner;
    const { category, name } = winner.product;
    setState("result");
    setCopy(category, name);
    confettiOn();
    sfx.tada();
    buzz([18, 60, 30]);
    announceEl.textContent = `결과: ${category}, ${name}`;
    await animateFocus(1, 720);
    busy = false;
  }

  function enterMachine() {
    show("machine");
    setState("idle");
    if (!reel.items.length) buildReel();
    startIdle();
  }

  /* ---------- 손잡이: 누르면 철컥, 머리를 좌→우로 밀면 당겨졌다가 튕기듯 복귀 ---------- */
  const REST = -26.52, PULL = 26.52, SKEW = 2.97;
  const leverEl = $("#lever"), handle = $("#leverHandle"), hit = $("#leverHit");
  const lever = { angle: REST, vel: 0, raf: 0, drag: null, fired: false, autoStop: null };

  function setAngle(a) {
    lever.angle = a;
    const t = (a - REST) / (PULL - REST);
    handle.style.setProperty("--a", `${a.toFixed(2)}deg`);
    handle.style.setProperty("--k", `${lerp(-SKEW, SKEW, t).toFixed(2)}deg`);
  }

  function springBack() {
    cancelAnimationFrame(lever.raf);
    let last = performance.now();
    const K = 420, C = 15;
    const step = (now) => {
      const dt = Math.min(0.032, (now - last) / 1000);
      last = now;
      const acc = -K * (lever.angle - REST) - C * lever.vel;
      lever.vel += acc * dt;
      setAngle(lever.angle + lever.vel * dt);
      if (Math.abs(lever.angle - REST) < 0.05 && Math.abs(lever.vel) < 0.5) {
        lever.vel = 0;
        setAngle(REST);
        return;
      }
      lever.raf = requestAnimationFrame(step);
    };
    lever.raf = requestAnimationFrame(step);
  }

  function fire() {
    if (lever.fired) return;
    lever.fired = true;
    sfx.clunk();
    buzz(28);
    spin();
  }

  function autoPull() {
    lever.fired = false;
    const from = lever.angle;
    lever.autoStop = tween({
      duration: reduceMotion.matches ? 0 : 170,
      ease: ease.inQuad,
      onUpdate: (e) => setAngle(lerp(from, PULL, e)),
      onDone: () => { fire(); lever.vel = -60; springBack(); },
    });
  }

  function leverReady() {
    return current === "machine" && !busy && machineEl.dataset.state !== "spinning";
  }

  hit.addEventListener("pointerdown", (e) => {
    if (!leverReady()) return;
    e.preventDefault();
    sfx.init(); sfx.resume();
    hit.setPointerCapture(e.pointerId);
    cancelAnimationFrame(lever.raf);
    lever.autoStop?.();
    lever.fired = false;
    lever.drag = { id: e.pointerId, x: e.clientX, t: performance.now(), moved: 0, lastNotch: 0 };
    leverEl.classList.add("is-pressed");
    handle.style.setProperty("--press", ".96");
    setAngle(REST + 4); // 누르는 순간 살짝 먹히는 느낌
    sfx.press();
    buzz(10);
  });

  hit.addEventListener("pointermove", (e) => {
    const d = lever.drag;
    if (!d || e.pointerId !== d.id) return;
    const dx = (e.clientX - d.x) / scale;
    d.moved = Math.max(d.moved, Math.abs(dx));
    const progress = clamp(dx / 66, 0, 1);
    setAngle(Math.max(REST + 4 * (1 - progress), lerp(REST, PULL, progress)));
    const notch = Math.floor(progress * 5);
    if (notch > d.lastNotch) { d.lastNotch = notch; sfx.ratchet(); }
    if (progress >= 1) fire();
  });

  function release(e) {
    const d = lever.drag;
    if (!d || e.pointerId !== d.id) return;
    lever.drag = null;
    leverEl.classList.remove("is-pressed");
    handle.style.setProperty("--press", "1");
    const progress = (lever.angle - REST) / (PULL - REST);
    const isTap = d.moved < 8 && performance.now() - d.t < 400;
    if (lever.fired) {
      springBack();
    } else if (isTap || progress >= 0.5) {
      autoPull();
    } else {
      springBack();
    }
  }
  hit.addEventListener("pointerup", release);
  hit.addEventListener("pointercancel", release);
  hit.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && leverReady()) {
      e.preventDefault();
      sfx.init(); sfx.resume();
      sfx.press();
      autoPull();
    }
  });

  /* ---------- 온보딩 ---------- */
  const steps = $$(".ob-step");
  const dots = $$(".ob-progress img");
  let step = 0;
  let typeTimers = [];

  function typeOut(p, delay) {
    const text = p.dataset.say;
    p.textContent = "";
    p.setAttribute("aria-label", text);
    [...text].forEach((ch, i) => {
      const s = document.createElement("span");
      s.textContent = ch;
      s.setAttribute("aria-hidden", "true");
      s.style.animation = reduceMotion.matches
        ? "none"
        : `char-in .32s cubic-bezier(.34,1.56,.64,1) ${delay + i * 70}ms both`;
      if (reduceMotion.matches) s.style.opacity = "1";
      p.append(s);
    });
  }

  function goStep(n) {
    if (n < 1 || n > steps.length) return;
    const prev = steps[step - 1];
    if (prev) {
      prev.classList.remove("is-active", "is-entering");
      prev.classList.add("is-leaving");
      setTimeout(() => prev.classList.remove("is-leaving"), 300);
    }
    step = n;
    const el = steps[n - 1];
    el.classList.remove("is-leaving", "is-entering");
    void el.offsetWidth;
    el.classList.add("is-active", "is-entering");
    typeOut($(".ob-bubble p", el), 1180); // 기니알바가 나타나고 말풍선이 뜬 뒤에 말한다
    dots.forEach((d) => d.classList.toggle("is-on", Number(d.dataset.step) === n));
  }

  /* ---------- 스플래시·온보딩 탭 (무대 밖 여백을 눌러도 동작) ---------- */
  // 온보딩: 탭하면 다음, 오른쪽으로 밀면 이전 단계
  const frame = $(".frame");
  let swipe = null;
  frame.addEventListener("pointerdown", (e) => { swipe = { x: e.clientX, y: e.clientY }; });
  frame.addEventListener("pointerup", (e) => {
    if (!swipe) return;
    const dx = e.clientX - swipe.x;
    swipe = null;
    if (current === "splash") {
      sfx.init(); sfx.resume();
      screens.splash.classList.add("is-leaving");
      show("onboarding");
      goStep(1);
    } else if (current === "onboarding") {
      if (dx > 50) return goStep(step - 1);
      if (step < steps.length) goStep(step + 1);
      else enterMachine();
    }
  });

  /* ---------- 시작 ---------- */
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  fill();
  const fontReady = document.fonts ? document.fonts.load('70px "LOTTERIACHAB"') : Promise.resolve();
  Promise.race([fontReady, wait(3000)]).then(() => document.documentElement.classList.remove("is-loading"));
  document.addEventListener("visibilitychange", () => {
    if (current !== "machine" || busy) return;
    if (document.hidden) stopIdle(); else startIdle();
  });
})();
