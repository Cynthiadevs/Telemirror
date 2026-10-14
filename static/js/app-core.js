/**
 * TeleMirror Pro v2 - Shared Application Core
 * Handles Auth, Responsive Navigation, Modals, Drawer, and Toasts across all dedicated pages.
 */

let CURRENT_USER = null;
let SUBSCRIPTION = null;
let ONBOARDING = null;
let PLANS = [];

// Theme Management (Light / Dark)
function initTheme() {
  const saved = localStorage.getItem('theme') || 'dark';
  applyTheme(saved);
}

function applyTheme(theme) {
  const html = document.documentElement;
  const icon = document.getElementById('themeToggleIcon');
  if (theme === 'light') {
    html.classList.remove('dark');
    if (icon) icon.className = 'fa-solid fa-moon text-slate-700';
    localStorage.setItem('theme', 'light');
  } else {
    html.classList.add('dark');
    if (icon) icon.className = 'fa-solid fa-sun text-amber-400';
    localStorage.setItem('theme', 'dark');
  }
}

function toggleTheme() {
  const isDark = document.documentElement.classList.contains('dark');
  applyTheme(isDark ? 'light' : 'dark');
}

// Auto-run theme initialization
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTheme);
  } else {
    initTheme();
  }
}

// Toast Notifications
function showToast(message, type = 'info') {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'fixed bottom-24 sm:bottom-6 right-4 sm:right-6 z-50 flex flex-col gap-2 pointer-events-none max-w-[90vw]';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `p-3 rounded-2xl shadow-2xl border text-xs font-semibold flex items-center gap-2 pointer-events-auto transition-all animate-in fade-in slide-in-from-bottom duration-200 ${
    type === 'success' ? 'bg-emerald-950/95 border-emerald-500/50 text-emerald-200' :
    type === 'error' ? 'bg-rose-950/95 border-rose-500/50 text-rose-200' :
    'bg-slate-900/95 border-slate-700 text-slate-100'
  }`;
  toast.innerHTML = `<i class="fa-solid ${type === 'success' ? 'fa-circle-check' : type === 'error' ? 'fa-triangle-exclamation' : 'fa-circle-info'} shrink-0 text-sm"></i> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => { toast.remove(); }, 3800);
}

// Drawer Toggler
function toggleMobileDrawer() {
  const drawer = document.getElementById('mobileDrawer');
  if (!drawer) return;
  const content = drawer.querySelector('.drawer-content');
  if (drawer.classList.contains('hidden')) {
    drawer.classList.remove('hidden');
    setTimeout(() => {
      if (content) content.classList.remove('translate-x-full');
    }, 15);
  } else {
    if (content) content.classList.add('translate-x-full');
    setTimeout(() => {
      drawer.classList.add('hidden');
    }, 280);
  }
}

// Close drawer or modal when tapping outer backdrop
document.addEventListener('click', (e) => {
  const drawer = document.getElementById('mobileDrawer');
  if (drawer && !drawer.classList.contains('hidden') && e.target === drawer) {
    toggleMobileDrawer();
  }
  const authModal = document.getElementById('authModal');
  if (authModal && !authModal.classList.contains('hidden') && e.target === authModal) {
    closeAuthModal();
  }
  const checkoutModal = document.getElementById('checkoutModal');
  if (checkoutModal && !checkoutModal.classList.contains('hidden') && e.target === checkoutModal) {
    if (typeof closeCheckoutModal === 'function') closeCheckoutModal();
  }
  const giveawayModal = document.getElementById('giveawayModal');
  if (giveawayModal && !giveawayModal.classList.contains('hidden') && e.target === giveawayModal) {
    if (typeof closeGiveawayModal === 'function') closeGiveawayModal();
  }
});

// Auth Checking & UI Synchronization
async function checkAuthAndSubscription(callback) {
  try {
    const res = await fetch('/api/auth/me');
    const data = await res.json();
    if (data.authenticated) {
      CURRENT_USER = data.user;
      SUBSCRIPTION = data.subscription;
      ONBOARDING = data.onboarding;
      updateHeaderForUser();
    } else {
      CURRENT_USER = null;
      SUBSCRIPTION = null;
      ONBOARDING = null;
      updateHeaderForVisitor();
    }
    highlightCurrentBottomNav();
    if (callback) callback(data);
  } catch (e) {
    console.error('Auth verification error:', e);
  }
}

function updateHeaderForUser() {
  const visitorDesktopNav = document.getElementById('visitorDesktopNav');
  const userDesktopNav = document.getElementById('userDesktopNav');
  const userProfileChip = document.getElementById('userProfileChip');
  const drawerAuthActions = document.getElementById('drawerAuthActions');
  const drawerLogoutAction = document.getElementById('drawerLogoutAction');
  const drawerUserCard = document.getElementById('drawerUserCard');
  const drawerUserLinks = document.getElementById('drawerUserLinks');
  const visitorBottomNav = document.getElementById('visitorBottomNav');
  const userBottomNav = document.getElementById('userBottomNav');

  if (visitorDesktopNav) visitorDesktopNav.classList.add('hidden');
  if (userDesktopNav) userDesktopNav.classList.remove('hidden');
  if (userProfileChip) {
    userProfileChip.classList.remove('hidden');
    userProfileChip.classList.add('flex');
  }

  if (drawerAuthActions) drawerAuthActions.classList.add('hidden');
  if (drawerLogoutAction) drawerLogoutAction.classList.remove('hidden');
  if (drawerUserCard) drawerUserCard.classList.remove('hidden');
  if (drawerUserLinks) drawerUserLinks.classList.remove('hidden');

  if (visitorBottomNav) visitorBottomNav.classList.add('hidden');
  if (userBottomNav) userBottomNav.classList.remove('hidden');

  const nameEl = document.getElementById('userDisplayName');
  const drawerNameEl = document.getElementById('drawerUserName');
  const drawerEmailEl = document.getElementById('drawerUserEmail');
  const avatarInitial = document.getElementById('userAvatarInitial');
  const drawerAvatarInitial = document.getElementById('drawerAvatarInitial');
  const avatarBtn = document.getElementById('userHeaderAvatarBtn');
  const avatarDot = document.getElementById('userAvatarStatusDot');

  if (nameEl) nameEl.innerText = CURRENT_USER.name;
  if (drawerNameEl) drawerNameEl.innerText = CURRENT_USER.name;
  if (drawerEmailEl) drawerEmailEl.innerText = CURRENT_USER.email || '';

  const initial = (CURRENT_USER.name || 'U').trim().charAt(0).toUpperCase();
  if (avatarInitial) avatarInitial.innerText = initial;
  if (drawerAvatarInitial) drawerAvatarInitial.innerText = initial;

  const isSub = Boolean(SUBSCRIPTION && SUBSCRIPTION.is_subscribed);
  const subName = isSub ? (SUBSCRIPTION.plan_name || 'Active') : 'Unsubscribed';
  const subBadge = document.getElementById('userSubBadge');
  const drawerSubBadge = document.getElementById('drawerUserSub');
  if (subBadge) subBadge.innerText = subName;
  if (drawerSubBadge) drawerSubBadge.innerText = subName;

  if (avatarBtn) {
    avatarBtn.classList.remove('hidden');
    avatarBtn.classList.add('flex');
  }
  if (avatarDot) {
    avatarDot.className = isSub
      ? 'absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-slate-950'
      : 'absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-amber-500 border-2 border-slate-950';
  }

  // Impersonation banner
  const impBanner = document.getElementById('impersonationBanner');
  if (impBanner) {
    if (CURRENT_USER.is_impersonating) {
      impBanner.classList.remove('hidden');
      impBanner.classList.add('flex');
      const impName = document.getElementById('impersonatedUserName');
      if (impName) impName.innerText = CURRENT_USER.name;
    } else {
      impBanner.classList.add('hidden');
    }
  }

  // Admin access buttons
  const isAdmin = CURRENT_USER.role === 'admin';
  const adminIds = ['adminPortalBtn', 'drawerAdminBtn', 'userBottomAdminBtn'];
  adminIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      if (isAdmin) el.classList.remove('hidden');
      else el.classList.add('hidden');
    }
  });
}

function updateHeaderForVisitor() {
  const visitorDesktopNav = document.getElementById('visitorDesktopNav');
  const userDesktopNav = document.getElementById('userDesktopNav');
  const userProfileChip = document.getElementById('userProfileChip');
  const drawerAuthActions = document.getElementById('drawerAuthActions');
  const drawerLogoutAction = document.getElementById('drawerLogoutAction');
  const drawerUserCard = document.getElementById('drawerUserCard');
  const drawerUserLinks = document.getElementById('drawerUserLinks');
  const visitorBottomNav = document.getElementById('visitorBottomNav');
  const userBottomNav = document.getElementById('userBottomNav');
  const avatarBtn = document.getElementById('userHeaderAvatarBtn');

  if (visitorDesktopNav) visitorDesktopNav.classList.remove('hidden');
  if (userDesktopNav) userDesktopNav.classList.add('hidden');
  if (userProfileChip) userProfileChip.classList.add('hidden');
  if (avatarBtn) avatarBtn.classList.add('hidden');

  if (drawerAuthActions) drawerAuthActions.classList.remove('hidden');
  if (drawerLogoutAction) drawerLogoutAction.classList.add('hidden');
  if (drawerUserCard) drawerUserCard.classList.add('hidden');
  if (drawerUserLinks) drawerUserLinks.classList.add('hidden');

  if (visitorBottomNav) visitorBottomNav.classList.remove('hidden');
  if (userBottomNav) userBottomNav.classList.add('hidden');

  const impBanner = document.getElementById('impersonationBanner');
  if (impBanner) impBanner.classList.add('hidden');

  const adminIds = ['adminPortalBtn', 'drawerAdminBtn', 'userBottomAdminBtn'];
  adminIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  });
}

// Highlight current page tab in bottom glass dock
function highlightCurrentBottomNav() {
  const path = window.location.pathname.toLowerCase();
  
  // Dock items
  document.querySelectorAll('.dock-item').forEach(item => {
    const href = item.getAttribute('href') || item.dataset.target || '';
    if (href && (path === href.toLowerCase() || (path === '/' && href === '/home'))) {
      item.classList.add('text-cyan-400', 'font-bold', 'active');
      item.classList.remove('text-gray-400', 'text-slate-400');
    } else {
      item.classList.remove('text-cyan-400', 'font-bold', 'active');
      item.classList.add('text-slate-400');
    }
  });
}

// Auth Modals
function openAuthModal(tab = 'login') {
  switchAuthTab(tab);
  const modal = document.getElementById('authModal');
  if (modal) modal.classList.remove('hidden');
}

function closeAuthModal() {
  const modal = document.getElementById('authModal');
  if (modal) modal.classList.add('hidden');
}

function switchAuthTab(tab) {
  const loginForm = document.getElementById('loginForm');
  const registerForm = document.getElementById('registerForm');
  const tabLogin = document.getElementById('authTabLogin');
  const tabRegister = document.getElementById('authTabRegister');

  if (tab === 'login') {
    if (loginForm) loginForm.classList.remove('hidden');
    if (registerForm) registerForm.classList.add('hidden');
    if (tabLogin) tabLogin.className = 'text-sm font-bold text-brand-400 border-b-2 border-brand-500 pb-1';
    if (tabRegister) tabRegister.className = 'text-sm font-bold text-slate-400 hover:text-white pb-1';
  } else {
    if (loginForm) loginForm.classList.add('hidden');
    if (registerForm) registerForm.classList.remove('hidden');
    if (tabRegister) tabRegister.className = 'text-sm font-bold text-brand-400 border-b-2 border-brand-500 pb-1';
    if (tabLogin) tabLogin.className = 'text-sm font-bold text-slate-400 hover:text-white pb-1';
  }
}

async function handleLogin(e) {
  e.preventDefault();
  try {
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (data.success) {
      closeAuthModal();
      showToast('Welcome back!', 'success');
      await checkAuthAndSubscription();
      if (CURRENT_USER && CURRENT_USER.role === 'admin') {
        window.location.href = '/admin';
      } else if (SUBSCRIPTION && SUBSCRIPTION.is_subscribed) {
        window.location.href = '/dashboard';
      } else {
        window.location.href = '/pricing';
      }
    } else {
      showToast(data.error || 'Login failed.', 'error');
    }
  } catch (err) {
    showToast('Network error on login.', 'error');
  }
}

async function handleRegister(e) {
  e.preventDefault();
  try {
    const name = document.getElementById('regName').value;
    const email = document.getElementById('regEmail').value;
    const password = document.getElementById('regPassword').value;
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, email, password })
    });
    const data = await res.json();
    if (data.success) {
      closeAuthModal();
      showToast('Account created successfully!', 'success');
      window.location.href = '/pricing';
    } else {
      showToast(data.error || 'Registration failed.', 'error');
    }
  } catch (err) {
    showToast('Registration network error.', 'error');
  }
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  CURRENT_USER = null;
  SUBSCRIPTION = null;
  ONBOARDING = null;
  window.location.href = '/';
}

async function exitImpersonation() {
  const res = await fetch('/api/auth/exit-impersonation', { method: 'POST' });
  const data = await res.json();
  if (data.success) {
    window.location.href = '/admin';
  }
}
