/* 声明式皮肤播放器。资源在切换前全部解码，播放状态由窗口确认。 */
(() => {
  const stage = document.getElementById('stage');
  const pet = document.getElementById('pet');
  const builtin = document.getElementById('builtin-pet');
  const canvas = document.getElementById('skin-canvas');
  const ctx = canvas.getContext('2d');
  let skin = null, images = {}, resident = new Set(), active = null, visible = true, switching = 0;
  let selected = '', started = performance.now(), flash = null;
  const history = new Map();
  let builtinEffectNodes = [];
  const fail = (code, message) => ({error: {code, message}});
  const state = () => ['dragging', 'talking', 'thinking'].find(name => stage.classList.contains(name)) || 'idle';
  const filesOf = (target, names) => new Set(names.flatMap(name => target.animations[name]?.sequence?.map(frame => frame.file) || []));
  async function decode(target, files) {
    const loaded = {};
    await Promise.all([...files].map(async file => {
      const image = new Image(); image.src = target.resources[file]; await image.decode(); loaded[file] = image;
    }));
    return loaded;
  }
  // 按需动画解码为 ImageBitmap，播完 close() 立即归还位图内存；Image 的解码缓存要等 GC 才释放。
  async function decodeBitmaps(target, files) {
    const loaded = {};
    try {
      await Promise.all([...files].map(async file => {
        const blob = await (await fetch(target.resources[file])).blob();
        loaded[file] = await createImageBitmap(blob);
      }));
    } catch (error) {
      for (const bitmap of Object.values(loaded)) bitmap.close();
      throw error;
    }
    return loaded;
  }
  function release(files) {
    for (const file of files) { images[file]?.close?.(); delete images[file]; }
  }
  function finish(status, reason) {
    if (!active) return;
    if (skin?.id === 'builtin') builtin.classList.remove(active.animation);
    for (const node of builtinEffectNodes) node.style.animationIterationCount = '';
    builtinEffectNodes = [];
    history.set(active.playback_id, {...active, status, reason});
    while (history.size > 128) history.delete(history.keys().next().value);
    active = null;
    selected = '';
    // 按需解码的精灵图播完即释放，常驻内存只保留状态与事件动画。
    release(Object.keys(images).filter(file => !resident.has(file)));
  }
  function info() { return {id: skin?.id || 'builtin', name: skin?.name || '麦麦（内置）', version: skin?.version || '1'}; }
  async function install(next) {
    const revision = ++switching;
    // 仅预解码状态与事件动画；插件动画在播放时解码，避免多动画皮肤的解码位图全部常驻内存。
    const required = next.id === 'builtin' ? new Set()
      : filesOf(next, [...Object.values(next.states || {}), ...Object.values(next.events || {})]);
    const loaded = await decode(next, required);
    if (revision !== switching) throw new Error('皮肤切换已被新请求替代');
    finish('interrupted', 'skin_changed');
    skin = next; images = loaded; resident = required; flash = null; selected = '';
    const c = skin.canvas;
    // 落脚点固定在窗口底边中点，气泡随皮肤锚点调整，输入区仍由主页面管理。
    pet.style.width = c.width + 'px'; pet.style.height = c.height + 'px';
    pet.style.left = (130 - c.anchor[0]) + 'px';
    pet.style.bottom = (4 - (c.height - c.anchor[1])) + 'px';
    pet.dataset.skinHitbox = JSON.stringify(c.hitbox);
    stage.style.setProperty('--pet-top', (4 + c.anchor[1] - c.bubble_anchor[1] + 8) + 'px');
    canvas.width = c.width * 2; canvas.height = c.height * 2;
    canvas.style.width = c.width + 'px'; canvas.style.height = c.height + 'px';
    builtin.style.display = skin.id === 'builtin' ? '' : 'none';
    canvas.style.display = skin.id === 'builtin' ? 'none' : 'block';
    window.dispatchEvent(new Event('pet-skin-changed'));
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
    const missing = skin.id === 'builtin' ? [] : [...filesOf(skin, [name])].filter(file => !images[file]);
    if (missing.length) {
      // 解码完成前继续显示当前状态动画，计时从真正开始播放时算起。
      const entry = active, revision = switching;
      entry.loading = true;
      decodeBitmaps(skin, missing).then(loaded => {
        if (active !== entry || revision !== switching) { for (const bitmap of Object.values(loaded)) bitmap.close(); return; }
        Object.assign(images, loaded);
        entry.loading = false;
        entry.started = performance.now();
      }, () => { if (active === entry) finish('interrupted', 'decode_failed'); });
    }
    if (skin.id === 'builtin') {
      builtin.classList.remove(name); void builtin.offsetWidth; builtin.classList.add(name);
      const selector = {squish: '#squash', happy: '#squash', shake: '#squash', blink: '.eye-open'}[name];
      builtinEffectNodes = selector ? [...builtin.querySelectorAll(selector)] : [];
      for (const node of builtinEffectNodes) node.style.animationIterationCount = String(repeat);
    }
    return {...active};
  }
  function tick(now) {
    requestAnimationFrame(tick);
    if (!skin) return;
    const current = state();
    if (active && ['talking', 'dragging'].includes(current)) finish('interrupted', current);
    if (active && !active.loading && now - active.started >= active.duration_ms) finish('finished', 'completed');
    if (flash && now >= flash.until) flash = null;
    if (skin.id === 'builtin' || !visible) return;
    const playing = active?.loading ? null : active;
    const name = playing?.animation || (['talking', 'dragging'].includes(current) ? skin.states[current] : flash?.name || skin.states[current]);
    if (selected !== name) { selected = name; started = now; }
    const animation = skin.animations[name];
    const elapsed = now - (playing?.started ?? started);
    let progress = elapsed % animation.duration_ms;
    let frame = animation.sequence[0];
    for (const candidate of animation.sequence) { frame = candidate; if (progress < frame.duration_ms) break; progress -= frame.duration_ms; }
    const [x,y,w,h] = frame.rect, c = skin.canvas;
    ctx.clearRect(0,0,canvas.width,canvas.height); ctx.save(); ctx.scale(2,2);
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
  }
  requestAnimationFrame(tick);
  window.petSkin = {
    install,
    cancelInstall() { switching++; },
    event(event) {
      if (!skin || skin.id === 'builtin' || active) return;
      const name = skin.events[event];
      if (name) flash = {name, until: performance.now() + skin.animations[name].duration_ms};
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
