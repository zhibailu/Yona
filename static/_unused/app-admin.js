        // ========== 操作菜单 ==========
        function toggleActionMenu() {
            const menu = document.getElementById('action-menu');
            menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
        }
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('action-menu');
            const btn = document.getElementById('action-menu-btn');
            if (menu && !menu.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                menu.style.display = 'none';
            }
        });

        async function showStats() {
            toggleActionMenu();
            try {
                const res = await fetch(`${API}/admin/stats?session_id=${currentSessionId || ''}`);
                const data = await res.json();
                const db = data.db;
                const vec = data.vector;
                const text = `数据库统计：\n总消息: ${db.total_messages} | User: ${db.user_messages} | Assistant: ${db.assistant_messages}\n空输出: ${db.empty_assistant} | 对话轮数: ${db.conversation_rounds}\n\n向量库统计：\n向量总数: ${vec.vector_count} | 有效: ${vec.effective_count} | 掩码: ${vec.mask_count}\n\n一致性: ${data.consistent ? '✅ 一致' : '⚠️ 不一致，建议重建向量库'}`;
                alert(text);
            } catch (e) {
                showSystem('获取统计失败');
            }
        }

        async function rebuildVector() {
            toggleActionMenu();
            if (!confirm('将从数据库重建当前会话的向量库，是否继续？')) return;
            try {
                const res = await fetch(`${API}/admin/rebuild-vector`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentSessionId })
                });
                const data = await res.json();
                setStatus(`向量库重建已启动: ${data.count} 条`);
            } catch (e) {
                showSystem('重建向量库失败');
            }
        }

        async function exportData() {
            toggleActionMenu();
            try {
                const res = await fetch(`${API}/admin/export?session_id=${currentSessionId || ''}`);
                const data = await res.json();
                const blob = new Blob([JSON.stringify(data.data, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `yona_export_${currentSessionId || 'all'}.json`;
                a.click();
                URL.revokeObjectURL(url);
                setStatus(`已导出 ${data.count} 条消息`);
            } catch (e) {
                showSystem('导出失败');
            }
        }

        async function cleanEmpty() {
            toggleActionMenu();
            if (!confirm('将清理当前会话中所有空输出的 assistant 及其对应的 user 消息，是否继续？')) return;
            try {
                const res = await fetch(`${API}/admin/clean-empty`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentSessionId })
                });
                const data = await res.json();
                await switchSession(currentSessionId);
                setStatus(`已清理 ${data.count} 条异常记录`);
            } catch (e) {
                showSystem('清理失败');
            }
        }
