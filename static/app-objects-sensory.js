        // ========== Yona 桌面物件 / 行动舞台 ==========
        let _knownObjectIds = new Set();
        let _announcedObjectIds = new Set();
        let _stageObject = null;

        function _setStageStatus(text, mode = 'idle') {
            const el = document.getElementById('stage-status');
            if (!el) return;
            el.textContent = text;
            el.dataset.mode = mode;
        }

        function _setStageTrace(html) {
            const el = document.getElementById('stage-trace');
            if (el) el.innerHTML = html;
        }

        function _setStageTraceText(text) {
            const el = document.getElementById('stage-trace');
            if (el) el.textContent = text;
        }

        function _markObjectsPending() {
            const listEl = document.getElementById('object-list');
            if (!listEl) return;
            const hasObjects = listEl.querySelector('.object-item');
            if (!hasObjects) {
                listEl.innerHTML = '<div class="feed-item" style="color:var(--text-faint)">她正在整理桌面...</div>';
            }
        }

        async function _refreshObjects() {
            return _refreshWorkspace();
        }

        async function _refreshWorkspace() {
            try {
                const params = new URLSearchParams();
                if (currentSessionId) params.set('session_id', currentSessionId);
                params.set('limit', '18');
                const res = await fetch(`${API}/workspace?${params}`);
                if (!res.ok) return;
                const data = await res.json();
                const objects = data.objects || [];
                _knownObjectIds = new Set(objects.map(o => o.id));
                _renderWorkspaceSummary(data.summary || {}, data.autonomy || {});
                if (objects.length) {
                    _renderObjectList(objects);
                    if (!_stageObject || !objects.some(o => o.id === _stageObject.id)) {
                        _renderStageObject(objects[0], '桌面上最近留下的东西。');
                    }
                } else {
                    const listEl = document.getElementById('object-list');
                    if (listEl) listEl.innerHTML = '<div class="feed-item" style="color:var(--text-faint)">桌面还是空的</div>';
                    _renderStageEmpty();
                }
                _renderActionTrail(data.actions || []);
            } catch(e) {}
        }

        async function _watchForNewObject(beforeIds) {
            for (const delay of [700, 1500, 2800, 5000]) {
                setTimeout(async () => {
                    const objects = await _fetchObjects();
                    if (!objects.length) {
                        _setStageTraceText('还没有看到新物件，可能这一轮她只说话没有动手。');
                        return;
                    }
                    const fresh = objects.find(o => !beforeIds.has(o.id));
                    if (fresh) {
                        const label = _objectTypeLabel(fresh.type);
                        _renderStageObject(fresh, `她刚刚放下了 <strong>${escapeHtml(label)}</strong>。`);
                        _renderObjectList(objects);
                        if (!_announcedObjectIds.has(fresh.id)) {
                            _announcedObjectIds.add(fresh.id);
                            showSystem(`Yona 把「${fresh.title || label}」放到了桌面上。`);
                        }
                    } else if (delay === 5000) {
                        _setStageStatus('这一轮她没有留下新物件。', 'idle');
                        _setStageTraceText('这一轮没有新动作。');
                    }
                }, delay);
            }
        }

        async function _fetchObjects() {
            try {
                const res = await fetch(`${API}/objects?limit=18`);
                if (!res.ok) return [];
                const data = await res.json();
                const objects = data.objects || [];
                _knownObjectIds = new Set(objects.map(o => o.id));
                return objects;
            } catch(e) {
                return [];
            }
        }

        function _renderObjectList(objects) {
            const listEl = document.getElementById('object-list');
            if (!listEl) return;
            if (!objects.length) {
                listEl.innerHTML = '<div class="feed-item" style="color:var(--text-faint)">桌面还是空的</div>';
                return;
            }
            listEl.innerHTML = objects.map(o => `
                <div class="feed-item object-item" onclick="selectStageObject('${o.id}')">
                    <span class="object-type">${_objectTypeLabel(o.type)}</span>
                    <span class="object-title">${escapeHtml(o.title || '')}</span>
                    <div>${escapeHtml((o.content || '').slice(0, 90))}</div>
                    <button class="object-delete" onclick="event.stopPropagation();deleteObject('${o.id}')" title="删除">
                        <i data-lucide="x" style="width:12px;height:12px"></i>
                    </button>
                </div>
            `).join('');
            lucide.createIcons();
        }

        function _renderWorkspaceSummary(summary, autonomy) {
            const el = document.getElementById('workspace-summary');
            if (!el) return;
            const counts = summary.object_counts || {};
            const total = Object.values(counts).reduce((a, b) => a + Number(b || 0), 0);
            const cycles = autonomy.cycles || 0;
            const actionCount = summary.action_count || 0;
            el.innerHTML = `
                <div class="workspace-stat"><strong>${total}</strong><span>物件</span></div>
                <div class="workspace-stat"><strong>${actionCount}</strong><span>动作</span></div>
                <div class="workspace-stat"><strong>${cycles}</strong><span>自循环</span></div>
            `;
        }

        function _renderActionTrail(actions) {
            const el = document.getElementById('action-trail');
            if (!el) return;
            if (!actions.length) {
                el.innerHTML = '<div class="feed-item" style="color:var(--text-faint)">还没有动作轨迹</div>';
                return;
            }
            el.innerHTML = actions.slice(0, 8).map(a => `
                <div class="feed-item action-item">
                    <span class="action-kind">${escapeHtml(_actionLabel(a.action))}</span>
                    <span>
                        <span class="object-title">${escapeHtml(a.title || a.object_type || '')}</span>
                        <div>${escapeHtml((a.summary || a.error || '').slice(0, 76))}</div>
                    </span>
                </div>
            `).join('');
        }

        function _actionLabel(action) {
            return {
                make_note: '便签',
                save_memory_card: '记忆',
                make_clipping: '剪报',
                attach_image_memory: '照片',
                write_diary_entry: '日记',
                list_objects: '查看',
                list_action_events: '轨迹',
            }[action] || action || '动作';
        }

        function _renderStageObject(obj, trace) {
            if (!obj) return _renderStageEmpty();
            _stageObject = obj;
            const titleEl = document.getElementById('stage-title');
            const copyEl = document.getElementById('stage-copy');
            if (titleEl) titleEl.textContent = obj.title || _objectTypeLabel(obj.type);
            if (copyEl) copyEl.textContent = obj.content || '';
            const documentButton = document.getElementById('stage-open-document');
            if (documentButton) {
                documentButton.style.display = obj.metadata && obj.metadata.document_id ? '' : 'none';
            }
            const correctionButton = document.getElementById('stage-correct-object');
            if (correctionButton) correctionButton.style.display = obj.type === 'document' ? 'none' : '';
            closeStageCorrection();
            _setStageStatus('她已经把一个结果放到了你面前。', 'done');
            const revision = Number(obj.revision || 1);
            const corrected = obj.state === 'corrected' ? `，已更正到第 ${revision} 版` : '';
            _setStageTrace(trace || `当前焦点：<strong>${escapeHtml(obj.title || _objectTypeLabel(obj.type))}</strong>${corrected}`);
            // TODO(action-fx): 桌面物件落下、照片收纳、日记生成等动作以后接专门图形效果/特效。
            lucide.createIcons();
        }

        function _renderStageEmpty() {
            _stageObject = null;
            const titleEl = document.getElementById('stage-title');
            const copyEl = document.getElementById('stage-copy');
            if (titleEl) titleEl.textContent = '桌面是空的';
            if (copyEl) copyEl.textContent = '等她真正留下东西，这里会直接亮出来。';
            const documentButton = document.getElementById('stage-open-document');
            if (documentButton) documentButton.style.display = 'none';
            const correctionButton = document.getElementById('stage-correct-object');
            if (correctionButton) correctionButton.style.display = '';
            _setStageStatus('她还没有伸手做什么。', 'idle');
            _setStageTraceText('下一次她产生动作时，我会在这里标出来。');
        }

        async function selectStageObject(id) {
            const objects = await _fetchObjects();
            const obj = objects.find(item => item.id === id);
            if (obj) _renderStageObject(obj);
        }

        // ========== 感官层：视觉 / 语音 ==========
        let _visionAttachment = null;
        let _voiceAttachment = null;
        let _voiceRecognition = null;
        let _mediaRecorder = null;
        let _audioChunks = [];
        let _speechHadResult = false;
        let _speechHadError = false;
        let _voiceOutputEnabled = localStorage.getItem('yona_voice_output') === '1';
        let _speechQueue = [];
        let _speechBuffer = '';
        let _speechDraining = false;
        let _speechGeneration = 0;
        let _speechAudio = null;
        let _voiceWarmupPromise = null;

        function _sensoryPayload() {
            if (!_visionAttachment && !_voiceAttachment) return null;
            const payload = {};
            if (_visionAttachment) {
                payload.visual = {
                    name: _visionAttachment.name,
                    summary: _visionAttachment.summary,
                    mime: _visionAttachment.mime || '',
                    data_url: _visionAttachment.dataUrl || '',
                    analysis: _visionAttachment.analysis || null,
                    proof: _visionAttachment.proof || '',
                };
            }
            if (_voiceAttachment) {
                payload.voice = {
                    transcript: _voiceAttachment.transcript || '',
                    analysis: _voiceAttachment.analysis || null,
                    name: _voiceAttachment.name || 'voice.webm',
                    mime: _voiceAttachment.mime || '',
                    data_url: _voiceAttachment.data_url || _voiceAttachment.dataUrl || '',
                    media_url: _voiceAttachment.media_url || '',
                    asset_id: _voiceAttachment.asset_id || '',
                    proof: _voiceAttachment.proof || '',
                };
            }
            return payload;
        }

        function _renderVisionInMessage(row, attachment) {
            const bubble = row && row.querySelector('.msg-bubble');
            if (!bubble || !attachment) return;
            _renderVisionInBubble(bubble, attachment);
        }

        function _renderVisionInBubble(bubble, attachment) {
            if (!bubble || !attachment) return;
            const wrap = document.createElement('div');
            wrap.className = 'msg-vision';
            const src = attachment.media_url || attachment.data_url || attachment.dataUrl || '';
            const label = attachment.name ? `图片：${attachment.name}` : '图片';
            wrap.title = label;
            if (src) {
                const image = document.createElement('img');
                image.src = src;
                image.alt = '';
                image.onclick = () => _openVisionPreview(src, label);
                wrap.appendChild(image);
            }
            bubble.appendChild(wrap);
        }

        function _renderVoiceInBubble(bubble, voice) {
            if (!bubble || !voice) return;
            const transcript = (voice.transcript || '').trim();
            const src = voice.media_url || voice.data_url || voice.dataUrl || '';
            const acoustics = (voice.analysis && voice.analysis.acoustics) || {};
            const acousticText = _formatAcousticObservation(acoustics);
            const wrap = document.createElement('div');
            wrap.className = 'msg-voice';
            wrap.innerHTML = `
                <i data-lucide="mic" style="width:13px;height:13px"></i>
                <span>
                    <span class="msg-voice-transcript">${escapeHtml(transcript ? `语音：${transcript.slice(0, 80)}` : '语音输入')}</span>
                    ${acousticText ? `<span class="msg-voice-acoustics">${escapeHtml(acousticText)}</span>` : ''}
                </span>
            `;
            if (src) {
                const audio = document.createElement('audio');
                audio.controls = true;
                audio.preload = 'metadata';
                audio.src = src;
                wrap.appendChild(audio);
            }
            bubble.appendChild(wrap);
        }

        function _formatAcousticObservation(acoustics) {
            if (!acoustics || !acoustics.available) return '';
            const pause = Math.round(Number(acoustics.silence_ratio || 0) * 100);
            const pitch = acoustics.median_pitch_hz == null ? '未测到稳定基频' : `基频 ${acoustics.median_pitch_hz} Hz`;
            const pace = acoustics.speaking_rate_chars_per_second == null
                ? ''
                : ` · ${acoustics.speaking_rate_chars_per_second} 字/秒`;
            return `${acoustics.duration_seconds} 秒 · 停顿 ${pause}% · ${pitch}${pace}`;
        }

        function _openVisionPreview(src, label) {
            if (!src) return;
            const old = document.getElementById('vision-preview-overlay');
            if (old) old.remove();
            const overlay = document.createElement('div');
            overlay.id = 'vision-preview-overlay';
            overlay.className = 'vision-preview-overlay';
            overlay.tabIndex = 0;
            const image = document.createElement('img');
            image.src = src;
            image.alt = label || '';
            image.onclick = (event) => event.stopPropagation();
            overlay.appendChild(image);
            overlay.onclick = () => overlay.remove();
            overlay.onkeydown = (event) => {
                if (event.key === 'Escape') overlay.remove();
            };
            document.body.appendChild(overlay);
            overlay.focus();
        }

        function clearVisionAttachment() {
            _visionAttachment = null;
            const strip = document.getElementById('sensory-strip');
            if (strip) strip.classList.remove('show');
            _setStageTraceText('视觉附件已收起。');
        }

        function clearSensoryAttachments() {
            _visionAttachment = null;
            _voiceAttachment = null;
            const strip = document.getElementById('sensory-strip');
            if (strip) strip.classList.remove('show');
        }

        async function _attachVisionImage(file) {
            if (!file) return;
            const dataUrl = await _readFileAsDataUrl(file);
            let analysis = null;
            try {
                const res = await fetch(`${API}/sensory/vision/analyze`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data: dataUrl, name: file.name || 'image' }),
                });
                if (!res.ok) {
                    const detail = await res.json().catch(() => ({}));
                    throw new Error(detail.detail || `视觉分析失败 (${res.status})`);
                }
                analysis = await res.json();
            } catch(e) {
                showSystem(`图片没有进入视觉模型：${e.message || e}`);
                _setStageStatus('她暂时没有看清这张图。', 'idle');
                _setStageTraceText('视觉分析失败，图片没有发送给对话模型。');
                return;
            }
            const vision = analysis && analysis.vision;
            if (!vision || !vision.text) {
                const reason = (vision && (vision.error || vision.reason)) || '视觉模型没有返回观察结果';
                showSystem(`图片没有完成视觉识别：${reason}`);
                _setStageStatus('她暂时没有看清这张图。', 'idle');
                _setStageTraceText('视觉模型不可用，已阻止元数据冒充视觉内容。');
                return;
            }
            const summary = analysis.summary || vision.text;
            _visionAttachment = {
                name: file.name || '未命名图片',
                dataUrl,
                summary,
                analysis,
                proof: analysis.proof || '',
                mime: file.type || '',
            };
            const strip = document.getElementById('sensory-strip');
            const thumb = document.getElementById('sensory-thumb');
            const text = document.getElementById('sensory-text');
            if (thumb) thumb.src = dataUrl;
            if (text) text.textContent = _sensoryStripLabel(_visionAttachment);
            if (strip) strip.classList.add('show');
            _setStageStatus('她看向你递来的画面。', 'seeing');
            _setStageTraceText('视觉输入已进入这一轮对话。');
            lucide.createIcons();
        }

        function _sensoryStripLabel(attachment) {
            const analysis = attachment && attachment.analysis;
            const hasVision = !!(analysis && analysis.vision && analysis.vision.text);
            const hasOcr = !!(analysis && analysis.ocr && analysis.ocr.text);
            const flags = [];
            if (hasVision) flags.push('视觉模型已识别');
            if (hasOcr) flags.push('OCR 已读字');
            if (!flags.length) flags.push('已附上画面');
            return `${attachment.name || '图片'} · ${flags.join(' · ')}`;
        }

        function _firstImageFile(itemsOrFiles) {
            const items = Array.from(itemsOrFiles || []);
            for (const item of items) {
                if (item.kind === 'file') {
                    const file = item.getAsFile();
                    if (file && file.type && file.type.startsWith('image/')) return file;
                } else if (item.type && item.type.startsWith('image/')) {
                    return item;
                }
            }
            return null;
        }

        function handleVisionPaste(event) {
            const file = _firstImageFile(event.clipboardData && event.clipboardData.items);
            if (!file) return;
            event.preventDefault();
            _attachVisionImage(file);
        }

        function handleVisionDragEnter(event) {
            const file = _firstImageFile(event.dataTransfer && event.dataTransfer.items)
                || _firstImageFile(event.dataTransfer && event.dataTransfer.files);
            if (!file) return;
            event.preventDefault();
            document.querySelector('.input-area')?.classList.add('drag-over');
            document.querySelector('.chat-area')?.classList.add('drag-over');
            _setStageStatus('她看见你正把一张图递过来。', 'seeing');
        }

        function handleVisionDragOver(event) {
            const file = _firstImageFile(event.dataTransfer && event.dataTransfer.items)
                || _firstImageFile(event.dataTransfer && event.dataTransfer.files);
            if (!file) return;
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        }

        function handleVisionDragLeave(event) {
            if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget)) return;
            document.querySelector('.input-area')?.classList.remove('drag-over');
            document.querySelector('.chat-area')?.classList.remove('drag-over');
        }

        function handleVisionDrop(event) {
            const file = _firstImageFile(event.dataTransfer && event.dataTransfer.files);
            if (!file) return;
            event.preventDefault();
            document.querySelector('.input-area')?.classList.remove('drag-over');
            document.querySelector('.chat-area')?.classList.remove('drag-over');
            _attachVisionImage(file);
        }

        function _readFileAsDataUrl(file) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
        }

        async function toggleVoiceInput() {
            const btn = document.getElementById('voice-btn');
            if (_mediaRecorder && _mediaRecorder.state === 'recording') {
                _mediaRecorder.stop();
                return;
            }
            if (_voiceRecognition) {
                _voiceRecognition.stop();
                _voiceRecognition = null;
                btn && btn.classList.remove('active');
                return;
            }
            if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    _audioChunks = [];
                    _mediaRecorder = new MediaRecorder(stream);
                    _mediaRecorder.ondataavailable = e => { if (e.data && e.data.size) _audioChunks.push(e.data); };
                    _mediaRecorder.onstart = () => {
                        btn && btn.classList.add('active');
                        _setStageStatus('她在听你说话。', 'listening');
                        _setStageTraceText('再次点麦克风结束录音。');
                    };
                    _mediaRecorder.onstop = async () => {
                        btn && btn.classList.remove('active');
                        stream.getTracks().forEach(track => track.stop());
                        const blob = new Blob(_audioChunks, { type: _mediaRecorder.mimeType || 'audio/webm' });
                        _mediaRecorder = null;
                        const ok = await _analyzeRecordedAudio(blob);
                        if (!ok) _startBrowserSpeechRecognition();
                    };
                    _mediaRecorder.start();
                    return;
                } catch(e) {
                    _startBrowserSpeechRecognition();
                    return;
                }
            }
            _startBrowserSpeechRecognition();
        }

        async function _analyzeRecordedAudio(blob) {
            try {
                const dataUrl = await _blobToDataUrl(blob);
                const res = await fetch(`${API}/sensory/audio/analyze`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ data: dataUrl, name: 'voice.webm' }),
                });
                if (!res.ok) {
                    _setStageTraceText('后端 ASR 请求失败，尝试浏览器识别。');
                    return false;
                }
                const analysis = await res.json();
                const transcript = analysis && analysis.asr && analysis.asr.text;
                if (!transcript) {
                    const asr = (analysis && analysis.asr) || {};
                    const reason = asr.error || asr.reason || (analysis && analysis.summary) || '后端 ASR 没有返回文字';
                    _setStageTraceText(`${reason}，尝试浏览器识别。`);
                    return false;
                }
                _voiceAttachment = {
                    transcript: transcript.trim(),
                    analysis,
                    proof: analysis.proof || '',
                    name: 'voice.webm',
                    mime: blob.type || '',
                    dataUrl,
                };
                const input = document.getElementById('input-box');
                input.value = transcript.trim();
                input.dispatchEvent(new Event('input'));
                const acousticText = _formatAcousticObservation(analysis.acoustics || {});
                _setStageTraceText(
                    acousticText
                        ? `她听见了文字，也测到了声音本身：${acousticText}。`
                        : '后端 ASR 已把语音转成文字。'
                );
                return true;
            } catch(e) {
                _setStageTraceText(`后端 ASR 调用失败：${e.message || e}，尝试浏览器识别。`);
                return false;
            }
        }

        function _blobToDataUrl(blob) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        }

        function _startBrowserSpeechRecognition() {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            const btn = document.getElementById('voice-btn');
            if (!SpeechRecognition) {
                showSystem('没有可用的后端 ASR，这个浏览器也不支持原生语音识别。');
                _setStageStatus('语音输入暂不可用。', 'idle');
                return;
            }
            const rec = new SpeechRecognition();
            rec.lang = 'zh-CN';
            rec.interimResults = true;
            rec.continuous = false;
            _speechHadResult = false;
            _speechHadError = false;
            rec.onstart = () => {
                btn && btn.classList.add('active');
                _setStageStatus('她在听你说话。', 'listening');
                _setStageTraceText('浏览器语音识别已开始。');
            };
            rec.onresult = (event) => {
                let text = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    text += event.results[i][0].transcript;
                }
                const input = document.getElementById('input-box');
                input.value = text.trim();
                input.dispatchEvent(new Event('input'));
                if (text.trim()) {
                    _speechHadResult = true;
                    _voiceAttachment = {
                        transcript: text.trim(),
                        analysis: { asr: { provider: 'browser_speech_recognition', text: text.trim(), available: true } },
                        name: 'browser-speech',
                        mime: '',
                    };
                }
            };
            rec.onend = () => {
                btn && btn.classList.remove('active');
                _voiceRecognition = null;
                if (_speechHadResult) {
                    _setStageTraceText('语音已经落到输入框里。');
                } else if (!_speechHadError) {
                    _setStageTraceText('浏览器语音识别结束，但没有识别到文字。');
                    showSystem('语音识别没有听到文字。');
                }
            };
            rec.onerror = (event) => {
                btn && btn.classList.remove('active');
                _voiceRecognition = null;
                _speechHadError = true;
                const reason = event && event.error ? event.error : 'unknown';
                _setStageTraceText(`浏览器语音识别失败：${reason}`);
                showSystem(`语音识别没有成功：${reason}`);
            };
            _voiceRecognition = rec;
            rec.start();
        }

        function toggleVoiceOutput() {
            _voiceOutputEnabled = !_voiceOutputEnabled;
            localStorage.setItem('yona_voice_output', _voiceOutputEnabled ? '1' : '0');
            const btn = document.getElementById('speak-btn');
            if (btn) btn.classList.toggle('active', _voiceOutputEnabled);
            if (_voiceOutputEnabled) {
                _warmupVoiceOutput(true);
            } else {
                _cancelStreamingSpeech();
                fetch(`${API}/sensory/audio/speech/release`, { method: 'POST' }).catch(() => {});
            }
            _setStageTraceText(_voiceOutputEnabled ? '她之后会把回复念出来。' : '她暂时只用文字回应。');
        }

        async function _warmupVoiceOutput(showStatus = false) {
            if (!_voiceOutputEnabled) return;
            if (_voiceWarmupPromise) return _voiceWarmupPromise;
            if (showStatus) _setStageTraceText('正在准备本地声线，文字聊天不受影响。');
            _voiceWarmupPromise = fetch(`${API}/sensory/audio/speech/warmup`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (showStatus) {
                        _setStageTraceText(
                            data.ready ? '本地声线已经准备好。' : `本地声线准备失败：${data.error || data.reason || '未知原因'}`
                        );
                    }
                    return data;
                })
                .catch(error => {
                    if (showStatus) _setStageTraceText(`本地声线准备失败：${error.message || error}`);
                    return null;
                })
                .finally(() => { _voiceWarmupPromise = null; });
            return _voiceWarmupPromise;
        }

        function _beginStreamingSpeech() {
            _speechGeneration++;
            _speechQueue = [];
            _speechBuffer = '';
            _speechDraining = false;
            if (_speechAudio) {
                _speechAudio.pause();
                _speechAudio = null;
            }
        }

        function _queueStreamingSpeech(token) {
            if (!_voiceOutputEnabled || !token) return;
            _speechBuffer += token;
            while (_speechBuffer) {
                let match = _speechBuffer.match(/^(.{6,80}?[。！？!?；;\n])/s);
                if (!match && _speechBuffer.length >= 48) {
                    match = _speechBuffer.match(/^(.{24,80}?[，,、])/s);
                }
                if (!match) break;
                _speechQueue.push(match[1].trim());
                _speechBuffer = _speechBuffer.slice(match[1].length);
            }
            _drainSpeechQueue(_speechGeneration);
        }

        function _flushStreamingSpeech() {
            if (!_voiceOutputEnabled) return;
            const tail = _speechBuffer.trim();
            if (tail) _speechQueue.push(tail);
            _speechBuffer = '';
            _drainSpeechQueue(_speechGeneration);
        }

        function _cancelStreamingSpeech() {
            _speechGeneration++;
            _speechQueue = [];
            _speechBuffer = '';
            _speechDraining = false;
            if (_speechAudio) {
                _speechAudio.pause();
                _speechAudio = null;
            }
        }

        async function _drainSpeechQueue(generation) {
            if (_speechDraining || !_voiceOutputEnabled) return;
            _speechDraining = true;
            try {
                while (_speechQueue.length && generation === _speechGeneration && _voiceOutputEnabled) {
                    const segment = _speechQueue.shift();
                    if (segment) await _speakAssistant(segment, generation);
                }
            } finally {
                if (generation === _speechGeneration) _speechDraining = false;
            }
        }

        async function _speakAssistant(text, generation = _speechGeneration) {
            if (!_voiceOutputEnabled || !text || generation !== _speechGeneration) return;
            try {
                const res = await fetch(`${API}/sensory/audio/speech`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.data_url) {
                        const audio = new Audio(data.data_url);
                        _speechAudio = audio;
                        await new Promise(resolve => {
                            audio.onended = resolve;
                            audio.onerror = resolve;
                            audio.play().catch(() => {
                                _setStageTraceText('本地语音已经生成，但浏览器阻止了自动播放。');
                                resolve();
                            });
                        });
                        if (_speechAudio === audio) _speechAudio = null;
                        return;
                    }
                    _setStageTraceText(`本地语音没有生成：${data.error || data.reason || '未知原因'}`);
                    return;
                }
                _setStageTraceText(`本地语音请求失败：HTTP ${res.status}`);
            } catch(e) {
                _setStageTraceText(`本地语音请求失败：${e.message || e}`);
            }
        }

        function _objectTypeLabel(type) {
            return {
                note: '便签',
                memory_card: '记忆',
                clipping: '剪报',
                document: '文档',
            }[type] || '物件';
        }

        function openStageDocument() {
            const metadata = (_stageObject && _stageObject.metadata) || {};
            if (!metadata.document_id) {
                _setStageTraceText('这个物件没有可打开的文档。');
                return;
            }
            window.open(`${API}/workspace/documents/${metadata.document_id}`, '_blank', 'noopener');
        }

        async function deleteObject(id) {
            try {
                await fetch(`${API}/objects/${id}`, { method: 'DELETE' });
                await _refreshObjects();
            } catch(e) {}
        }

        function continueStageObject() {
            if (!_stageObject) return;
            const input = document.getElementById('input-box');
            input.value = `继续围绕「${_stageObject.title || '这件事'}」往下做，不要复述，直接推进一个新动作。`;
            input.focus();
            input.dispatchEvent(new Event('input'));
            _setStageStatus('你把桌面上的东西递回给她了。', 'ready');
        }

        function openStageCorrection() {
            if (!_stageObject) return;
            const panel = document.getElementById('stage-correction');
            const title = document.getElementById('stage-correction-title');
            const content = document.getElementById('stage-correction-content');
            if (title) title.value = _stageObject.title || '';
            if (content) content.value = _stageObject.content || '';
            panel && panel.classList.add('open');
            content && content.focus();
            _setStageStatus('这次更正会保留原来的版本。', 'ready');
        }

        function closeStageCorrection() {
            document.getElementById('stage-correction')?.classList.remove('open');
        }

        async function saveStageCorrection() {
            if (!_stageObject) return;
            const title = document.getElementById('stage-correction-title')?.value.trim();
            const content = document.getElementById('stage-correction-content')?.value.trim();
            if (!title || !content) return;
            try {
                const res = await fetch(`${API}/objects/${_stageObject.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title,
                        content,
                        reason: 'user_correction',
                        confidence: 'high',
                    }),
                });
                if (!res.ok) throw new Error('更正没有保存成功');
                const updated = await res.json();
                _renderStageObject(updated, `这条记录已由你更正为第 ${updated.revision || 2} 版。`);
                await _refreshObjects();
            } catch(e) {
                _setStageTraceText(e.message || '更正没有保存成功');
            }
        }

        async function showStageSource() {
            if (!_stageObject) return;
            try {
                const res = await fetch(`${API}/objects/${_stageObject.id}/source`);
                if (!res.ok) throw new Error('没有找到来源');
                const data = await res.json();
                const messages = data.messages || [];
                const revisions = data.revisions || [];
                if (!messages.length && !revisions.length) {
                    _setStageTraceText('这是手动或自主生成的物件，没有绑定聊天轮次。');
                    return;
                }
                const parts = messages.map(m => `${m.role === 'user' ? '你' : 'Yona'}：${(m.content || '').slice(0, 180)}`);
                if (revisions.length) parts.push(`保留了 ${revisions.length} 个旧版本`);
                _setStageTraceText(parts.join('\n'));
            } catch(e) {
                _setStageTraceText(e.message || '来源读取失败');
            }
        }

        async function deleteStageObject() {
            if (!_stageObject) return;
            const id = _stageObject.id;
            await deleteObject(id);
            _setStageTraceText('你把刚才那件东西从桌面上收走了。');
        }

        async function pulseAutonomy() {
            _setStageStatus('她自己醒来了一下。', 'thinking');
            _setStageTraceText('正在让她跑一次自主脉冲。');
            try {
                const before = new Set(_knownObjectIds);
                const res = await fetch(`${API}/autonomy/pulse`, { method: 'POST' });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || '脉冲失败');
                }
                const data = await res.json();
                _setStageStatus('她刚刚自己动了一轮。', 'done');
                _setStageTraceText(`自主脉冲完成，用时 ${data.elapsed_ms || 0}ms。`);
                await _refreshWorkspace();
                _watchForNewObject(before);
            } catch(e) {
                _setStageStatus('自主脉冲没有成功。', 'idle');
                _setStageTraceText(e.message || '脉冲失败');
            }
        }
        setInterval(_refreshObjects, 45000);
