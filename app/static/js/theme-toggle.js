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

        // Sidebar collapse (desktop)
        var COLLAPSE_KEY = 'cctv-sidebar-collapsed';
        var collapseBtn = document.getElementById('sidebarCollapseBtn');
        var mainContent = document.querySelector('.main-content');

        function applySidebarCollapse(collapsed) {
            if (collapsed) {
                sidebar.classList.add('collapsed');
                if (mainContent) mainContent.classList.add('sidebar-collapsed');
            } else {
                sidebar.classList.remove('collapsed');
                if (mainContent) mainContent.classList.remove('sidebar-collapsed');
            }
            localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
        }

        if (localStorage.getItem(COLLAPSE_KEY) === '1') {
            applySidebarCollapse(true);
        }

        if (collapseBtn) {
            collapseBtn.addEventListener('click', function () {
                applySidebarCollapse(!sidebar.classList.contains('collapsed'));
            });
        }
    });
})();
