(() => {
  const dialog = document.getElementById("authDialog");
  const form = document.getElementById("authForm");
  const usernameInput = document.getElementById("authUsername");
  const passwordInput = document.getElementById("authPassword");
  const authToggle = document.getElementById("authToggle");
  const signedInAs = document.getElementById("signedInAs");
  const authTitle = document.getElementById("authTitle");
  const authDescription = document.getElementById("authDescription");
  const authSubmit = document.getElementById("authSubmit");
  const passwordHint = document.getElementById("passwordHint");
  const authError = document.getElementById("authError");
  let setupRequired = false;

  const showError = (message) => {
    authError.textContent = message;
    authError.hidden = false;
  };

  const setAuthenticated = (username) => {
    const authenticated = Boolean(username);
    document.body.classList.toggle("authenticated", authenticated);
    signedInAs.hidden = !authenticated;
    signedInAs.textContent = authenticated ? `Signed in as ${username}` : "";
    authToggle.textContent = authenticated ? "Log out" : "Log in";
  };

  const setMode = (isSetup) => {
    setupRequired = isSetup;
    authTitle.textContent = isSetup ? "Create your account" : "Sign in to continue";
    authDescription.textContent = isSetup
      ? "Set up the account you will use to access both dashboards."
      : "Use your dashboard account to continue.";
    authSubmit.textContent = isSetup ? "Create account" : "Log in";
    passwordHint.hidden = !isSetup;
    passwordInput.autocomplete = isSetup ? "new-password" : "current-password";
    passwordInput.minLength = isSetup ? 8 : 1;
    authError.hidden = true;
  };

  const openDialog = () => {
    if (!dialog.open) dialog.showModal();
    usernameInput.focus();
  };

  const initializeAuth = async () => {
    try {
      const response = await fetch("/api/auth/status", {cache: "no-store"});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not check sign-in status.");
      setMode(payload.setup_required);
      setAuthenticated(payload.authenticated ? payload.username : "");
      if (!payload.authenticated) openDialog();
    } catch (error) {
      console.error("Could not check dashboard sign-in status.", error);
      showError("Could not check sign-in status. Refresh the page and try again.");
      openDialog();
    }
  };

  authToggle.addEventListener("click", async () => {
    authError.hidden = true;
    if (!document.body.classList.contains("authenticated")) {
      openDialog();
      return;
    }

    authToggle.disabled = true;
    try {
      const response = await fetch("/api/auth/logout", {method: "POST"});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not log out.");
      setAuthenticated("");
      setMode(false);
      passwordInput.value = "";
      openDialog();
    } catch (error) {
      console.error("Could not log out of the dashboard.", error);
      showError(error.message || "Could not log out. Please try again.");
      if (!dialog.open) openDialog();
    } finally {
      authToggle.disabled = false;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    authError.hidden = true;
    authSubmit.disabled = true;
    try {
      const response = await fetch(
        setupRequired ? "/api/auth/setup" : "/api/auth/login",
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            username: usernameInput.value.trim(),
            password: passwordInput.value,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Could not sign in.");
      passwordInput.value = "";
      setAuthenticated(payload.username);
      dialog.close();
    } catch (error) {
      showError(error.message || "Could not sign in. Please try again.");
    } finally {
      authSubmit.disabled = false;
    }
  });

  dialog.addEventListener("cancel", (event) => {
    if (!document.body.classList.contains("authenticated")) event.preventDefault();
  });

  let currentVersion = null;
  let initialized = false;
  let checking = false;
  let pollTimer = null;

  const checkForChanges = async () => {
    if (checking) return;
    checking = true;
    try {
      const response = await fetch("/api/dev/version", {cache: "no-store"});
      if (response.status === 404) {
        if (pollTimer !== null) window.clearInterval(pollTimer);
        return;
      }
      if (!response.ok) throw new Error(`Development reload check failed (${response.status}).`);
      const payload = await response.json();
      if (!payload.enabled) {
        if (pollTimer !== null) window.clearInterval(pollTimer);
        return;
      }
      if (!initialized) {
        currentVersion = payload.version;
        initialized = true;
      } else if (payload.version !== currentVersion) {
        window.location.reload();
      }
    } catch (error) {
      console.warn("Could not check for local dashboard changes; retrying.", error);
    } finally {
      checking = false;
    }
  };

  void initializeAuth();
  void checkForChanges();
  pollTimer = window.setInterval(checkForChanges, 1000);
})();
