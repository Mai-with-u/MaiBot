/* 声明式皮肤播放器；远端皮肤首帧就绪后切换，其余帧按需加载。 */
(() => {
  const stage = document.getElementById('stage');
  const pet = document.getElementById('pet');
  const builtin = document.getElementById('builtin-pet');
  const loading = document.getElementById('skin-loading');
  const canvas = document.getElementById('skin-canvas');
  const ctx = canvas.getContext('2d');
  const previousCanvas = document.createElement('canvas');
  const previousCtx = previousCanvas.getContext('2d');
  const hitCanvas = document.createElement('canvas');
  const hitCtx = hitCanvas.getContext('2d', {willReadFrequently: true});
  let hitRects = [];
  const transitionDuration = 160;
  let skin = null, images = {}, active = null, visible = true, switching = 0;
  let loadImage, pixelRatio = 1;
  let loadingTimer;
  let loadingDeadline;
  let selected = '', started = performance.now(), flash = null;
  let transitionStarted = null, hasFrame = false;
  const history = new Map();
  let builtinEffectNodes = [];
  const fail = (code, message) => ({error: {code, message}});
  const state = () => ['dragging', 'talking', 'thinking'].find(name => stage.classList.contains(name)) || 'idle';
  function finish(status, reason) {
    if (!active) return;
    if (skin?.id === 'builtin') builtin.classList.remove(active.animation);
    for (const node of builtinEffectNodes) node.style.animationIterationCount = '';
    builtinEffectNodes = [];
    history.set(active.playback_id, {...active, status, reason});
    while (history.size > 128) history.delete(history.keys().next().value);
    active = null;
    selected = '';
  }
  function info() { return {id: skin?.id || 'builtin', name: skin?.name || '麦麦（内置）', version: skin?.version || '1'}; }
  function resizeCanvas() {
    const {width, height} = skin.canvas;
    pixelRatio = Math.min(4, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    previousCanvas.width = canvas.width;
    previousCanvas.height = canvas.height;
    transitionStarted = null;
    hasFrame = false;
  }
  function setLoading(pending) {
    clearTimeout(loadingTimer);
    clearTimeout(loadingDeadline);
    if (pending) {
      loadingDeadline = setTimeout(() => setLoading(false), 30_000);
      if (loading.hidden) loadingTimer = setTimeout(() => {
        loading.hidden = false;
        window.dispatchEvent(new Event('pet-skin-loading'));
      }, 120);
    } else if (!loading.hidden) {
      loading.hidden = true;
      window.dispatchEvent(new Event('pet-skin-loading'));
    }
  }
  async function install(next, fetchAsset, prefetchAsset) {
    const revision = ++switching;
    const firstAnimation = next.states?.[state()];
    const loaded = Object.create(null), pending = new Map(), retries = new Map();
    const requested = new Set();
    let prefetching;
    const load = name => {
      const retry = retries.get(name);
      if (retry && performance.now() < retry.nextAttemptAt) return Promise.resolve();
      if (!pending.has(name)) pending.set(name, (async () => {
        const src = next.resources[name];
        requested.add(src);
        const image = new Image();
        if (prefetching?.digest === src) await prefetching.task;
        image.src = fetchAsset ? await fetchAsset(src) : src;
        await image.decode();
        loaded[name] = image;
        retries.delete(name);
      })().catch(error => {
        const attempts = (retries.get(name)?.attempts || 0) + 1;
        const delay = Math.min(30_000, 500 * 2 ** (attempts - 1));
        retries.set(name, {attempts, nextAttemptAt: performance.now() + delay});
        pending.delete(name);
        throw error;
      }));
      return pending.get(name);
    };
    setLoading(next.id !== 'builtin');
    try {
      if (fetchAsset && next.id !== 'builtin') {
        await load(next.animations[firstAnimation].sequence[0].file);
      } else {
        await Promise.all(Object.keys(next.resources || {}).map(load));
      }
    } finally {
      if (revision === switching) setLoading(false);
    }
    if (revision !== switching) throw new Error('皮肤切换已被新请求替代');
    finish('interrupted', 'skin_changed');
    skin = next; images = loaded; loadImage = fetchAsset ? load : null; flash = null; selected = '';
    transitionStarted = null; hasFrame = false;
    const c = skin.canvas;
    // 底部预留阶段字幕，落脚点固定在窗口底边中点，气泡随皮肤锚点调整，输入区仍由主页面管理。
    pet.style.width = c.width + 'px'; pet.style.height = c.height + 'px';
    pet.style.left = (130 - c.anchor[0]) + 'px';
    pet.style.bottom = (24 - (c.height - c.anchor[1])) + 'px';
    pet.dataset.skinHitbox = JSON.stringify(c.hitbox);
    stage.style.setProperty('--pet-top', (24 + c.anchor[1] - c.bubble_anchor[1] + 8) + 'px');
    resizeCanvas();
    hitCanvas.width = c.width; hitCanvas.height = c.height;
    hitRects = [];
    canvas.style.width = c.width + 'px'; canvas.style.height = c.height + 'px';
    builtin.style.display = skin.id === 'builtin' ? '' : 'none';
    canvas.style.display = skin.id === 'builtin' ? 'none' : 'block';
    render(performance.now(), firstAnimation);
    window.dispatchEvent(new Event('pet-skin-changed'));
    if (prefetchAsset && next.id !== 'builtin') {
      void (async () => {
        // Download one at a time without decoding every sprite sheet into memory.
        for (const digest of new Set(Object.values(next.resources))) {
          if (revision !== switching) return;
          if (requested.has(digest)) continue;
          const task = prefetchAsset(digest).catch(error => console.warn('皮肤后台预加载失败', error));
          prefetching = {digest, task};
          await task;
          prefetching = null;
        }
      })();
    }
    return info();
  }
  function play(owner, name, repeat = 1) {
    if (!visible) return fail('hidden', '桌宠已隐藏');
    if (!skin || !Object.hasOwn(skin.animations, name)) return fail('animation_not_found', '当前皮肤没有此动画');
    if (['talking', 'dragging'].includes(state()) || (active && active.owner !== owner)) return fail('busy', '桌宠正在执行其他动作');
    const duration = skin.animations[name].duration_ms * repeat;
    if (!Number.isInteger(repeat) || repeat < 1 || duration > 30000) return fail('invalid_duration', '动画总时长应不超过 30 秒');
    finish('interrupted', 'replaced');
    active = {playback_id: crypto.randomUUID(), owner, skin_id: skin.id, animation: name, status: 'playing',
              duration_ms: duration, started: performance.now()};
    flash = null; selected = '';
    if (skin.id === 'builtin') {
      builtin.classList.remove(name); void builtin.offsetWidth; builtin.classList.add(name);
      const selector = {squish: '#squash', happy: '#squash', shake: '#squash', blink: '.eye-open'}[name];
      builtinEffectNodes = selector ? [...builtin.querySelectorAll(selector)] : [];
      for (const node of builtinEffectNodes) node.style.animationIterationCount = String(repeat);
    }
    return {...active};
  }
  function render(now, firstAnimation) {
    if (!skin) return;
    const current = state();
    if (active && ['talking', 'dragging'].includes(current)) finish('interrupted', current);
    if (active && now - active.started >= active.duration_ms) finish('finished', 'completed');
    if (flash && now >= flash.until) flash = null;
    if (skin.id === 'builtin' || !visible) return;
    const event = ['talking', 'dragging'].includes(current) ? null : flash;
    const name = firstAnimation || active?.animation || event?.name || skin.states[current];
    const selection = active || event || name;
    if (selected !== selection) {
      // 保存实际显示的画面，连续点击或中途切换也从当前姿势开始过渡。
      if (hasFrame) {
        previousCtx.clearRect(0, 0, previousCanvas.width, previousCanvas.height);
        previousCtx.drawImage(canvas, 0, 0);
        transitionStarted = now;
      }
      selected = selection; started = now;
    }
    const animation = skin.animations[name];
    const elapsed = now - (active?.started ?? event?.started ?? started);
    let progress = elapsed % animation.duration_ms;
    const frames = animation.sequence;
    let index = 0;
    for (; index < frames.length - 1 && progress >= frames[index].duration_ms; index++) {
      progress -= frames[index].duration_ms;
    }
    const frame = frames[index];
    if (!images[frame.file]) {
      // Leave the last complete frame visible while a new animation asset is in flight.
      loadImage?.(frame.file).catch(() => {});
      return;
    }
    const nextFrame = frames[(index + 1) % frames.length];
    if (!firstAnimation && !images[nextFrame.file]) loadImage?.(nextFrame.file).catch(() => {});
    if (pixelRatio !== Math.min(4, window.devicePixelRatio || 1)) resizeCanvas();
    const [x,y,w,h] = frame.rect, c = skin.canvas;
    ctx.clearRect(0,0,canvas.width,canvas.height); ctx.save();
    if (transitionStarted !== null && now - transitionStarted >= transitionDuration) transitionStarted = null;
    if (transitionStarted !== null) {
      const progress = (now - transitionStarted) / transitionDuration;
      const blend = progress * progress * (3 - 2 * progress);
      ctx.globalAlpha = 1 - blend;
      ctx.drawImage(previousCanvas, 0, 0);
      // 透明帧按预乘颜色相加，避免两帧重叠处在过渡中变暗或变透明。
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = blend;
    }
    ctx.scale(canvas.width / c.width, canvas.height / c.height);
    const phase = elapsed / Math.max(600, animation.duration_ms) * Math.PI * 2;
    ctx.translate(c.width / 2, c.height - 4);
    if (animation.motion === 'bob') ctx.translate(0, -2 - 2 * Math.sin(phase));
    if (animation.motion === 'hop') ctx.translate(0, -Math.abs(Math.sin(phase)) * 8);
    if (animation.motion === 'sway') ctx.rotate(Math.sin(phase) * .035);
    if (animation.motion === 'shake') ctx.translate(Math.sin(elapsed / 40) * 3, 0);
    if (animation.motion === 'pulse') ctx.scale(1 + Math.sin(phase) * .025, 1 - Math.sin(phase) * .025);
    ctx.translate(-c.width / 2, -c.height + 4);
    const scale = Math.min((c.width - 8) / w, (c.height - 12) / h);
    ctx.drawImage(images[frame.file], x,y,w,h, (c.width-w*scale)/2,c.height-4-h*scale,w*scale,h*scale);
    ctx.restore();
    hasFrame = true;
    // 用当前合成帧的 alpha 生成逐像素区域，包含动作位移和过渡，透明空隙交还桌面。
    hitCtx.clearRect(0, 0, c.width, c.height);
    hitCtx.drawImage(canvas, 0, 0, c.width, c.height);
    hitRects = window.petAlphaRegion(hitCtx.getImageData(0, 0, c.width, c.height).data, c.width, c.height);
    window.dispatchEvent(new Event('pet-skin-frame'));
  }
  function tick(now) {
    requestAnimationFrame(tick);
    render(now);
  }
  requestAnimationFrame(tick);
  window.petSkin = {
    install,
    setLoading,
    hitRegion() { return skin && skin.id !== 'builtin' ? hitRects : null; },
    cancelInstall() { switching++; setLoading(false); },
    event(event) {
      if (!skin || skin.id === 'builtin' || active) return;
      const name = skin.events[event];
      if (name) {
        const started = performance.now();
        flash = {name, started, until: started + skin.animations[name].duration_ms};
      }
    },
    visibility(value) { visible = value; if (!visible) finish('interrupted', 'hidden'); },
    command(action, args, owner) {
      if (action === 'get_skin') return info();
      if (action === 'list_animations') return {skin: info(), animations: Object.entries(skin?.animations || {}).map(([name, a]) => ({name, duration_ms: a.duration_ms})), events: skin?.events || {}};
      if (action === 'play_animation') return play(owner, args.name, args.repeat);
      if (action === 'play_event') {
        const name = skin?.events[args.event];
        return name ? play(owner, name, 1) : fail('event_not_found', '当前皮肤没有映射此事件');
      }
      if (action === 'stop_animation' || action === 'animation_status') {
        const item = active?.playback_id === args.playback_id ? active : history.get(args.playback_id);
        if (!item) return fail('playback_not_found', '播放记录不存在或已过期');
        if (item.owner !== owner) return fail('not_owner', '不能访问其他插件的播放记录');
        if (action === 'stop_animation' && item === active) { finish('stopped', 'requested'); return history.get(args.playback_id); }
        return {...item};
      }
      return fail('unknown_action', '未知动画操作');
    },
  };
})();
