document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-authors-widget]").forEach((widget) => {
    const rows = widget.querySelector("[data-author-rows]");
    const template = widget.querySelector("[data-author-template]");

    widget.querySelector("[data-add-author]").addEventListener("click", () => {
      const row = template.content.firstElementChild.cloneNode(true);
      rows.appendChild(row);
      row.querySelector('input[name$="_name"]').focus();
    });

    rows.addEventListener("click", (event) => {
      const removeButton = event.target.closest("[data-remove-author]");
      if (removeButton) removeButton.closest("[data-author-row]").remove();
    });
  });
});
