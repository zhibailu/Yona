        // ========== LLM 调试日志面板 ==========
        // 推送式:后端每次真实 LLM 调用 → SSE 推一条(engine._TracingLLM
        // 记录 + /admin/llm-log/stream 广播)。面板展开才订阅;没有调用
        // 就静默挂着 —— 零请求,不再 2s 盲轮询拿空。
        // 打开面板:先 fetch 一次历史快照,再开 SSE 等增量。
        let _llmLogSource = null;   // EventSource(展开/全屏时非空)

        function _llmLogWanted() {
            const panel = document.getElementById('llm-log-panel');
            const ov = document.getElementById('debug-overlay');
            return (panel && !panel.classList.contains('collapsed'))
                || (ov && ov.style.display === 'flex');
        }

        function _llmLogRow(l) {
            return `<div class="feed-item"><span class="time">${l.t}</span> <b style="color:var(--accent)">${l.d}</b> <span style="color:var(--text-faint)">${escapeHtml(l.m)}</span><pre style="margin:2px 0 0 12px;white-space:pre-wrap;font-size:10px;max-height:120px;overflow-y:auto">${escapeHtml(l.c)}</pre></div>`;
        }

        function _renderLlmLog(list, full) {
            const el = document.getElementById('llm-log-list');
            if (el && list.length) {
                el.innerHTML = list.slice(-30).map(_llmLogRow).join('');
                el.parentElement.scrollTop = el.parentElement.scrollHeight;
            }
            const fel = document.getElementById('debug-full-log');
            if (fel) {
                if (!full.length) { fel.textContent = '(暂无 LLM 调用)'; return; }
                fel.innerHTML = full.map(l => {
                    const dirStyle = l.d === '→' ? 'color:#e8a0b4' : l.d.includes('🔧') ? 'color:#a0e8c0' : 'color:#a0c8e8';
                    return `<div style="margin-bottom:16px;border-bottom:1px solid var(--line);padding-bottom:12px">
                        <div style="margin-bottom:4px"><b style="${dirStyle}">${l.d}</b> <span style="color:var(--text-faint)">${l.t} ${l.m}</span></div>
                        <div style="white-space:pre-wrap;word-break:break-all">${escapeHtml(l.c)}</div>
                    </div>`;
                }).join('');
            }
        }

        let _llmLogCache = [];  // 已收到的全部(SSE 增量 append,渲染时截尾)

        function _appendLlmLog(entry) {
            _llmLogCache.push(entry);
            if (_llmLogCache.length > 300) _llmLogCache = _llmLogCache.slice(-300);
            _renderLlmLog(_llmLogCache, _llmLogCache);
        }

        async function _syncLlmLog() {
            const wanted = _llmLogWanted();
            if (!wanted) {
                if (_llmLogSource) { _llmLogSource.close(); _llmLogSource = null; }
                return;
            }
            // 首次打开:拉历史快照(一次性),再订阅增量
            if (!_llmLogSource) {
                try {
                    const res = await fetch(`${API}/admin/llm-log`);
                    if (res.ok) {
                        const data = await res.json();
                        _llmLogCache = data.log || [];
                        _renderLlmLog(_llmLogCache, _llmLogCache);
                    }
                } catch(e) {}
                try {
                    _llmLogSource = new EventSource(`${API}/admin/llm-log/stream`);
                    _llmLogSource.onmessage = (ev) => {
                        try { _appendLlmLog(JSON.parse(ev.data)); } catch(e) {}
                    };
                    // EventSource 断线自动重连,无需处理
                } catch(e) { _llmLogSource = null; }
            }
        }

        // 面板 toggle / 全屏开关都会改变"想看吗",跟着状态开/关订阅。
        // 用轻量的状态检查驱动(不 fetch,只是开关 EventSource)。
        setInterval(_syncLlmLog, 1000);

        async function openDebugFullscreen() {
            const ov = document.getElementById('debug-overlay');
            ov.style.display = 'flex';
            await _syncLlmLog(); // 拉历史快照 + 订阅增量(幂等)
        }
        function closeDebugFullscreen() {
            document.getElementById('debug-overlay').style.display = 'none';
            _syncLlmLog();
        }

        // ========== 图片管理（服务端持久化，按会话绑定） ==========
        let _imgTarget = 'bg';

        function _imgUrl(target) {
            return `${API}/images/${currentSessionId || 'global'}/${target}`;
        }

        function pickImage(target) {
            _imgTarget = target;
            document.getElementById('image-picker').click();
        }

        function handleImagePick(event) {
            const file = event.target.files[0];
            if (!file) return;
            if (_imgTarget === 'vision') {
                _attachVisionImage(file);
                event.target.value = '';
                return;
            }
            const reader = new FileReader();
            reader.onload = function() {
                const b64 = reader.result;
                if (_imgTarget === 'bg') {
                    _uploadAndApply(_imgTarget, b64);
                    _showBgPositionSelector(b64);
                } else {
                    _showCropModal(b64, _imgTarget, function(cropped) { _uploadAndApply(_imgTarget, cropped); });
                }
            };
            reader.readAsDataURL(file);
            event.target.value = '';
        }

        function _uploadAndApply(target, b64) {
            if (!currentSessionId) return;
            _render(target, b64);
            fetch(_imgUrl(target), {
                method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({data: b64})
            });
        }

        function _render(target, src) {
            if (!src) return;
            if (target === 'bg') {
                const a = document.querySelector('.chat-area');
                a.style.backgroundImage = `url(${src})`;
                a.style.backgroundSize = '100% auto';
                a.style.backgroundRepeat = 'no-repeat';
                a.style.backgroundAttachment = 'scroll';
                // 不覆盖已保存的 background-position-y
                if (!a.style.backgroundPositionY) a.style.backgroundPosition = 'top center';
            } else {
                const sel = target === 'avatar_ai'
                    ? '.msg-row.assistant .msg-avatar, #typing-avatar'
                    : '.msg-row.user .msg-avatar';
                document.querySelectorAll(sel).forEach(el => {
                    el.style.backgroundImage = `url(${src})`;
                    el.style.backgroundSize = 'cover';
                    el.textContent = '';
                });
                // 若为 AI 头像，同步刷新肖像面板
                if (target === 'avatar_ai') {
                    const panel = document.getElementById('portrait-panel');
                    const portraitImg = panel.querySelector('.portrait-img');
                    if (portraitImg && panel.classList.contains('show')) {
                        portraitImg.style.backgroundImage = `url(${src})`;
                        portraitImg.style.backgroundSize = 'cover';
                    }
                }
            }
        }

        function _loadSessionImages() {
            const sid = currentSessionId;
            if (!sid) return;
            for (const t of ['bg', 'avatar_ai', 'avatar_user']) {
                fetch(_imgUrl(t)).then(r => { if (r.ok) _render(t, _imgUrl(t)); }).catch(()=>{});
            }
            // 恢复保存的背景位置
            fetch('/bg-position/' + sid).then(r => r.json()).then(d => {
                _pinnedBgPct = d.position || 0;
                _applyBgPosition(_pinnedBgPct);
            }).catch(() => {});
        }

        // appendMessage 创建头像时用服务端 URL
        async function resetAllImages() {
            const sid = currentSessionId;
            if (!sid) return;
            for (const t of ['bg', 'avatar_ai', 'avatar_user']) {
                try { await fetch(_imgUrl(t, sid), {method: 'DELETE'}); } catch(e) {}
            }
            document.querySelector('.chat-area').style.backgroundImage = '';
            document.querySelectorAll('.msg-avatar').forEach(el => {
                el.style.backgroundImage = ''; el.style.backgroundSize = '';
                el.textContent = el.dataset.defaultText || '';
            });
        }

        // 剪裁弹窗
        function _showBgPositionSelector(src) {
            // 全图预览 + 选位，不裁剪。原图已上传，这里只设 background-position-y
            const old = document.getElementById('crop-overlay');
            if (old) old.remove();
            const overlay = document.createElement('div');
            overlay.id = 'crop-overlay';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.88);display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px';

            // 视口框 = 模拟聊天区比例
            const vw = Math.min(540, window.innerWidth - 40);
            const vh = Math.round(vw * 0.62);
            const wrap = document.createElement('div');
            wrap.style.cssText = `width:${vw}px;height:${vh}px;overflow-y:auto;overflow-x:hidden;border:2px solid #e8a0b4;border-radius:8px;background:#111;position:relative`;
            const img = new Image();
            img.style.cssText = 'width:100%;display:block';
            img.onload = function() { wrap.scrollTop = 0; };
            img.src = src;
            wrap.appendChild(img);

            // 中间标线
            const hint = document.createElement('div');
            hint.style.cssText = 'position:absolute;left:8px;right:8px;top:50%;height:1px;border-top:2px dashed rgba(232,160,180,0.7);pointer-events:none';
            wrap.appendChild(hint);

            overlay.appendChild(wrap);

            const tip = document.createElement('div');
            tip.textContent = '滚动选择初始展示区域（虚线 = 默认可见位置）';
            tip.style.cssText = 'color:#aaa;font-size:12px';
            overlay.appendChild(tip);

            const btnRow = document.createElement('div'); btnRow.style.cssText = 'display:flex;gap:10px';
            const okBtn = document.createElement('button'); okBtn.textContent = '确定';
            okBtn.style.cssText = 'padding:8px 24px;border:none;border-radius:8px;background:#e8a0b4;color:#fff;cursor:pointer;font-size:14px';
            okBtn.onclick = function() {
                const renderedH = img.naturalWidth ? img.naturalHeight * (vw / img.naturalWidth) : 0;
                const maxS = Math.max(1, renderedH - vh);
                const pct = Math.round(wrap.scrollTop / maxS * 100);
                const chat = document.querySelector('.chat-area');
                chat.style.backgroundPositionY = pct + '%';
                _saveBgPosition(pct);
                overlay.remove();
            };
            const cancelBtn = document.createElement('button'); cancelBtn.textContent = '取消（使用顶部）';
            cancelBtn.style.cssText = 'padding:8px 16px;border:1px solid #888;border-radius:8px;background:transparent;color:#ccc;cursor:pointer;font-size:14px';
            cancelBtn.onclick = function() { overlay.remove(); };
            btnRow.appendChild(okBtn); btnRow.appendChild(cancelBtn);
            overlay.appendChild(btnRow);
            document.body.appendChild(overlay);
        }

        function _showCropModal(src, target, callback) {
            const old = document.getElementById('crop-overlay');
            if (old) old.remove();
            const overlay = document.createElement('div');
            overlay.id = 'crop-overlay';
            overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px';

            const isBg = target === 'bg';
            // 背景：宽画布全宽展示；头像：正方形
            const cw = isBg ? Math.min(720, window.innerWidth - 60) : 300;
            const ch = isBg ? Math.round(cw * 0.56) : 300;  // ~16:9 viewport

            const canvas = document.createElement('canvas');
            canvas.width = cw; canvas.height = ch;
            canvas.style.cssText = 'border:1px solid #e8a0b4;border-radius:8px;cursor:grab;max-width:100vw';
            const img = new Image();
            img.onload = function() {
                const ctx = canvas.getContext('2d');
                // 背景：图宽=canvas宽，高度按比例，只能上下滚
                // 头像：保持原逻辑 fit + drag + zoom
                let iw, ih;
                if (isBg) {
                    iw = cw;
                    ih = img.height * (cw / img.width);
                } else {
                    const scale = Math.max(cw / img.width, ch / img.height);
                    iw = img.width * scale; ih = img.height * scale;
                }
                let ox = isBg ? 0 : (cw - iw) / 2;
                let oy = isBg ? 0 : (ch - ih) / 2;
                let dragging = false, sx = 0, sy = 0;

                function draw() {
                    ctx.clearRect(0, 0, cw, ch);
                    // 选区外半透明遮罩
                    ctx.fillStyle = 'rgba(0,0,0,0.5)';
                    ctx.fillRect(0, 0, cw, ch);
                    // 图片只画在选区内
                    ctx.save();
                    ctx.beginPath(); ctx.rect(0, 0, cw, ch); ctx.clip();
                    ctx.drawImage(img, ox, oy, iw, ih);
                    ctx.restore();
                }
                draw();

                function clampPos() {
                    // 图片不超出 canvas 边界（无黑边），背景和头像通用
                    if (ih <= ch) { oy = (ch - ih) / 2; }
                    else { oy = Math.min(0, Math.max(ch - ih, oy)); }
                    if (iw <= cw) { ox = (cw - iw) / 2; }
                    else { ox = Math.min(0, Math.max(cw - iw, ox)); }
                }
                canvas.onmousedown = function(e) { dragging = true; sx = e.offsetX - ox; sy = e.offsetY - oy; canvas.style.cursor = 'grabbing'; };
                canvas.onmousemove = function(e) { if (dragging) { ox = e.offsetX - sx; oy = e.offsetY - sy; clampPos(); draw(); } };
                canvas.onmouseup = canvas.onmouseleave = function() { dragging = false; canvas.style.cursor = isBg ? 'grab' : 'crosshair'; };
                canvas.onwheel = function(e) {
                    e.preventDefault();
                    if (isBg) {
                        oy -= e.deltaY;
                    } else {
                        const z = e.deltaY < 0 ? 1.1 : 0.9;
                        iw *= z; ih *= z;
                        ox -= (e.offsetX - ox) * (z - 1);
                        oy -= (e.offsetY - oy) * (z - 1);
                    }
                    clampPos(); draw();
                };

                overlay.appendChild(canvas);
                const btnRow = document.createElement('div'); btnRow.style.cssText = 'display:flex;gap:10px';
                const okBtn = document.createElement('button'); okBtn.textContent = '确定';
                okBtn.style.cssText = 'padding:8px 24px;border:none;border-radius:8px;background:#e8a0b4;color:#fff;cursor:pointer;font-size:14px';
                okBtn.onclick = function() {
                    const sx = -ox * img.width / iw;
                    const sy = -oy * img.height / ih;
                    const sw = cw * img.width / iw;
                    const sh = ch * img.height / ih;
                    const outSize = isBg ? Math.round(1920 * (sw / img.width)) : 256;
                    const outH = isBg ? Math.round(outSize * (sh / sw)) : outSize;
                    const out = document.createElement('canvas');
                    out.width = outSize; out.height = outH;
                    out.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, outSize, outH);
                    const quality = isBg ? 0.92 : 0.85;
                    callback(out.toDataURL('image/jpeg', quality));
                    overlay.remove();
                };
                const cancelBtn = document.createElement('button'); cancelBtn.textContent = '取消';
                cancelBtn.style.cssText = 'padding:8px 24px;border:1px solid #888;border-radius:8px;background:transparent;color:#ccc;cursor:pointer;font-size:14px';
                cancelBtn.onclick = function() { overlay.remove(); };
                btnRow.appendChild(okBtn); btnRow.appendChild(cancelBtn);
                overlay.appendChild(btnRow);
            };
            img.src = src;
            document.body.appendChild(overlay);
        }

        // ========== 右键菜单 ==========
        const _ctxItems = {
            bg: [
                { label: '更换背景', icon: 'image', action: () => pickImage('bg') },
                { label: '清除背景', icon: 'trash-2', action: () => { fetch(_imgUrl('bg'),{method:'DELETE'}).catch(()=>{}); document.querySelector('.chat-area').style.backgroundImage = ''; hideCtxMenu(); } },
            ],
            avatar_ai: [
                { label: '更换小夜子头像', icon: 'user-round', action: () => pickImage('avatar_ai') },
                { label: '恢复默认', icon: 'undo-2', action: () => { fetch(_imgUrl('avatar_ai'),{method:'DELETE'}).catch(()=>{}); location.reload(); } },
            ],
            avatar_user: [
                { label: '更换我的头像', icon: 'user', action: () => pickImage('avatar_user') },
                { label: '恢复默认', icon: 'undo-2', action: () => { fetch(_imgUrl('avatar_user'),{method:'DELETE'}).catch(()=>{}); location.reload(); } },
            ],
        };

        function showCtxMenu(e, menuId) {
            e.preventDefault();
            const menu = document.getElementById('ctx-menu');
            const items = _ctxItems[menuId] || [];
            menu.innerHTML = items.map((it, i, arr) => {
                let html = `<button onclick="hideCtxMenu();(${it.action.toString()})()"><i data-lucide="${it.icon}" style="width:14px;height:14px"></i> ${it.label}</button>`;
                return html;
            }).join('');
            menu.style.display = 'block';
            menu.style.left = Math.min(e.clientX, window.innerWidth - 170) + 'px';
            menu.style.top = Math.min(e.clientY, window.innerHeight - 120) + 'px';
            lucide.createIcons();
        }

        function hideCtxMenu() {
            document.getElementById('ctx-menu').style.display = 'none';
        }

        document.addEventListener('click', (e) => {
            const menu = document.getElementById('ctx-menu');
            if (menu && !menu.contains(e.target)) menu.style.display = 'none';
        });

        // 左键头像 → 换头像 / 展开肖像面板
        document.addEventListener('click', function(e) {
            const av = e.target.closest('.msg-avatar');
            if (!av) return;
            // 排除右键菜单里的点击
            if (e.target.closest('#ctx-menu')) return;
            const row = av.closest('.msg-row');
            if (!row) return;

            if (row.classList.contains('user')) {
                // 左键用户头像 → 直接换头像
                pickImage('avatar_user');
            } else {
                // 左键 AI 头像
                const bg = av.style.backgroundImage;
                if (bg && bg !== 'none') {
                    // 有自定义头像 → 展开肖像面板
                    const panel = document.getElementById('portrait-panel');
                    const portraitImg = panel.querySelector('.portrait-img');
                    portraitImg.style.backgroundImage = bg;
                    portraitImg.style.backgroundSize = 'cover';
                    panel.classList.add('show');
                } else {
                    // 无自定义头像 → 直接换头像
                    pickImage('avatar_ai');
                }
            }
        });

        // 右键聊天气泡头像 → 各自的菜单
        document.addEventListener('contextmenu', function(e) {
            const av = e.target.closest('.msg-avatar');
            if (av) {
                const row = av.closest('.msg-row');
                if (row && row.classList.contains('user')) showCtxMenu(e, 'avatar_user');
                else showCtxMenu(e, 'avatar_ai');
            }
        });

        // 右键聊天区背景 → 背景菜单
        document.getElementById('messages').addEventListener('contextmenu', function(e) {
            if (!e.target.closest('.msg-avatar') && !e.target.closest('.msg-bubble')) {
                showCtxMenu(e, 'bg');
            }
        });

        // ===== 背景高亮模式 =====
        let _pinnedBgPct = 0;  // 选框确定的固定展示位，高亮滑动不影响它

        function _applyBgPosition(pct) {
            const chat = document.querySelector('.chat-area');
            chat.style.backgroundPositionY = pct + '%';
        }

        function enterBgHighlight() {
            const chat = document.querySelector('.chat-area');
            const bgUrl = chat.style.backgroundImage;
            if (!bgUrl || bgUrl === 'none') return;

            const url = bgUrl.replace(/url\(["']?/, '').replace(/["']?\)/, '');
            const img = document.getElementById('bg-scroll-img');
            const layer = document.getElementById('bg-scroll-layer');

            const doScroll = function() {
                const renderedH = img.naturalWidth ? img.naturalHeight * (layer.clientWidth / img.naturalWidth) : 0;
                const maxScroll = Math.max(1, renderedH - layer.clientHeight);
                layer.scrollTop = _pinnedBgPct / 100 * maxScroll;
            };

            chat.classList.add('bg-highlight');

            if (img.src === url && img.complete) {
                doScroll();
            } else {
                img.onload = doScroll;
                img.src = url;
            }
        }

        function exitBgHighlight() {
            const chat = document.querySelector('.chat-area');
            // 恢复选框位，不保存滑动位置
            _applyBgPosition(_pinnedBgPct);
            chat.classList.remove('bg-highlight');
        }

        function toggleBgHighlight() {
            const chat = document.querySelector('.chat-area');
            if (chat.classList.contains('bg-highlight')) {
                exitBgHighlight();
            } else {
                enterBgHighlight();
            }
        }

        function _saveBgPosition(pct) {
            _pinnedBgPct = pct;
            _applyBgPosition(pct);
            if (!currentSessionId) return;
            fetch('/bg-position', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({session_id: currentSessionId, position: pct})
            }).catch(() => {});
        }
        // 左键聊天区空白处 → 切换高亮
        document.getElementById('messages').addEventListener('click', function(e) {
            if (!e.target.closest('.msg-avatar') && !e.target.closest('.msg-bubble')) {
                toggleBgHighlight();
            }
        });
        // Esc 退出高亮
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const chat = document.querySelector('.chat-area');
                if (chat.classList.contains('bg-highlight')) toggleBgHighlight();
            }
        });
