        // ========== 上下文预设 ==========
        document.getElementById('preset-select').onchange = (e) => {
            if (e.target.value) {
                localStorage.setItem('yona_preset', e.target.value);
            } else {
                localStorage.removeItem('yona_preset');
            }
        };

        async function loadPresets() {
            try {
                const res = await fetch(`${API}/presets`);
                const presets = await res.json();
                const sel = document.getElementById('preset-select');
                sel.innerHTML = '<option value="">-- 选择预设 --</option>' +
                    presets.map(p => `<option value="${p.name}">${p.title}</option>`).join('');
                const saved = localStorage.getItem('yona_preset');
                if (saved && presets.some(p => p.name === saved)) {
                    sel.value = saved;
                    await loadPreset();
                }
            } catch (e) { console.warn('加载预设列表失败', e); }
        }

        async function loadPreset() {
            const name = document.getElementById('preset-select').value;
            if (!name) return;
            try {
                const res = await fetch(`${API}/presets/${name}`);
                const p = await res.json();
                if (p.model) {
                    document.getElementById('model-select').value = p.model;
                    document.getElementById('model-display').textContent = p.model;
                }
                if (p.temperature != null) {
                    document.getElementById('temperature').value = p.temperature;
                    document.getElementById('temp-display').textContent = p.temperature;
                }
                if (p.prompt) {
                    document.getElementById('system-prompt').value = p.prompt;
                }
                if (p.sources && p.sources.sliding_window) {
                    const sw = p.sources.sliding_window;
                    if (sw.max_rounds !== undefined) {
                        document.getElementById('max-rounds').value = sw.max_rounds;
                        document.getElementById('rounds-num').value = sw.max_rounds;
                    }
                }
                // (2026-09:token_budget/summarize 控件已撤 —— 旧预设里的这两个
                //  sources 字段不再回填;输出上限固定、摘要压缩待 compact)
                localStorage.setItem('yona_preset', name);
                updateMeta();
                // 2026-09 任务6:应用预设 = 复制进当前会话快照(程序赋值不触发
                // input 事件,这里手动触发防抖保存)
                if (typeof _scheduleSnapshotSave === 'function') _scheduleSnapshotSave();
                setStatus(`已加载预设: ${p.title}`);
            } catch (e) { showSystem('加载预设失败'); }
        }

        async function savePreset() {
            const name = prompt('预设文件名（英文，不含 .yaml）:');
            if (!name) return;
            const title = prompt('显示名称（中文）:', name) || name;
            const desc = prompt('说明（可选）:', '') || '';
            const settings = getSettings();
            try {
                await fetch(`${API}/presets`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name, title, description: desc,
                        prompt: settings.system_prompt || '',
                        model: settings.model !== defaultSettings.model ? settings.model : null,
                        temperature: settings.temperature,
                        sources: {
                            // 只存真生效的旋钮:上下文窗口轮数(2026-09 真接线)。
                            // 输出上限固定 4096 服务端、摘要压缩待 compact —— 不入预设。
                            sliding_window: { max_rounds: settings.max_rounds || 20 }
                        }
                    })
                });
                await loadPresets();
                document.getElementById('preset-select').value = name;
                setStatus(`已保存预设: ${title}`);
            } catch (e) { showSystem('保存预设失败'); }
        }

        async function deletePreset() {
            const name = document.getElementById('preset-select').value;
            if (!name) return;
            if (!confirm(`删除预设 "${name}"？`)) return;
            try {
                await fetch(`${API}/presets/${name}`, { method: 'DELETE' });
                await loadPresets();
                setStatus(`已删除预设: ${name}`);
            } catch (e) { showSystem('删除预设失败'); }
        }

        function setStatus(text) {
            document.getElementById('status-text').textContent = text;
        }

        // ========== 内心活动面板 ==========
        async function _refreshInnerLife() {
            try {
                const res = await fetch(`${API}/admin/agent-feed?limit=8`);
                if (!res.ok) return;
                const data = await res.json();
                // 更新心情
                const moodEl = document.getElementById('feed-mood');
                if (data.mood) {
                    moodEl.textContent = `心情 ${data.mood.mood} · 精力 ${data.mood.energy} — ${data.mood.desc}`;
                } else {
                    moodEl.textContent = '';
                }
                // 更新事件
                const listEl = document.getElementById('feed-list');
                if (data.events && data.events.length) {
                    listEl.innerHTML = data.events.map(e =>
                        `<div class="feed-item"><span class="time">${(e.created_at || '').slice(-5)}</span>${escapeHtml(e.content)}</div>`
                    ).join('');
                } else {
                    listEl.innerHTML = '<div class="feed-item" style="color:var(--text-faint)">暂无活动</div>';
                }
            } catch(e) {}
        }
        setInterval(_refreshInnerLife, 60000);
        _refreshInnerLife();
