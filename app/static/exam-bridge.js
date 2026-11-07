/* Minimal Android bridge for the official exam H5 webview.
 * Preserve the platform's JSBridge implementation and callbacks.
 * Unknown native commands are surfaced; never acknowledge an unimplemented monitor.
 */
(() => {
    window.androidjsbridge = {
        postNotification(name, payload) {
            let data = {};
            try {
                data = JSON.parse(payload || '{}');
            } catch (_) {
                /* record command only */
            }
            void window.courseExamBridge(String(name), data).catch(() => {});
        },
    };
    const ready = () => {
        if (!window.jsBridge || typeof window.jsBridge.setDevice !== 'function') return false;
        window.jsBridge.setDevice('android');
        return true;
    };
    document.addEventListener(
        'DOMContentLoaded',
        () => {
            if (ready()) return;
            let attempts = 0;
            const timer = setInterval(() => {
                if (ready() || ++attempts > 100) clearInterval(timer);
            }, 100);
        },
        { once: true },
    );
})();
