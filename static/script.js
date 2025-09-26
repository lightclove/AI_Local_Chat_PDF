/**
 * Модуль: Клиентский UI-скрипт (чаты, сообщения, загрузки)
 *
 * Зачем:
 * - Дать пользователю удобный интерфейс для выбора модели, управления чатами и общения с ИИ.
 * - Передавать базовые клиентские логи на бэкенд для корреляции инцидентов.
 *
 * Почему именно так:
 * - Минимальный vanilla JS без фреймворков снижает зависимость и облегчает внедрение.
 * - Чёткое разделение: логгер, инициализация UI, API-вызовы, обработчики событий.
 *
 * Когда использовать:
 * - Всегда загружается как часть `static/index.html` для работы веб-клиента на одной странице.
 *
 * Основные обязанности:
 * - Управление списком чатов, отображение сообщений, отправка запросов к /api/*.
 * - Синхронизация выбора модели с бэкендом.
 * - Буферизация и отправка фронтенд-логов на /api/logs.
 *
 */
let currentChatId = null;
let currentUserId = (function(){
    try {
        const k = 'chat_user_id';
        // Сохраняем стабильный анонимный идентификатор пользователя в localStorage,
        // чтобы бэкенд мог группировать чаты и логи по пользователю без авторизации
        let v = localStorage.getItem(k);
        if (!v) {
            v = 'user-' + Math.random().toString(36).substr(2, 9);
            localStorage.setItem(k, v);
        }
        return v;
    } catch (_) {
        // Fallback, если localStorage недоступен (например, приватный режим)
        return 'user-' + Math.random().toString(36).substr(2, 9);
    }
})();
let isSending = false;

// Продвинутая система фронтенд-логгирования с comprehensive захватом всех событий
const FrontendLogger = (() => {
    const buffer = [];
    const localStorage = [];
    let flushTimer = null;
    let retryCount = 0;
    const MAX_BUFFER_SIZE = 100;
    const MAX_RETRY_COUNT = 3;
    const FLUSH_INTERVAL = 2000;
    const RETRY_INTERVALS = [1000, 3000, 10000]; // прогрессивные интервалы retry

    // Собираем расширенную контекстную информацию
    function getContext(additionalContext = {}) {
        return {
            // Базовая информация
            href: location.href,
            referrer: document.referrer || null,
            userAgent: navigator.userAgent,
            language: navigator.language,
            cookieEnabled: navigator.cookieEnabled,
            onLine: navigator.onLine,

            // Информация о экране
            screenWidth: screen.width,
            screenHeight: screen.height,
            viewportWidth: window.innerWidth,
            viewportHeight: window.innerHeight,
            colorDepth: screen.colorDepth,
            pixelRatio: window.devicePixelRatio || 1,

            // Информация о браузере
            appName: navigator.appName,
            appVersion: navigator.appVersion,
            platform: navigator.platform,
            vendor: navigator.vendor,
            product: navigator.product,

            // Временные метки
            timestamp: Date.now(),
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,

            // Состояние приложения
            model: localStorage.getItem('lm_model_id') || null,
            currentChat: currentChatId,
            userId: currentUserId,

            // Дополнительная информация
            ...additionalContext
        };
    }

    // Улучшенная функция логирования с категоризацией
    function push(level, message, context = {}, category = 'general') {
        try {
            const logEntry = {
                level: level.toUpperCase(),
                message: String(message),
                timestamp: new Date().toISOString(),
                category: category,
                context: getContext(context),
                user_id: currentUserId,
                session_id: sessionStorage.getItem('session_id') || generateSessionId(),
                sequence: buffer.length + 1
            };

            // Добавляем в буфер
            buffer.push(logEntry);

            // Сохраняем локально для оффлайн режима
            try {
                localStorage.push(logEntry);
                if (localStorage.length > MAX_BUFFER_SIZE) {
                    localStorage.splice(0, localStorage.length - MAX_BUFFER_SIZE);
                }
                // Сохраняем в localStorage
                const localLogs = JSON.parse(localStorage.getItem('frontend_logs') || '[]');
                localLogs.push(logEntry);
                if (localLogs.length > MAX_BUFFER_SIZE * 2) {
                    localLogs.splice(0, localLogs.length - MAX_BUFFER_SIZE);
                }
                localStorage.setItem('frontend_logs', JSON.stringify(localLogs));
            } catch (storageError) {
                console.warn('Failed to save logs locally:', storageError);
            }

            // Немедленная отправка критических ошибок
            if (level.toUpperCase() === 'ERROR' || category === 'critical') {
                immediateFlush();
            } else {
            scheduleFlush();
            }

        } catch (e) {
            // last-resort: пишем в консоль
            console.error('FrontendLogger push error:', e);
        }
    }

    // Генерация session_id
    function generateSessionId() {
        const sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        sessionStorage.setItem('session_id', sessionId);
        return sessionId;
    }

    // Немедленная отправка критических логов
    async function immediateFlush() {
        if (buffer.length === 0) return;

        const payload = buffer.splice(0, buffer.length);
        await sendLogs(payload, true);
    }

    // Обычная отправка с retry логикой
    async function flush() {
        if (buffer.length === 0) {
            // Отправляем локальные логи если нет новых
            const localLogs = JSON.parse(localStorage.getItem('frontend_logs') || '[]');
            if (localLogs.length > 0) {
                await sendLogs(localLogs.slice(), false);
                localStorage.setItem('frontend_logs', '[]');
            }
            return;
        }

        const payload = buffer.splice(0, buffer.length);
        await sendLogs(payload, false);
    }

    // Отправка логов с retry логикой
    async function sendLogs(logs, isCritical = false) {
        if (logs.length === 0) return;

        try {
            const response = await fetch('/api/logs', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Log-Source': 'frontend',
                    'X-Log-Critical': isCritical ? 'true' : 'false'
                },
                body: JSON.stringify(logs)
            });

            if (response.ok) {
                retryCount = 0;
                FrontendLogger.info(`Successfully sent ${logs.length} log entries`);
            } else {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
        } catch (e) {
            console.error('Failed to send logs:', e);

            // Сохраняем обратно в локальное хранилище
            try {
                const existingLogs = JSON.parse(localStorage.getItem('frontend_logs') || '[]');
                const allLogs = [...logs, ...existingLogs];
                if (allLogs.length > MAX_BUFFER_SIZE * 3) {
                    allLogs.splice(0, allLogs.length - MAX_BUFFER_SIZE * 2);
                }
                localStorage.setItem('frontend_logs', JSON.stringify(allLogs));
            } catch (storageError) {
                console.error('Failed to save logs locally:', storageError);
            }

            // Retry логика
            if (retryCount < MAX_RETRY_COUNT) {
                retryCount++;
                const delay = RETRY_INTERVALS[retryCount - 1] || 10000;

                setTimeout(async () => {
                    await sendLogs(logs, isCritical);
                }, delay);
            } else {
                console.error('Max retry count reached, giving up on logs:', logs);
                retryCount = 0;
            }
        }
    }

    // Планирование отправки
    function scheduleFlush() {
        if (flushTimer) return;

        flushTimer = setTimeout(async () => {
            flushTimer = null;
            await flush();
        }, FLUSH_INTERVAL);
    }

    // Глобальные обработчики ошибок
    window.addEventListener('error', (e) => {
        push('ERROR', e.message || 'WindowError', {
            filename: e.filename,
            lineno: e.lineno,
            colno: e.colno,
            error: e.error?.toString(),
            stack: e.error?.stack
        }, 'javascript');
    });

    // Необработанные промисы
    window.addEventListener('unhandledrejection', (e) => {
        push('ERROR', 'Unhandled promise rejection', {
            reason: e.reason?.toString(),
            stack: e.reason?.stack,
            promise: e.promise?.toString()
        }, 'async');
    });

    // Ошибки ресурсов
    window.addEventListener('error', (e) => {
        if (e.target !== window) {
            push('ERROR', `Resource failed to load: ${e.target.src || e.target.href}`, {
                tagName: e.target.tagName,
                src: e.target.src,
                href: e.target.href,
                type: e.target.type
            }, 'resource');
        }
    }, true);

    // Сетевые ошибки
    const originalFetch = window.fetch;
    window.fetch = async function(...args) {
        const startTime = performance.now();
        const url = args[0] instanceof Request ? args[0].url : args[0];

        try {
            const response = await originalFetch(...args);
            const endTime = performance.now();

            if (!response.ok) {
                push('WARNING', `HTTP ${response.status} ${response.statusText}`, {
                    url: url,
                    method: args[0] instanceof Request ? args[0].method : 'GET',
                    status: response.status,
                    statusText: response.statusText,
                    duration: endTime - startTime
                }, 'network');
            } else {
                push('INFO', `HTTP ${response.status} ${response.statusText}`, {
                    url: url,
                    method: args[0] instanceof Request ? args[0].method : 'GET',
                    status: response.status,
                    duration: endTime - startTime
                }, 'network');
            }

            return response;
        } catch (error) {
            const endTime = performance.now();
            push('ERROR', `Network request failed: ${error.message}`, {
                url: url,
                method: args[0] instanceof Request ? args[0].method : 'GET',
                duration: endTime - startTime,
                error: error.message
            }, 'network');
            throw error;
        }
    };

    // Мониторинг производительности
    if ('PerformanceObserver' in window) {
        // Мониторинг LCP (Largest Contentful Paint)
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                push('INFO', `Performance: LCP ${entry.startTime.toFixed(2)}ms`, {
                    entryType: entry.entryType,
                    startTime: entry.startTime,
                    value: entry.value
                }, 'performance');
            }
        }).observe({ entryTypes: ['largest-contentful-paint'] });

        // Мониторинг FID (First Input Delay)
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                push('INFO', `Performance: FID ${entry.processingStart.toFixed(2)}ms`, {
                    entryType: entry.entryType,
                    processingStart: entry.processingStart,
                    inputDelay: entry.processingStart - entry.startTime
                }, 'performance');
            }
        }).observe({ entryTypes: ['first-input'] });

        // Мониторинг CLS (Cumulative Layout Shift)
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                if (entry.value > 0.1) { // Только значимые сдвиги
                    push('WARNING', `Performance: CLS ${entry.value.toFixed(3)}`, {
                        entryType: entry.entryType,
                        value: entry.value,
                        sources: entry.sources?.map(s => s.node?.tagName).join(',')
                    }, 'performance');
                }
            }
        }).observe({ entryTypes: ['layout-shift'] });
    }

    // Мониторинг памяти
    if ('memory' in performance) {
        setInterval(() => {
            const memory = performance.memory;
            const memoryUsage = {
                used: Math.round(memory.usedJSHeapSize / 1024 / 1024),
                total: Math.round(memory.totalJSHeapSize / 1024 / 1024),
                limit: Math.round(memory.jsHeapSizeLimit / 1024 / 1024)
            };

            if (memoryUsage.used > memoryUsage.limit * 0.8) {
                push('WARNING', `Memory usage high: ${memoryUsage.used}MB/${memoryUsage.limit}MB`, memoryUsage, 'memory');
            } else {
                push('DEBUG', `Memory usage: ${memoryUsage.used}MB/${memoryUsage.limit}MB`, memoryUsage, 'memory');
            }
        }, 30000); // Каждые 30 секунд
    }

    // Мониторинг пользовательских действий
    document.addEventListener('click', (e) => {
        if (e.target.matches('button, input, select, textarea, a')) {
            push('DEBUG', `User interaction: ${e.target.tagName}.${e.target.className || ''}`, {
                tagName: e.target.tagName,
                className: e.target.className,
                id: e.target.id,
                text: e.target.textContent?.substring(0, 50) || e.target.value?.substring(0, 50)
            }, 'user-action');
        }
    });

    // Мониторинг видимости страницы
    document.addEventListener('visibilitychange', () => {
        push('INFO', `Page visibility changed: ${document.visibilityState}`, {
            visibilityState: document.visibilityState
        }, 'lifecycle');

        if (document.visibilityState === 'visible') {
            // Отправляем накопленные логи при возвращении на страницу
            flush();
        }
    });

    // Мониторинг соединения
    window.addEventListener('online', () => {
        push('INFO', 'Network connection restored', { online: true }, 'network');
        flush(); // Отправляем накопленные логи
    });

    window.addEventListener('offline', () => {
        push('WARNING', 'Network connection lost', { online: false }, 'network');
    });

    // Мониторинг Service Worker
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.addEventListener('error', (e) => {
            push('ERROR', 'Service Worker error', {
                filename: e.filename,
                lineno: e.lineno,
                colno: e.colno,
                message: e.message
            }, 'service-worker');
        });

        navigator.serviceWorker.addEventListener('message', (e) => {
            if (e.data && e.data.type === 'LOG') {
                push(e.data.level || 'INFO', e.data.message, e.data.context, e.data.category || 'service-worker');
            }
        });
    }

    // Инициализация session_id
    generateSessionId();

    return {
        debug: (msg, ctx = {}, cat = 'general') => push('DEBUG', msg, ctx, cat),
        info: (msg, ctx = {}, cat = 'general') => push('INFO', msg, ctx, cat),
        warn: (msg, ctx = {}, cat = 'general') => push('WARNING', msg, ctx, cat),
        error: (msg, ctx = {}, cat = 'general') => push('ERROR', msg, ctx, cat),

        // Специализированные методы
        performance: (msg, ctx = {}) => push('INFO', msg, ctx, 'performance'),
        network: (msg, ctx = {}) => push('INFO', msg, ctx, 'network'),
        userAction: (msg, ctx = {}) => push('DEBUG', msg, ctx, 'user-action'),
        lifecycle: (msg, ctx = {}) => push('INFO', msg, ctx, 'lifecycle'),
        memory: (msg, ctx = {}) => push('INFO', msg, ctx, 'memory'),

        // Утилиты
        flush: flush,
        getBufferSize: () => buffer.length,
        getLocalStorageSize: () => JSON.parse(localStorage.getItem('frontend_logs') || '[]').length,
        clearBuffer: () => { buffer.length = 0; },
        clearLocalStorage: () => localStorage.setItem('frontend_logs', '[]'),

        // Отладочная информация
        getStats: () => ({
            bufferSize: buffer.length,
            localStorageSize: JSON.parse(localStorage.getItem('frontend_logs') || '[]').length,
            retryCount,
            sessionId: sessionStorage.getItem('session_id')
        })
    };
})();

// Инициализация UI и обработчики событий
document.addEventListener('DOMContentLoaded', function() {
    FrontendLogger.info('Загрузка интерфейса (DOMContentLoaded)', {
        readyState: document.readyState,
        cookieCount: document.cookie.split(';').length
    }, 'lifecycle');

    // Мониторинг загрузки ресурсов
    const resourceObserver = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
            if (entry.duration > 1000) { // Ресурсы, загружающиеся дольше секунды
                FrontendLogger.warn(`Slow resource load: ${entry.name}`, {
                    name: entry.name,
                    duration: entry.duration,
                    entryType: entry.entryType
                }, 'performance');
            }
        }
    });

    if ('PerformanceObserver' in window) {
        resourceObserver.observe({ entryTypes: ['resource'] });
    }

    initModelSelect();
    loadChats();

    // Кнопка создания нового чата
    const newBtn = document.getElementById('new-chat-btn');
    if (newBtn) {
        newBtn.addEventListener('click', async () => {
            FrontendLogger.userAction('Нажатие кнопки "Новый чат"');

            const created = await createChat('Новый чат');
            if (created && created.chat_id) {
                FrontendLogger.info('Новый чат выбран после создания', {
                    chatId: created.chat_id,
                    title: created.title
                }, 'user-action');

                await loadChats();
                const el = document.querySelector(`.chat-item[data-chat-id="${created.chat_id}"]`);
                if (el) el.click();
            } else {
                FrontendLogger.error('Не удалось создать новый чат');
            }
        });
    }

    // Синхронизация верхней полосы прокрутки с горизонтальным списком чатов с API /api/users/{currentUserId}/chats
    const topScroll = document.getElementById('chat-list-scrollbar-top');
    const chatListEl = document.getElementById('chat-list');
    let syncing = false;
    function updateChatListScrollbars() {
        try {
            const spacer = topScroll && topScroll.querySelector('.spacer');
            if (spacer && chatListEl) {
                spacer.style.width = (chatListEl.scrollWidth || 0) + 'px';
            }
        } catch (_) {}
    }
    if (topScroll && chatListEl) {
        topScroll.addEventListener('scroll', () => {
            if (syncing) return; syncing = true;
            chatListEl.scrollLeft = topScroll.scrollLeft;
            syncing = false;
        });
        chatListEl.addEventListener('scroll', () => {
            if (syncing) return; syncing = true;
            topScroll.scrollLeft = chatListEl.scrollLeft;
            syncing = false;
        });
        window.addEventListener('resize', updateChatListScrollbars);
    }

    // Обработчик выбора PDF для загрузки
    document.getElementById('pdf-upload').addEventListener('change', function(e) {
        const file = e.target.files[0];
        if (file) {
            FrontendLogger.info('PDF выбран', { name: file.name, size: file.size });
            uploadPDF(file);
        }
    });

    // Поле ввода сообщения и поведение кнопки отправки с API /api/chat/ask и сохранение истории переписки
    const input = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-button');
    input.addEventListener('keydown', function(e) {
        // Enter без Shift — отправка; Shift+Enter — перенос строки
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    input.addEventListener('input', () => {
        // Кнопка доступна только если есть текст и выбран чат
        sendBtn.disabled = !input.value.trim() || !currentChatId;
    });
    sendBtn.addEventListener('click', () => {
        FrontendLogger.userAction('Нажатие кнопки отправки сообщения', {
            hasMessage: !!input.value.trim(),
            currentChatId
        });
        sendMessage();
    });

    // Перед уходом со страницы пытаемся сбросить буфер логов
    window.addEventListener('beforeunload', () => {
        FrontendLogger.flush();
    });
    // Экспорт в window для повторных вызовов
    window.updateChatListScrollbars = updateChatListScrollbars;
});
// Инициализация выбора модели из списка моделей с API /api/models и сохранение в localStorage
async function initModelSelect() {
    try {
        const sel = document.getElementById('model-select');
        if (!sel) return;
        sel.disabled = true;
        sel.innerHTML = '<option>Загрузка моделей...</option>';
        const res = await fetch('/api/models');
        const data = await res.json();
        const models = (data && data.models) || [];
        sel.innerHTML = '';
        // Заполняем список моделей значениями id/name (универсально под разные ответы)
        models.forEach(m => {
            const id = m.id || m.name || m.model || '';
            if (!id) return;
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = id;
            sel.appendChild(opt);
        });
        // Восстанавливаем ранее выбранную модель из localStorage и выбираем ее
        const saved = localStorage.getItem('lm_model_id');
        if (saved) {
            sel.value = saved;
            await selectModel(saved);
        } else if (sel.options.length > 0) {
            await selectModel(sel.value);
        }
        sel.disabled = false;
        sel.addEventListener('change', async () => {
            await selectModel(sel.value);
        });
    } catch (e) {
        FrontendLogger.error('Сбой инициализации списка моделей', { error: String(e) });
    }
}
// Выбор модели из списка моделей с API /api/models/select и сохранение в localStorage
async function selectModel(modelId) {
    try {
        if (!modelId) return;
        const res = await fetch('/api/models/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: modelId })
        });
        if (res.ok) {
            // Сохраняем выбранную модель для будущих сессий
            localStorage.setItem('lm_model_id', modelId);
            FrontendLogger.info('Модель выбрана', { modelId });
        } else {
            const t = await res.text();
            FrontendLogger.error('Не удалось выбрать модель', { status: res.status, t });
        }
    } catch (e) {
        FrontendLogger.error('Исключение при выборе модели', { error: String(e) });
    }
}
// Загрузка списка чатов с API /api/users/{currentUserId}/chats и отображение в UI
async function loadChats() {
    const startTime = performance.now();
    try {
        FrontendLogger.debug('Загрузка списка чатов', {
            userId: currentUserId,
            currentChat: currentChatId
        }, 'user-action');

        const response = await fetch(`/api/users/${currentUserId}/chats`);
        const endTime = performance.now();

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const chats = await response.json();

        FrontendLogger.info('Чаты загружены успешно', {
            count: (chats && chats.length) || 0,
            duration: endTime - startTime,
            userId: currentUserId
        }, 'user-action');
        const chatList = document.getElementById('chat-list');
        chatList.innerHTML = '';

        if (!Array.isArray(chats) || chats.length === 0) {
            chatList.innerHTML = '<div class="loading">Чатов пока нет</div>';
            if (window.updateChatListScrollbars) window.updateChatListScrollbars();
            return;
        }

        // Рендер списка чатов: заголовок и кнопки действий (переименовать/удалить)
        chats.forEach(chat => {
            const chatItem = document.createElement('div');
            chatItem.className = 'chat-item';
            chatItem.dataset.chatId = chat.chat_id;

            const titleSpan = document.createElement('span');
            titleSpan.className = 'title';
            titleSpan.textContent = chat.title;
            chatItem.appendChild(titleSpan);

            const actions = document.createElement('div');
            actions.className = 'actions';

            const renameBtn = document.createElement('button');
            renameBtn.className = 'icon-btn';
            renameBtn.title = 'Переименовать';
            renameBtn.textContent = '✎';
            renameBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                FrontendLogger.userAction('Нажатие кнопки переименования чата', {
                    chatId: chat.chat_id,
                    currentTitle: titleSpan.textContent
                });

                const current = titleSpan.textContent || '';
                const next = prompt('Введите новое название', current);
                if (!next || next.trim() === '' || next.trim() === current) return;

                const ok = await renameChat(chat.chat_id, next.trim());
                if (ok) {
                    titleSpan.textContent = next.trim();
                    FrontendLogger.info('Чат переименован', {
                        chatId: chat.chat_id,
                        oldTitle: current,
                        newTitle: next.trim()
                    }, 'user-action');
                }
            });

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'icon-btn';
            deleteBtn.title = 'Удалить';
            deleteBtn.textContent = '🗑';
            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                FrontendLogger.userAction('Нажатие кнопки удаления чата', {
                    chatId: chat.chat_id,
                    currentChatId
                });

                if (!confirm('Удалить этот чат?')) {
                    FrontendLogger.debug('Отмена удаления чата', { chatId: chat.chat_id });
                    return;
                }

                const ok = await deleteChat(chat.chat_id);
                if (ok) {
                    FrontendLogger.info('Чат удален', {
                        chatId: chat.chat_id,
                        wasCurrentChat: currentChatId === chat.chat_id
                    }, 'user-action');

                    if (currentChatId === chat.chat_id) currentChatId = null;
                    await loadChats();
                }
            });

            actions.appendChild(renameBtn);
            actions.appendChild(deleteBtn);
            chatItem.appendChild(actions);

            chatItem.onclick = () => selectChat(chat.chat_id, chatItem);
            chatList.appendChild(chatItem);
        });

        if (window.updateChatListScrollbars) window.updateChatListScrollbars();

        // Автовыбор первого чата, если ничего не выбрано
        if (!currentChatId && chats[0]) {
            const firstItem = document.querySelector('.chat-item');
            await selectChat(chats[0].chat_id, firstItem);
        }
    } catch (error) {
        console.error('Ошибка загрузки чатов:', error);
        FrontendLogger.error('Ошибка загрузки чатов', { error: String(error) });
    }
}

async function selectChat(chatId, element) {
    const previousChatId = currentChatId;
    currentChatId = chatId;

    FrontendLogger.userAction('Выбор чата', {
        chatId,
        previousChatId,
        isFirstSelection: !previousChatId
    }, 'user-action');

    // Обновляем подсветку выбранного чата
    document.querySelectorAll('.chat-item').forEach(item => {
        item.classList.remove('active');
    });
    element.classList.add('active');

    // Делаем поле ввода доступным для набора
    const input = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-button');
    input.disabled = false;
    sendBtn.disabled = !input.value.trim() || !currentChatId;

    // Загружаем историю сообщений выбранного чата
    try {
        FrontendLogger.debug('Загрузка сообщений', { chatId });
        const response = await fetch(`/api/chats/${chatId}/messages`);
        const messages = await response.json();

        const chatContainer = document.getElementById('chat-container');
        chatContainer.innerHTML = '';
        // Рендер сообщений в чате с учетом времени отправки и мыслительного процесса ИИ
        messages.forEach(message => {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${message.role}-message`;
            messageDiv.textContent = message.content;
            if (message.timestamp) {
                const msgTime = new Date(message.timestamp).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
                const timeElem = document.createElement('span');
                timeElem.classList.add('message-time');
                timeElem.textContent = msgTime;
                messageDiv.appendChild(timeElem);
            }
            if (message.role === 'assistant' && message.thinking_time) {
                const thinkElem = document.createElement('span');
                thinkElem.classList.add('thinking-time');
                thinkElem.textContent = `ИИ думал ${message.thinking_time} сек.`;
                messageDiv.appendChild(thinkElem);
            }
            chatContainer.appendChild(messageDiv);
        });

        // Ensure auto-scroll after loading messages
        // Прокручиваем к последнему сообщению
        if (messages.length > 0) {
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

    } catch (error) {
        console.error('Ошибка загрузки сообщений:', error);
        FrontendLogger.error('Ошибка загрузки сообщений', { error: String(error), chatId });
        // Оставляем поле ввода активным даже если история не загрузилась
        input.disabled = false;
        sendBtn.disabled = !input.value.trim() || !currentChatId;
    }
}
// Отправка сообщения в чат
async function sendMessage() {
    const input = document.getElementById('message-input');
    const message = input.value.trim();

    if (!message || !currentChatId) {
        FrontendLogger.debug('Попытка отправки пустого сообщения', {
            hasMessage: !!message,
            hasChatId: !!currentChatId
        }, 'user-action');
        return;
    }

    if (isSending) {
        FrontendLogger.warn('Попытка отправки во время отправки', {
            currentChatId,
            messageLength: message.length
        }, 'user-action');
        return; // защита от двойных кликов
    }

    isSending = true;
    const startTime = performance.now();

    FrontendLogger.info('Отправка сообщения', {
        chatId: currentChatId,
        messageLength: message.length,
        model: localStorage.getItem('lm_model_id')
    }, 'user-action');

    // Добавляем сообщение пользователя в UI сразу (оптимистичный апдейт)
    const chatContainer = document.getElementById('chat-container');
    const userMessageDiv = document.createElement('div');
    userMessageDiv.className = 'message user-message';
    userMessageDiv.textContent = message;
    chatContainer.appendChild(userMessageDiv);

    // Очищаем поле ввода
    input.value = '';

    // Вставляем индикатор «ИИ думает»
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'loading';
    loadingDiv.textContent = 'ИИ думает…';
    chatContainer.appendChild(loadingDiv);

    try {
        // Отправляем запрос на бэкенд к /api/chat/ask с запросом к ИИ с учетом RAG-контекста и сохранения истории переписки
        const response = await fetch('/api/chat/ask', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                chat_id: currentChatId,
                message: message,
                user_id: currentUserId
            })
        });

        const result = await response.json();
        FrontendLogger.info('Сообщение отправлено, ответ получен', { ok: response.ok });

        // Убираем индикатор загрузки
        chatContainer.removeChild(loadingDiv);

        // Выводим ответ ассистента
        const assistantMessageDiv = document.createElement('div');
        assistantMessageDiv.className = 'message assistant-message';
        assistantMessageDiv.textContent = result.response;

        // Добавляем время отправки ИИ
        const assistantTimestamp = new Date().toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        const assistantTimeElem = document.createElement('span');
        assistantTimeElem.classList.add('message-time');
        assistantTimeElem.textContent = assistantTimestamp;
        assistantMessageDiv.appendChild(assistantTimeElem);

        // Время мыслительного процесса ИИ
        if (result.thinking_time) {
            const thinkElem = document.createElement('span');
            thinkElem.classList.add('thinking-time');
            thinkElem.textContent = `Подумал ${result.thinking_time} сек.`;
            assistantMessageDiv.appendChild(thinkElem);
        }
        //
        chatContainer.appendChild(assistantMessageDiv);
        chatContainer.scrollTop = chatContainer.scrollHeight;
        const timestamp = new Date().toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        const timeElem = document.createElement('span');
        timeElem.classList.add('message-time');
        timeElem.textContent = timestamp;
        assistantMessageDiv.appendChild(timeElem);

    } catch (error) {
        console.error('Ошибка отправки сообщения:', error);
        FrontendLogger.error('Ошибка отправки сообщения', { error: String(error) });
        chatContainer.removeChild(loadingDiv);

        const errorDiv = document.createElement('div');
        errorDiv.className = 'message assistant-message';
        errorDiv.textContent = 'Ошибка: не удалось получить ответ от ИИ';
        chatContainer.appendChild(errorDiv);
    } finally {
        isSending = false;
        const sendBtn = document.getElementById('send-button');
        sendBtn.disabled = !input.value.trim() || !currentChatId;
    }
}

async function uploadPDF(file) {
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/documents/upload', {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            alert('PDF успешно загружен!');
            loadChats(); // обновляем список чатов, хотя документы не привязаны к чату напрямую
        } else {
            const error = await response.json();
            alert('Не удалось загрузить PDF: ' + (error.detail || 'Неизвестная ошибка'));
        }
    } catch (error) {
        console.error('Ошибка загрузки PDF:', error);
        FrontendLogger.error('Ошибка загрузки PDF', { error: String(error) });
        alert('Ошибка загрузки PDF');
    }
}

async function createChat(title) {
    const startTime = performance.now();
    try {
        FrontendLogger.info('Создание нового чата', {
            title,
            userId: currentUserId,
            model: localStorage.getItem('lm_model_id')
        }, 'user-action');

        const res = await fetch(`/api/users/${currentUserId}/chats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        const endTime = performance.now();

        if (!res.ok) {
            const err = await res.text();
            FrontendLogger.error('Создание чата не удалось', {
                status: res.status,
                err,
                title,
                duration: endTime - startTime
            }, 'user-action');
            return null;
        }
        const chat = await res.json();
        FrontendLogger.info('Чат создан', { chatId: chat.chat_id });
        return chat;
    } catch (e) {
        FrontendLogger.error('Исключение при создании чата', { error: String(e) });
        return null;
    }
}

async function renameChat(chatId, title) {
    try {
        const res = await fetch(`/api/chats/${chatId}/title`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        if (!res.ok) {
            FrontendLogger.error('Переименование чата не удалось', { status: res.status });
        }
        return res.ok;
    } catch (e) {
        FrontendLogger.error('Исключение при переименовании чата', { error: String(e), chatId });
        return false;
    }
}

async function deleteChat(chatId) {
    try {
        const res = await fetch(`/api/chats/${chatId}`, { method: 'DELETE' });
        if (!res.ok) {
            FrontendLogger.error('Удаление чата не удалось', { status: res.status });
        }
        return res.ok;
    } catch (e) {
        FrontendLogger.error('Исключение при удалении чата', { error: String(e), chatId });
        return false;
    }
}
