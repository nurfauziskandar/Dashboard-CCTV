(function () {
    var STORAGE_KEY = 'cctv-theme';

    function getPreferred() {
        var stored = localStorage.getItem(STORAGE_KEY);
        if (stored) return stored;
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }

    function apply(theme) {
        document.documentElement.setAttribute('data-bs-theme', theme);
        localStorage.setItem(STORAGE_KEY, theme);
    }

    apply(getPreferred());

    document.addEventListener('DOMContentLoaded', function () {
        var toggle = document.getElementById('themeToggle');
        if (toggle) {
            toggle.addEventListener('click', function () {
                var current = document.documentElement.getAttribute('data-bs-theme');
                apply(current === 'dark' ? 'light' : 'dark');
            });
        }

        // Sidebar toggle (mobile)
        var sidebarToggle = document.getElementById('sidebarToggle');
        var sidebar = document.getElementById('sidebar');
        var overlay = document.getElementById('sidebarOverlay');
        var sidebarClose = document.getElementById('sidebarClose');

        function openSidebar() {
            sidebar.classList.add('show');
            overlay.classList.add('show');
        }

        function closeSidebar() {
            sidebar.classList.remove('show');
            overlay.classList.remove('show');
        }

        if (sidebarToggle) sidebarToggle.addEventListener('click', openSidebar);
        if (sidebarClose) sidebarClose.addEventListener('click', closeSidebar);
        if (overlay) overlay.addEventListener('click', closeSidebar);
    });
})();
