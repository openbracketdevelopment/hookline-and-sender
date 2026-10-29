/* global SwaggerUIBundle */
(() => {
  const root = document.documentElement;
  const toggle = document.getElementById("theme-toggle");
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  let savedTheme = null;
  try {
    savedTheme = localStorage.getItem("hookline-theme");
  } catch (error) {
    console.warn("Theme preference storage is unavailable.", error);
  }
  function applyTheme(theme) {
    root.dataset.theme = theme;
    const dark = theme === "dark";
    toggle.textContent = dark ? "Light mode" : "Dark mode";
    toggle.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} mode`);
    toggle.setAttribute("aria-pressed", String(dark));
  }
  applyTheme(["light", "dark"].includes(savedTheme) ? savedTheme : preference.matches ? "dark" : "light");
  toggle.addEventListener("click", () => {
    savedTheme = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(savedTheme);
    try {
      localStorage.setItem("hookline-theme", savedTheme);
    } catch (error) {
      console.warn("Theme preference could not be saved.", error);
    }
  });
  preference.addEventListener("change", (event) => {
    if (!savedTheme) applyTheme(event.matches ? "dark" : "light");
  });
  const status = document.getElementById("explorer-status");
  if (typeof SwaggerUIBundle !== "function") {
    status.textContent = "The API explorer could not load. Check your internet connection and reload. The OpenAPI schema link above remains available.";
    status.setAttribute("role", "alert");
    return;
  }
  SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    displayRequestDuration: true,
    persistAuthorization: false,
    validatorUrl: null,
    docExpansion: "list",
    filter: true,
    tryItOutEnabled: false,
    onComplete: () => { status.hidden = true; },
  });
})();
