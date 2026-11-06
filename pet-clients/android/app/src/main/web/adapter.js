/* Translate the existing desktop bridge into Android's narrow native API. */
(() => {
  const native = window.__androidChannel.invoke;
  const id = () => crypto.randomUUID().replaceAll('-', '');
  const request = (method, path, payload = null) => native('request', {method, path, payload});
  const event = (type, args = {}) => native('event', {type, ...args});
  const todo = (action, args = {}) => request('POST', '/todos', {action, ...args, ...(action === 'list' ? {} : {request_id: id()})});
  window.__TAURI_INTERNALS__.invoke = async (command, payload) => {
    if (command !== 'pet_call') return native(command, payload);
    const {method, args: a} = payload;
    switch (method) {
      case 'send': {
        if (!/^[a-f0-9]{32}$/.test(a[0])) throw new Error('无效请求 ID');
        const result = await request('POST', '/chat/' + a[0], {text: a[1], images: a[2]});
        window.pet.receive({type:'accepted', request_id:a[0]}); return result;
      }
      case 'todos': return (await todo('list', {include_completed:!!a[0]})).items.filter(item => !a[0] || item.status === 'completed');
      case 'todo_image': {
        if (!/^[a-f0-9]{64}$/.test(a[0])) throw new Error('无效图片 ID');
        return (await request('GET', '/todos/images/' + a[0])).data_url;
      }
      case 'add_todo': return (await todo('add', {text:a[0], options:a[1]})).item;
      case 'update_todo': return (await todo('update', {todo_id:a[0], text:a[1], options:a[2]})).item;
      case 'done_todo': return todo('complete', {todo_id:a[0]});
      case 'clicked': return event('clicked');
      case 'refresh_buttons': return event('buttons_refresh');
      case 'click_button': return event('button_click', {plugin_id:a[0], button_id:a[1], action_id:a[2]});
      case 'exploration_tasks': return event('exploration_tasks', {task_id:a[0], request_id:a[1]});
      case 'explore': return event('explore', {goal:a[0]});
      case 'screen_action': throw new Error('Android 版暂不支持自动读屏');
      default: return native('pet_call', payload);
    }
  };
  // Focus belongs to the floating window only while editing; Back/outside releases it again.
  document.addEventListener('focusin', event => {
    if (event.target.matches('input,textarea,[contenteditable=true]')) native('focus', {editing:true}).catch(console.error);
  });
})();
