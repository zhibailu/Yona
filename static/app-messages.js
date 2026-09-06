        // ========== 消息显示与操作 ==========
        function appendMessage(role, content, msgId = null, sensory = null) {
            const container = document.getElementById('messages');

            // system 消息特殊处理
            if (role === 'system') {
                const div = document.createElement('div');
                div.className = 'msg-row system';
                div.innerHTML = `<div class="msg-bubble">${escapeHtml(content)}</div>`;
                container.appendChild(div);
                container.scrollTop = container.scrollHeight;
                return div;
            }

            const row = document.createElement('div');
            row.className = 'msg-row ' + role;

            // 头像
            const avatar = document.createElement('div');
            avatar.className = 'msg-avatar';
            const defaultTxt = role === 'user' ? '我' : '🌸';
            avatar.dataset.defaultText = defaultTxt;
            avatar.textContent = defaultTxt;
            // 异步加载服务端头像
            const avUrl = _imgUrl(role === 'user' ? 'avatar_user' : 'avatar_ai');
            fetch(avUrl).then(r => { if (r.ok) {
                avatar.style.backgroundImage = `url(${avUrl})`;
                avatar.style.backgroundSize = 'cover';
                avatar.textContent = '';
            }}).catch(()=>{});

            // 气泡
            const bubble = document.createElement('div');
            bubble.className = 'msg-bubble';
            bubble.innerHTML = `<div class="msg-content">${escapeHtml(content)}</div>`;
            if (sensory) bubble._sensory = sensory;

            if (msgId) {
                bubble.dataset.msgId = msgId;
                const actionsDiv = document.createElement('div');
                actionsDiv.className = 'msg-actions';
                if (role === 'user') {
                    actionsDiv.innerHTML = `
                        <button onclick="editMessage(this)"><i data-lucide="pencil" style="width:12px;height:12px"></i></button>
                        <button onclick="deleteMessage(this)"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
                        <button onclick="retryMessage(this)"><i data-lucide="rotate-ccw" style="width:12px;height:12px"></i></button>
                    `;
                } else {
                    actionsDiv.innerHTML = `
                        <button onclick="deleteMessage(this)"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
                        <button onclick="regenerateMessage(this)"><i data-lucide="refresh-cw" style="width:12px;height:12px"></i></button>
                    `;
                }
                bubble.appendChild(actionsDiv);
            }
            if (sensory && sensory.visual) {
                _renderVisionInBubble(bubble, sensory.visual);
            }
            if (sensory && sensory.voice) {
                _renderVoiceInBubble(bubble, sensory.voice);
            }

            row.appendChild(avatar);
            row.appendChild(bubble);
            container.appendChild(row);
            container.scrollTop = container.scrollHeight;
            lucide.createIcons();
            return row;
        }

        // 为气泡注入操作按钮（msgId 后补时用）
        function _injectMsgActions(bubble, role) {
            if (!bubble || bubble.querySelector('.msg-actions')) return;
            const actionsDiv = document.createElement('div');
            actionsDiv.className = 'msg-actions';
            if (role === 'user') {
                actionsDiv.innerHTML = `
                    <button onclick="editMessage(this)"><i data-lucide="pencil" style="width:12px;height:12px"></i></button>
                    <button onclick="deleteMessage(this)"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
                    <button onclick="retryMessage(this)"><i data-lucide="rotate-ccw" style="width:12px;height:12px"></i></button>
                `;
            } else {
                actionsDiv.innerHTML = `
                    <button onclick="deleteMessage(this)"><i data-lucide="trash-2" style="width:12px;height:12px"></i></button>
                    <button onclick="regenerateMessage(this)"><i data-lucide="refresh-cw" style="width:12px;height:12px"></i></button>
                `;
            }
            bubble.appendChild(actionsDiv);
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text == null ? '' : String(text);
            return div.innerHTML;
        }

        function escapeAttr(text) {
            return escapeHtml(text).replace(/"/g, '&quot;');
        }

        function showSystem(text) {
            appendMessage('system', text);
        }

        function showError(text) {
            const container = document.getElementById('messages');
            const row = document.createElement('div');
            row.className = 'msg-row error';
            row.innerHTML = `<div class="msg-avatar" style="font-size:16px">⚠️</div><div class="msg-bubble">${escapeHtml(text)}</div>`;
            container.appendChild(row);
            container.scrollTop = container.scrollHeight;
            _injectMsgActions(row.querySelector('.msg-bubble'), 'assistant');
            lucide.createIcons();
        }

        // 显示/移除 typing indicator
        function showTyping() {
            const container = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'typing-indicator';
            div.id = 'typing-indicator';
            div.innerHTML = `
                <div class="msg-avatar" id="typing-avatar">🌸</div>
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
            `;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }

        function hideTyping() {
            const el = document.getElementById('typing-indicator');
            if (el) el.remove();
        }

        // ========== 消息操作 ==========
        function editMessage(btn) {
            const msgDiv = btn.closest('.msg-row');
            const bubble = msgDiv.querySelector('.msg-bubble');
            const msgId = bubble.dataset.msgId;
            const contentDiv = bubble.querySelector('.msg-content');
            const oldText = contentDiv.textContent;

            const textarea = document.createElement('textarea');
            textarea.className = 'msg-edit';
            textarea.value = oldText;
            contentDiv.innerHTML = '';
            contentDiv.appendChild(textarea);
            textarea.focus();

            const save = async () => {
                const newText = textarea.value.trim();
                if (!newText || newText === oldText) {
                    contentDiv.textContent = oldText;
                    return;
                }
                if (!msgId) {
                    contentDiv.textContent = newText;
                    return;
                }
                try {
                    const res = await fetch(`${API}/messages/${msgId}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: newText })
                    });
                    if (!res.ok) throw new Error('更新失败');
                    contentDiv.textContent = newText;
                    await _refreshObjects();
                } catch (e) {
                    contentDiv.textContent = oldText;
                    showSystem('编辑失败');
                }
            };

            textarea.onblur = save;
            textarea.onkeydown = (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    save();
                }
            };
        }

        async function deleteMessage(btn) {
            const msgDiv = btn.closest('.msg-row');
            const bubble = msgDiv.querySelector('.msg-bubble');
            const msgId = bubble.dataset.msgId;
            if (!msgId) return;
            if (!confirm(`删除从此消息开始的所有消息？`)) return;
            try {
                const params = new URLSearchParams();
                if (currentSessionId) params.set('session_id', currentSessionId);
                const url = `${API}/messages/from/${msgId}` + (params.toString() ? '?' + params.toString() : '');
                const res = await fetch(url, { method: 'DELETE' });
                if (!res.ok) throw new Error('删除失败');
                const data = await res.json().catch(() => ({}));
                if (data.deleted_objects) showSystem(`已收走 ${data.deleted_objects} 个绑定产物。`);
            } catch (e) {
                showSystem('删除失败');
                return;
            }
            if (currentSessionId) {
                await switchSession(currentSessionId);
                await _refreshObjects();
            }
        }

        async function regenerateMessage(btn) {
            const msgDiv = btn.closest('.msg-row');
            // 往回找最近的一条用户消息（可能被占位气泡或错误气泡隔开）
            let prevMsg = msgDiv.previousElementSibling;
            while (prevMsg && !prevMsg.classList.contains('user')) {
                prevMsg = prevMsg.previousElementSibling;
            }
            if (!prevMsg) {
                showSystem('无法重新生成：找不到上一条用户消息');
                return;
            }
            const prevBubble = prevMsg.querySelector('.msg-bubble');
            const userId = prevBubble.dataset.msgId;
            const userText = prevBubble.querySelector('.msg-content').textContent;

            if (!userId) return;
            if (!confirm('从这条消息开始重新对话？')) return;
            const sensory = await _prepareSensoryRetry(prevBubble._sensory);
            if (sensory === false) return;

            try {
                const params = new URLSearchParams();
                if (currentSessionId) params.set('session_id', currentSessionId);
                const res = await fetch(`${API}/messages/from/${userId}?${params}`, { method: 'DELETE' });
                const data = await res.json().catch(() => ({}));
                if (data.deleted_objects) showSystem(`已收走 ${data.deleted_objects} 个绑定产物。`);
            } catch (e) { showSystem('删除失败'); return; }

            await switchSession(currentSessionId);
            await _refreshObjects();
            await sendMessage(userText, sensory);
        }

        async function retryMessage(btn) {
            const msgDiv = btn.closest('.msg-row');
            const bubble = msgDiv.querySelector('.msg-bubble');
            const userId = bubble.dataset.msgId;
            const userText = bubble.querySelector('.msg-content').textContent;

            if (!userId) return;
            if (!confirm('从这条消息开始重新对话？')) return;
            const sensory = await _prepareSensoryRetry(bubble._sensory);
            if (sensory === false) return;

            try {
                const params = new URLSearchParams();
                if (currentSessionId) params.set('session_id', currentSessionId);
                const res = await fetch(`${API}/messages/from/${userId}?${params}`, { method: 'DELETE' });
                const data = await res.json().catch(() => ({}));
                if (data.deleted_objects) showSystem(`已收走 ${data.deleted_objects} 个绑定产物。`);
            } catch (e) { showSystem('删除失败'); return; }

            await switchSession(currentSessionId);
            await _refreshObjects();
            await sendMessage(userText, sensory);
        }

        async function _prepareSensoryRetry(sensory) {
            if (!sensory) return null;
            const copy = JSON.parse(JSON.stringify(sensory));
            for (const key of ['visual', 'voice']) {
                const item = copy[key];
                if (!item || item.data_url || !item.media_url) continue;
                try {
                    const response = await fetch(item.media_url);
                    if (!response.ok) throw new Error(`媒体读取失败 (${response.status})`);
                    item.data_url = await _blobToDataUrl(await response.blob());
                } catch (error) {
                    showSystem(`无法重新读取这轮的${key === 'visual' ? '图片' : '语音'}：${error.message || error}`);
                    return false;
                }
            }
            return copy;
        }

        // ========== 发送消息 ==========
        function setupInput() {
            const input = document.getElementById('input-box');
            const inputArea = document.querySelector('.input-area');
            const chatArea = document.querySelector('.chat-area');
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
            input.addEventListener('input', () => {
                input.style.height = 'auto';
                input.style.height = Math.min(input.scrollHeight, 120) + 'px';
            });
            input.addEventListener('paste', handleVisionPaste);
            [inputArea, chatArea].forEach(target => {
                if (!target) return;
                target.addEventListener('dragenter', handleVisionDragEnter);
                target.addEventListener('dragover', handleVisionDragOver);
                target.addEventListener('dragleave', handleVisionDragLeave);
                target.addEventListener('drop', handleVisionDrop);
            });
        }

        let _sendCount = 0;
        async function sendMessage(overrideText = null, overrideSensory = null) {
            _sendCount++;
            const countId = 'SEND-' + _sendCount;
            document.getElementById('status-text').textContent = countId + ' sending...';
            // 2026-09 连接管理:没连接过引擎 = 服务端 503,别假装发送,引导去连接
            if (!window._connected) {
                document.getElementById('status-text').textContent = '先连接模型才能聊天';
                if (typeof openConnect === 'function') openConnect();
                return;
            }
            const input = document.getElementById('input-box');
            const baseText = overrideText || input.value.trim();
            const sensoryPayload = overrideSensory || _sensoryPayload();
            if ((!baseText && !sensoryPayload) || isSending || !currentSessionId) {
                document.getElementById('status-text').textContent = countId + ' BLOCKED';
                return;
            }

            isSending = true;
            if (!overrideText) input.value = '';
            input.style.height = 'auto';
            setStatus('小夜子正在思考...');
            const objectIdsBefore = new Set(_knownObjectIds);
            _setStageStatus('她接过你的话，正在判断要不要动手。', 'thinking');
            const btn = document.getElementById('send-btn');
            btn.disabled = true;

            const userDiv = appendMessage('user', baseText || '给你看这个。', null, sensoryPayload);
            showTyping();

            const settings = getSettings();
            let aiDiv = null;
            _beginStreamingSpeech();

            try {
                const res = await fetch(`${API}/chat/stream`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: currentSessionId,
                        message: baseText,
                        sensory: sensoryPayload,
                        model: settings.model,
                        temperature: settings.temperature,
                        system_prompt: settings.system_prompt,
                        // max_rounds = 上下文窗口(2026-09 真接线);
                        // max_tokens/enable_summarize 已不再发送(占位字段,见 chat.py)
                        max_rounds: settings.max_rounds,
                    })
                });

                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || '请求失败');
                }

                hideTyping();
                aiDiv = appendMessage('assistant', '...');

                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let fullText = '';
                let firstToken = false;
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        try {
                            const data = JSON.parse(line.slice(6));
                            if (data._mutter) {
                                firstToken = true;
                                fullText = data._mutter;
                                const contentEl = aiDiv.querySelector('.msg-bubble .msg-content');
                                if (contentEl) {
                                    contentEl.textContent = data._mutter;
                                    contentEl.style.opacity = '0.5';
                                }
                            } else if (data.tool_status) {
                                _setStageStatus(data.tool_status, 'thinking');
                                _setStageTraceText('只读工具正在工作，外部内容不会获得操作权限。');
                            } else if (data.busy) {
                                _setStageStatus(data.busy_text || '她正在忙别的事，消息已排队。', 'thinking');
                            } else if (data.token) {
                                if (firstToken) {
                                    firstToken = false;
                                    fullText = '';
                                    const contentEl = aiDiv.querySelector('.msg-bubble .msg-content');
                                    if (contentEl) contentEl.style.opacity = '1';
                                }
                                fullText += data.token;
                                _queueStreamingSpeech(data.token);
                                const contentEl = aiDiv.querySelector('.msg-bubble .msg-content');
                                if (contentEl) contentEl.textContent = fullText;
                                const container = document.getElementById('messages');
                                container.scrollTop = container.scrollHeight;
                            } else if (data.done) {
                                if (data.user_msg_id) {
                                    const ub = userDiv.querySelector('.msg-bubble');
                                    ub.dataset.msgId = data.user_msg_id;
                                    _injectMsgActions(ub, 'user');
                                }
                                if (data.assistant_msg_id) {
                                    const ab = aiDiv.querySelector('.msg-bubble');
                                    ab.dataset.msgId = data.assistant_msg_id;
                                    _injectMsgActions(ab, 'assistant');
                                }
                                lucide.createIcons();
                                _markObjectsPending();
                                _setStageStatus('她说完了，正在把能留下的东西放到桌面上。', 'acting');
                                _watchForNewObject(objectIdsBefore);
                                setTimeout(_refreshWorkspace, 900);
                            } else if (data.error) {
                                throw new Error(data.error);
                            }
                        } catch (parseErr) {
                            // skip unparseable lines
                        }
                    }
                }

                if (!fullText) throw new Error('LLM 返回空响应');
                _flushStreamingSpeech();
                clearSensoryAttachments();
                setStatus(`${settings.model} · T=${settings.temperature}`);

            } catch (e) {
                _cancelStreamingSpeech();
                hideTyping();
                if (aiDiv && aiDiv.parentNode) aiDiv.remove();
                const container = document.getElementById('messages');
                const row = document.createElement('div');
                row.className = 'msg-row error';
                row.innerHTML = `<div class="msg-avatar" style="font-size:16px">⚠️</div><div class="msg-bubble">错误: ${escapeHtml(e.message)}</div>`;
                container.appendChild(row);
                container.scrollTop = container.scrollHeight;
                _injectMsgActions(row.querySelector('.msg-bubble'), 'assistant');
                lucide.createIcons();
                setStatus('发生错误');
            } finally {
                isSending = false;
                btn.disabled = false;
                input.focus();
            }
        }
