document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-winner-probability-page]").forEach((page) => {
    page.querySelectorAll("select[data-auto-submit]").forEach((select) => {
      select.addEventListener("change", () => {
        const form = select.closest("form");
        if (form) form.requestSubmit();
      });
    });
  });
});
