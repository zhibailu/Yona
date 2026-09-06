        const API = 'http://127.0.0.1:8000';
        let currentSessionId = null;
        let isSending = false;
        let defaultSettings = {};
        let editingMsgId = null;

        // ========== 初始化 ==========
        window.onload = async () => {
            lucide.createIcons();
            await loadSettings();
            await loadModels();
            await loadPresets();
            await loadSessions();
            const saved = localStorage.getItem('yona_session_id');
            if (saved && document.querySelector(`[data-session-id="${saved}"]`)) {
                await switchSession(saved);
            } else {
                const first = document.querySelector('.session-item');
                if (first) {
                    await switchSession(first.dataset.sessionId);
                } else {
                    await createSession();
                }
            }
            _loadSessionImages();
            _refreshObjects();
            // 2026-09 任务4:speak/voice 开关已撤(无后端),onload 不再读它
            setupInput();
            _watchSnapshotAutoSave();
        };

        // ========== 设置加载 ==========
        async function loadSettings() {
            try {
                const res = await fetch(`${API}/settings`);
                defaultSettings = await res.json();
                document.getElementById('temperature').value = defaultSettings.temperature;
                document.getElementById('temp-display').textContent = defaultSettings.temperature;
                document.getElementById('system-prompt').value = '';
                updateMeta();
            } catch (e) {
                showSystem('无法加载设置，请确认服务已启动');
            }

            try {
                const ctxRes = await fetch(`${API}/context/sources`);
                const sources = await ctxRes.json();
                for (const s of sources) {
                    if (s.source_id === 'sliding_window') {
                        // 上下文窗口 = 保留最近 N 轮(2026-09 真接线,按轮边界裁剪)
                        defaultSettings.max_rounds = s.config.max_rounds || 20;
                        const roundsEl = document.getElementById('max-rounds');
                        const roundsNum = document.getElementById('rounds-num');
                        if (roundsEl) roundsEl.value = s.config.max_rounds || 20;
                        if (roundsNum) roundsNum.value = s.config.max_rounds || 20;
                    }
                    // token_budget / summarize:占位(compact 未接,2026-09 起
                    // UI 不再提供无效滑块 —— 见 index.html 上下文预算信息卡)
                }
            } catch (e) {
                console.warn('上下文配置加载失败，使用默认值', e);
            }
        }

        async function loadModels() {
            // 2026-09 连接管理:下拉 = /settings 返回的"当前端点可用模型"
            // (引擎快照,连接向导实测拉通后缓存)。未连接 = 空 + 弹向导。
            const select = document.getElementById('model-select');
            const note = document.getElementById('conn-note');
            const st = defaultSettings || {};
            const models = (st.models || []).map(m => (typeof m === 'string' ? m : m.id));
            window._connected = st.configured === true;
            select.innerHTML = models.map(id =>
                `<option value="${escapeAttr(id)}">${escapeHtml(id)}</option>`
            ).join('');
            if (window._connected) {
                const saved = localStorage.getItem('yona_model');
                const want = models.includes(saved) ? saved : (st.model || models[0] || '');
                if (want) select.value = want;
                document.getElementById('model-display').textContent = select.value || '';
                if (note) note.textContent = '已连接 · 下拉切换即生效（同端点）';
                window._cfgModel = st.model || models[0] || '';
            } else {
                document.getElementById('model-display').textContent = '未连接';
                if (note) note.textContent = '先「连接你的模型」才能聊天';
                // 首启(没连过)自动弹向导;失败容错不阻塞其它 UI
                setTimeout(() => { try { openConnect(); } catch (e) {} }, 300);
            }
            select.onchange = () => {
                const v = select.value;
                if (v) localStorage.setItem('yona_model', v);
                document.getElementById('model-display').textContent = v || '未连接';
                updateMeta();
            };
            updateMeta();
        }

        function updateMeta() {
            const sel = document.getElementById('model-select');
            const model = (sel && sel.value) ? sel.value : (window._connected ? '' : '(未连接)');
            const temp = document.getElementById('temperature').value;
            document.getElementById('current-meta').textContent = `${model} · T=${temp}`;
        }

        document.getElementById('temperature').oninput = (e) => {
            document.getElementById('temp-display').textContent = e.target.value;
            updateMeta();
        };

        document.getElementById('max-rounds').oninput = (e) => {
            document.getElementById('rounds-num').value = e.target.value;
        };
        document.getElementById('rounds-num').onchange = (e) => {
            let v = parseInt(e.target.value);
            if (isNaN(v)) v = 20;
            v = Math.max(0, Math.min(40, v));
            e.target.value = v;
            document.getElementById('max-rounds').value = v;
        };
        // (2026-09:Token 预算滑块已撤 —— 输出上限固定 4096 服务端、上下文预算
        //  按轮数窗口,见 index.html 上下文预算信息卡;旧 max-context-tokens/
        //  tokens-num 的 oninput/onchange 随控件一并删除)

        function resetSettings() {
            document.getElementById('model-select').value = defaultSettings.model;
            document.getElementById('temperature').value = defaultSettings.temperature;
            document.getElementById('temp-display').textContent = defaultSettings.temperature;
            document.getElementById('system-prompt').value = '';
            document.getElementById('max-rounds').value = defaultSettings.max_rounds || 20;
            document.getElementById('rounds-num').value = defaultSettings.max_rounds || 20;
            updateMeta();
            // 2026-09 任务6:恢复默认 = 把本会话快照清掉(回旗舰/全局默认)
            _scheduleSnapshotSave();
        }

        function getSettings() {
            const s = {
                // model = 下拉当前选择(同端点可用列表内;连接后一定有值)
                model: document.getElementById('model-select').value || undefined,
                temperature: parseFloat(document.getElementById('temperature').value),
                system_prompt: document.getElementById('system-prompt').value || undefined,
                // max_tokens / enable_summarize 不再发送:输出上限固定 4096(params),
                // 摘要压缩待 compact(都是 UI 不该改/还没接的,见 chat.py 字段注释)。
            };
            // max_rounds = 上下文窗口(2026-09 真接线);注意 0 = 不限制是真值,
            // 别用 || null 把它吞成默认 —— 那是两种语义
            const rv = parseInt(document.getElementById('max-rounds').value, 10);
            s.max_rounds = Number.isNaN(rv) ? null : rv;
            return s;
        }

        // ========== 连接管理 UI(2026-09 任务③:UI 是配置唯一入口)==========
        function openConnect() {
            const ov = document.getElementById('connect-overlay');
            if (!ov) return;
            ov.style.display = 'flex';
            const stat = document.getElementById('conn-status');
            if (stat) stat.textContent = '';
        }

        function closeConnect() {
            const ov = document.getElementById('connect-overlay');
            if (ov) ov.style.display = 'none';
        }

        async function connectSave() {
            const url = document.getElementById('conn-url').value.trim();
            const key = document.getElementById('conn-key').value.trim();
            const model = document.getElementById('conn-model').value.trim();
            const stat = document.getElementById('conn-status');
            const btn = document.getElementById('conn-save-btn');
            if (!url || !key) { if (stat) stat.textContent = '请填写 API 地址和 Key'; return; }
            if (btn) btn.disabled = true;
            if (stat) { stat.style.color = '#888'; stat.textContent = '测通中…'; }
            try {
                const res = await fetch(`${API}/admin/llm-config`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ base_url: url, api_key: key, model: model }),
                });
                const data = await res.json();
                if (!res.ok) { throw new Error(data.detail || '连接失败'); }
                // 连接成功:重拉 /settings 刷新连接态与可用模型
                await loadSettings();
                await loadModels();
                localStorage.setItem('yona_model', data.model || '');
                closeConnect();
                setStatus(`已连接: ${data.base_url} · 默认 ${data.model || ''}`);
            } catch (e) {
                if (stat) { stat.style.color = '#c0392b'; stat.textContent = e.message || String(e); }
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        // ========== 会话快照(2026-09 任务6:每会话一组设定,自动+防抖)==========
        // 三层:当轮(消息里手动改的)> 会话快照(meta.json)> 全局默认。
        // 这里只管"快照"这一层:改动自动 PATCH;切会话时由 _applySessionSettings 回填。
        let _snapshotTimer = null;
        let _applyingSnapshot = false;

        function _collectSnapshot() {
            const s = {};
            const t = parseFloat(document.getElementById('temperature').value);
            if (Number.isFinite(t)) s.temperature = t;
            const rv = parseInt(document.getElementById('max-rounds').value, 10);
            if (!Number.isNaN(rv)) s.max_rounds = rv;   // 0 = 不限制,真存
            const sp = (document.getElementById('system-prompt').value || '').trim();
            if (sp) s.system_prompt = sp;               // 空 = 旗舰,不落键
            const sel = document.getElementById('model-select');
            if (sel && sel.value) s.model = sel.value;
            return s;
        }

        function _scheduleSnapshotSave() {
            if (!currentSessionId || _applyingSnapshot) return;
            clearTimeout(_snapshotTimer);
            _snapshotTimer = setTimeout(async () => {
                const sid = currentSessionId;
                const body = _collectSnapshot();
                try {
                    await fetch(`${API}/sessions/${sid}/settings`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ settings: body }),
                    });
                } catch (e) { console.warn('快照保存失败(会话还在,稍后自动重试)', e); }
            }, 700);  // 防抖:停手 0.7s 才写盘
        }

        function _watchSnapshotAutoSave() {
            const onInput = () => { if (!_applyingSnapshot) _scheduleSnapshotSave(); };
            ['temperature', 'system-prompt', 'max-rounds', 'rounds-num']
                .forEach(id => { const el = document.getElementById(id); if (el) el.addEventListener('input', onInput); });
            const modelSel = document.getElementById('model-select');
            if (modelSel) modelSel.addEventListener('change', onInput);
        }

        function _applySessionSettings(settings) {
            // 切到某会话:把它快照的值回填到控件(不触发自动保存回写)
            _applyingSnapshot = true;
            try {
                const s = settings || {};
                const tempEl = document.getElementById('temperature');
                const tempNum = (typeof s.temperature === 'number') ? s.temperature
                    : (defaultSettings.temperature != null ? defaultSettings.temperature : 0.9);
                tempEl.value = tempNum;
                document.getElementById('temp-display').textContent = tempNum;

                document.getElementById('system-prompt').value = s.system_prompt || '';

                const rounds = (typeof s.max_rounds === 'number') ? s.max_rounds
                    : (defaultSettings.max_rounds || 20);
                document.getElementById('max-rounds').value = rounds;
                document.getElementById('rounds-num').value = rounds;

                const sel = document.getElementById('model-select');
                if (sel) {
                    const want = (s.model && Array.from(sel.options).some(o => o.value === s.model))
                        ? s.model : (defaultSettings.model || sel.options[0]?.value || '');
                    sel.value = want || '';
                    document.getElementById('model-display').textContent = want || '未连接';
                    if (want) localStorage.setItem('yona_model', want);
                }
            } finally {
                _applyingSnapshot = false;
                updateMeta();
            }
        }
