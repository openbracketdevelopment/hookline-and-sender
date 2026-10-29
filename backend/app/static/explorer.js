/* global SwaggerUIBundle */
(() => {
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
