        // ========== 会话管理 ==========
        async function loadSessions() {
            try {
                const res = await fetch(`${API}/sessions`);
                const sessions = await res.json();
                const list = document.getElementById('session-list');
                list.innerHTML = '';
                sessions.forEach(s => {
                    const div = document.createElement('div');
                    div.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
                    div.dataset.sessionId = s.id;
                    div.innerHTML = `
                        <span class="session-title">${escapeHtml(s.title || '未命名')}</span>
                        <span class="session-actions">
                            <button onclick="event.stopPropagation(); renameSession('${s.id}', '${escapeHtml(s.title || '未命名')}')" title="重命名">
                                <i data-lucide="pencil" style="width:12px;height:12px"></i></button>
                            <button onclick="event.stopPropagation(); deleteSession('${s.id}')" title="删除">
                                <i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
                        </span>
                    `;
                    div.onclick = () => switchSession(s.id);
                    list.appendChild(div);
                });
                lucide.createIcons();
            } catch (e) {
                showSystem('无法连接服务器');
            }
        }

        async function createSession() {
            try {
                const res = await fetch(`${API}/sessions`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: '新会话' })
                });
                const data = await res.json();
                currentSessionId = data.session_id;
                localStorage.setItem('yona_session_id', currentSessionId);
                document.getElementById('messages').innerHTML = '';
                document.getElementById('current-title').textContent = data.title;
                _setFeedHeading(data.title || '内心');
                // 新会话 = 无快照 = 回旗舰/全局默认
                if (typeof _applySessionSettings === 'function') _applySessionSettings({});
                await loadSessions();
                setStatus('新会话已创建');
            } catch (e) {
                showSystem('创建会话失败');
            }
        }

        async function switchSession(id) {
            currentSessionId = id;
            localStorage.setItem('yona_session_id', id);
            document.getElementById('messages').innerHTML = '';
            await loadSessions();

            try {
                const res = await fetch(`${API}/sessions/${id}`);
                if (!res.ok) {
                    // 会话已被删除或不存在，清除本地记录
                    localStorage.removeItem('yona_session_id');
                    currentSessionId = null;
                    document.getElementById('current-title').textContent = '小夜子';
                    setStatus('会话不存在，请创建新会话');
                    return;
                }
                const data = await res.json();
                document.getElementById('current-title').textContent = data.title || '未命名';
                _setFeedHeading(data.title || '内心');
                (data.messages || []).forEach(m => {
                    appendMessage(m.role, m.content, m.id, m.sensory || null);
                });
                // 2026-09 任务6:该会话的快照(若有)回填到设置控件
                if (typeof _applySessionSettings === 'function') {
                    _applySessionSettings(data.settings || {});
                }
                // 加载该会话绑定的图片配置
                await _loadSessionImages();
            } catch (e) {
                console.error('switchSession 失败 id=' + id, e);
                showSystem('加载历史失败: ' + (e.message || String(e)));
                setStatus('ERR: ' + (e.message || String(e)));
            }
        }

        async function renameSession(id, currentTitle) {
            const newTitle = prompt('新标题:', currentTitle);
            if (!newTitle || newTitle === currentTitle) return;
            try {
                await fetch(`${API}/sessions/${id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: newTitle })
                });
                await loadSessions();
                if (id === currentSessionId) {
                    document.getElementById('current-title').textContent = newTitle;
                }
            } catch (e) {
                showSystem('重命名失败');
            }
        }

        async function deleteSession(id) {
            if (!confirm('确定删除此会话？')) return;
            try {
                await fetch(`${API}/sessions/${id}`, { method: 'DELETE' });
                if (id === currentSessionId) {
                    currentSessionId = null;
                    localStorage.removeItem('yona_session_id');
                    document.getElementById('messages').innerHTML = '';
                }
                await loadSessions();
                if (!currentSessionId && document.getElementById('session-list').children.length === 0) {
                    // 全删了，清空聊天区，需要时用户手动加号
                    document.getElementById('current-title').textContent = '小夜子';
                    setStatus('就绪');
                }
            } catch (e) {
                showSystem('删除失败');
            }
        }
